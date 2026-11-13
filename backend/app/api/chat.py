"""Interim task-understanding agent (rule based).

Stands in for the Ollama-backed LLM agent (plan phase 4) so the chat UI
works end to end in v1. It reads the whole conversation (context lives
in the request), extracts place / traffic level / time / closures with
regular expressions, and returns a Pydantic-validated RunRequest. Like
the LLM version it never produces coordinates: the place name goes to
the geocoder inside the worker.
"""

import re

from app.api.schemas import ChatMessage, ChatReply, LaneClosure, RunRequest

_LEVELS = {"less": ("light", "low", "less", "night", "empty"),
           "moderate": ("moderate", "normal", "medium", "average"),
           "high": ("heavy", "high", "rush", "peak", "jam", "busy")}
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_PLACE = re.compile(r"\b(?:at|in|near|around)\s+([A-Z][\w .,'-]*?)"
                    r"(?=\s+(?:at|in|during|for|with|and|now|between|around)\b|[.!?]|$)")
_CLOSE = re.compile(r"\bclos(?:e|ing|ure of)\s+(?:the\s+)?"
                    r"(?:(lane|lanes)\s+([\d, ]+)\s+(?:of|on)\s+)?"
                    r"([A-Z][\w .'-]+?)(?=\s+(?:at|in|near|during|for|and)\b|[.,!?]|$)", re.I)


def _level(text: str) -> str | None:
    """Traffic level keyword in `text`, if any."""
    low = text.lower()
    for level, words in _LEVELS.items():
        if any(re.search(rf"\b{w}", low) for w in words):
            return level
    return None


def _time(text: str) -> str | None:
    """First plausible clock time ("9am", "18:30") in `text` as HH:MM."""
    for m in _TIME.finditer(text):
        hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if not (m.group(2) or ap):
            continue  # a bare number is not a time
        hh = hh % 12 + (12 if ap == "pm" else 0) if ap else hh
        if hh < 24 and mm < 60:
            return f"{hh:02d}:{mm:02d}"
    return None


def _apply(params: dict, text: str) -> None:
    """Merge whatever one user message specifies into `params` (in place)."""
    if (lvl := _level(text)) is not None:
        params["traffic"] = lvl
    if (t := _time(text)) is not None:
        params["time"] = t
    for m in _CLOSE.finditer(text):
        road = m.group(3).strip()
        if m.group(1):
            lanes = [int(x) for x in re.findall(r"\d+", m.group(2))]
            params.setdefault("close_lanes", []).append(LaneClosure(road=road, lanes=lanes))
        else:
            params.setdefault("close_roads", []).append(road)
    if (p := _PLACE.search(text)) is not None:
        params["place"] = p.group(1).strip()


def _summary(req: RunRequest) -> str:
    """One-line human summary of a request."""
    edits = [f"close {r}" for r in req.close_roads]
    edits += [f"close lane(s) {c.lanes or 'all'} of {c.road}" for c in req.close_lanes]
    scen = f" Scenario: {'; '.join(edits)}." if edits else " Baseline only (no road edits)."
    return (f"Simulating **{req.place}** at {req.time}, traffic "
            f"**{req.traffic or 'from time of day'}**, {int(req.duration_s // 60)} min.{scen}")


def respond(messages: list[ChatMessage]) -> ChatReply:
    """Answer the conversation and extract run parameters.

    Args:
        messages: Full chat history, oldest first.
    Returns:
        ChatReply; `ready` is True once a place is known.
    """
    params: dict = {}
    for m in messages:
        if m.role == "user":
            _apply(params, m.content)
    if "place" not in params:
        return ChatReply(reply="Which area should I simulate? For example: "
                               "*rush hour at Silk Board Junction, Bengaluru*.")
    req = RunRequest(**params)
    return ChatReply(reply=_summary(req) + " Press **Run** to start.", params=req, ready=True)
