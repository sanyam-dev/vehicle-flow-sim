"""Task-understanding agent: chat history -> reply + validated RunRequest.

One turn:
  1. system prompt (rules, traffic levels, current parameters, known road
     names of the area when its graph is cached) + the whole conversation;
  2. the LLM answers with JSON following `AgentTurn`'s schema;
  3. Pydantic validates it (one retry with the error message if invalid);
  4. the turn is merged into the current parameters, the place is checked
     with the geocoder and road names are matched to the area's roads.

The LLM never produces coordinates: `AgentTurn` has no coordinate fields and
rejects a place that looks like "lat, lon". If the provider is unavailable
or keeps answering invalid JSON, the rule-based agent answers instead.
"""

import json
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from rapidfuzz import fuzz, process, utils

from app.api import chat as rules
from app.api.schemas import ChatMessage, ChatReply, LaneClosure, RunRequest
from app.config import get_settings
from app.llm.provider import LLMError, LLMProvider, get_provider

MAX_ATTEMPTS = 2
FUZZY_CUTOFF = 80
_COORDS = re.compile(r"-?\d{1,3}\.\d+\s*[, ]\s*-?\d{1,3}\.\d+")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

SYSTEM_PROMPT = """You are the task-understanding agent of a road-traffic simulator.
Turn the user's request into simulation parameters and talk in short, plain sentences.

Parameters you fill in (JSON):
- place: a place NAME to look up on OpenStreetMap (junction / road / locality + city),
  e.g. "Silk Board Junction, Bengaluru". Never coordinates. null = keep the current place.
- traffic: "less" (500-700 vehicles/h per main road), "moderate" (1000-1400), "high"
  (about 2000+), "auto" (derive from time of day), or null = keep current.
- time: start time "HH:MM" (24 h) or null = keep. Business hours carry more traffic,
  nights very little.
- duration_min: simulated minutes (2-60) or null = keep.
- close_roads: names of roads to close completely.
- close_lanes: [{"road": name, "lanes": [k, ...]}]; lane 0 = kerb lane, [] = all lanes.
- ready_to_run: true when a place is known and the request is clear.
- reply: 1-3 sentences confirming what will run, or the one question you need answered.

Rules:
- Keep everything already agreed in the conversation. close_roads and close_lanes must
  list ALL closures requested so far ([] if none or if the user removed them).
- When known road names are listed below, use those exact names.
- Ask only when the place is missing or ambiguous; everything else has defaults.
- Never invent coordinates or road names that the user did not mention."""


class AgentTurn(BaseModel):
    """Structured LLM answer for one chat turn (see SYSTEM_PROMPT)."""

    reply: str = Field(min_length=1, max_length=1500)
    place: str | None = Field(default=None, max_length=160)
    traffic: Literal["less", "moderate", "high", "auto"] | None = None
    time: str | None = None
    duration_min: int | None = Field(default=None, ge=2, le=60)
    close_roads: list[str] = Field(default_factory=list, max_length=20)
    close_lanes: list[LaneClosure] = Field(default_factory=list, max_length=20)
    ready_to_run: bool = False

    @field_validator("place")
    @classmethod
    def _no_coordinates(cls, v: str | None) -> str | None:
        """Places are names for the geocoder; coordinates are not allowed."""
        if v is not None and (_COORDS.search(v) or not re.search(r"[A-Za-z]{2}", v)):
            raise ValueError("place must be a place name, not coordinates")
        return v.strip() if v else None

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str | None) -> str | None:
        """24 h "HH:MM" only."""
        if v is not None and not _HHMM.match(v.strip()):
            raise ValueError("time must be HH:MM (24 h)")
        return v.strip() if v else None


@lru_cache(maxsize=16)
def _roads_of_area(area_id: str) -> tuple[str, ...]:
    """Road names of a cached area graph (loaded once per process)."""
    import osmnx as ox

    from app.scenario.modifier import road_names

    path = get_settings().path("graphs", f"{area_id}.graphml")
    return tuple(road_names(ox.load_graphml(path)))


def known_roads(place: str) -> list[str] | None:
    """Road names of `place` if its graph is already cached, else None.

    Raises:
        GeocodeError: if the place cannot be found at all.
    """
    from app.geo.geocoder import GeocodeError, resolve_area

    try:
        area = resolve_area(place)
    except GeocodeError:
        raise
    except Exception:  # geocoder offline: carry on without road names
        return None
    if not get_settings().path("graphs", f"{area.area_id}.graphml").exists():
        return None
    return list(_roads_of_area(area.area_id))


def match_road(name: str, roads: list[str]) -> str | None:
    """Map a road name from the chat to one the simulator knows.

    A case-insensitive substring of a known name is kept as is (the worker
    closes every road containing it); otherwise the best fuzzy match above
    FUZZY_CUTOFF is used. Returns None if nothing matches.
    """
    if any(name.lower() in r.lower() for r in roads):
        return name
    best = process.extractOne(name, roads, scorer=fuzz.WRatio,
                             processor=utils.default_process, score_cutoff=FUZZY_CUTOFF)
    return best[0] if best else None


def _prompt_messages(history: list[ChatMessage], current: RunRequest,
                     roads: list[str] | None) -> list[dict]:
    """System prompt with current parameters and road names, then the history."""
    ctx = {k: v for k, v in current.model_dump().items() if k not in ("seed", "dt")}
    system = f"{SYSTEM_PROMPT}\n\nCurrent parameters: {json.dumps(ctx)}"
    if roads:
        system += "\nKnown roads in this area: " + "; ".join(roads[:150])
    return [{"role": "system", "content": system}] + [m.model_dump() for m in history]


def llm_schema() -> dict:
    """JSON schema sent to the model for structured output.

    Every field is required (null is still allowed where the model may keep a
    value): with optional fields small models emit only `reply` and leave the
    parameters out. `reply` comes last so the model fills the parameters first
    and then summarises them.
    """
    schema = AgentTurn.model_json_schema()
    props = schema["properties"]
    schema["properties"] = {k: props[k] for k in [*[k for k in props if k != "reply"], "reply"]}
    schema["required"] = list(schema["properties"])
    return schema


def ask_llm(provider: LLMProvider, messages: list[dict]) -> AgentTurn:
    """Get a valid AgentTurn, retrying once with the validation error.

    Raises:
        LLMError: if the provider fails or every answer is invalid.
    """
    schema = llm_schema()
    msgs = list(messages)
    for _ in range(MAX_ATTEMPTS):
        raw = provider.complete(msgs, schema)
        try:
            return AgentTurn.model_validate_json(raw)
        except ValidationError as exc:
            errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                               for e in exc.errors())
            msgs += [{"role": "assistant", "content": raw[:2000]},
                     {"role": "user", "content": f"That JSON was invalid ({errors}). "
                                                 "Answer again with valid JSON only."}]
    raise LLMError("the model kept answering invalid JSON")


def merge(turn: AgentTurn, current: RunRequest) -> RunRequest:
    """Apply a turn to the current parameters (null fields keep their value).

    Map edits made in the UI (`close_edges`) are kept; the name-based
    closures come from the turn, which lists all of them.
    """
    p = current.model_dump()
    if turn.place:
        p["place"] = turn.place
    if turn.traffic:
        p["traffic"] = None if turn.traffic == "auto" else turn.traffic
    if turn.time:
        p["time"] = turn.time
    if turn.duration_min:
        p["duration_s"] = 60.0 * turn.duration_min
        p["warmup_s"] = min(p["warmup_s"], p["duration_s"] / 4)
    p["close_roads"] = turn.close_roads
    p["close_lanes"] = [c.model_dump() for c in turn.close_lanes]
    return RunRequest.model_validate(p)


def resolve_roads(req: RunRequest, roads: list[str]) -> tuple[RunRequest, list[str]]:
    """Replace closure road names by known ones; drop and report unknown ones.

    Returns:
        (updated request, names that match no road in the area).
    """
    unknown: list[str] = []

    def fix(name: str) -> str | None:
        hit = match_road(name, roads)
        if hit is None:
            unknown.append(name)
        return hit

    close_roads = [r for r in (fix(n) for n in req.close_roads) if r]
    close_lanes = [c.model_copy(update={"road": r}) for c in req.close_lanes
                   if (r := fix(c.road))]
    return req.model_copy(update={"close_roads": close_roads,
                                  "close_lanes": close_lanes}), unknown


def _fallback(history: list[ChatMessage], why: str) -> ChatReply:
    """Rule-based answer, with a note saying why the LLM was not used."""
    reply = rules.respond(history)
    note = f"\n\n_(LLM agent unavailable: {why}. Used the keyword parser.)_"
    return reply.model_copy(update={"reply": reply.reply + note, "agent": "rules"})


def _check_place(req: RunRequest, roads_before: list[str] | None,
                 place_before: str) -> tuple[list[str] | None, str | None]:
    """Geocode a changed place. Returns (road names or None, error text or None)."""
    from app.geo.geocoder import GeocodeError

    if req.place == place_before:
        return roads_before, None
    try:
        return known_roads(req.place), None
    except GeocodeError:
        return None, (f"I couldn't find **{req.place}** on OpenStreetMap. "
                      "Could you name it differently (e.g. add the city)?")


def respond(history: list[ChatMessage], current: RunRequest | None = None,
            provider: LLMProvider | None = None) -> ChatReply:
    """Answer one chat turn with the LLM agent (rule-based fallback).

    Args:
        history: Whole conversation, oldest first (the agent is stateless).
        current: Parameters currently in the UI form, if any.
        provider: LLM provider; defaults to the one selected in settings.
    Returns:
        ChatReply with the validated RunRequest in `params`.
    """
    current = current or RunRequest()
    try:
        provider = provider or get_provider()
        if provider is None:
            return rules.respond(history).model_copy(update={"agent": "rules"})
        roads, _ = _check_place(current, None, "")
        turn = ask_llm(provider, _prompt_messages(history, current, roads))
        req = merge(turn, current)
    except LLMError as exc:
        return _fallback(history, str(exc))
    roads, place_error = _check_place(req, roads, current.place)
    if place_error:
        return ChatReply(reply=place_error, params=None, ready=False, agent="llm")
    reply, unknown = turn.reply, []
    if roads:
        req, unknown = resolve_roads(req, roads)
    if unknown:
        reply += (f"\n\nI couldn't find {', '.join(f'**{u}**' for u in unknown)} in this area, "
                  "so I left it out. Which road did you mean?")
    return ChatReply(reply=reply, params=req, ready=turn.ready_to_run and not unknown,
                     agent="llm")
