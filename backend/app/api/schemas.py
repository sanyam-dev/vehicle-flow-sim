"""Pydantic request / response models shared by the API and the chat agent.

`RunRequest` is the single validated description of a simulation job.
Both the chat endpoint and the hyper-parameter form produce one.
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

TrafficLevel = Literal["less", "moderate", "high"]
EdgeId = Annotated[str, Field(pattern=r"^\d{1,12}-\d{1,12}-\d{1,6}$")]
SCENARIO_FIELDS = ("close_roads", "close_edges", "close_lanes", "map_edits", "junction_edits")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class LaneClosure(BaseModel):
    """Close some lanes of every edge whose name contains `road`.

    Attributes:
        road: Road name fragment (matched case-insensitively).
        lanes: Lane indices to close, 0 = kerb lane. Empty closes all lanes.
    """

    road: str = Field(min_length=2, max_length=80)
    lanes: list[int] = Field(default_factory=list, max_length=8)


class MapPoint(BaseModel):
    """A point the user clicked on the map (never produced by the LLM).

    Attributes:
        lat, lon: The point, already snapped to a road by the map.
        grade: The level the user chose where roads are stacked ("road",
            "bridge" for a flyover, "underpass"); None prefers the ground road.
        edge: The chosen road's directed edge "u-v-k" in the network the user
            clicked on; used when it still exists, else `grade` decides.
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    grade: Literal["road", "bridge", "underpass"] | None = None
    edge: EdgeId | None = None


class MapEdit(BaseModel):
    """Map-editor edit between two clicked points.

    Attributes:
        kind: "flyover" / "underpass" (a new grade-separated road between the
            points) or "add_lane" (one more lane along the roads joining them).
        a, b: The two points; each snaps to the nearest road.
        lanes: Lanes per direction of a new flyover / underpass.
        both_directions: For "add_lane": also widen the way back.
    """

    kind: Literal["flyover", "underpass", "add_lane"]
    a: MapPoint
    b: MapPoint
    lanes: int = Field(default=2, ge=1, le=4)
    both_directions: bool = True

    @model_validator(mode="after")
    def _distinct_points(self):
        """The two points must differ."""
        if abs(self.a.lat - self.b.lat) < 1e-6 and abs(self.a.lon - self.b.lon) < 1e-6:
            raise ValueError("the two points of a map edit must be different")
        return self


class JunctionEdit(BaseModel):
    """Change how one junction is controlled.

    Attributes:
        node: Junction node id (`osm` of the junction in the network GeoJSON).
        control: "signal" (fixed-time traffic light) or "circle" (roundabout).
        stop_s: Red time per approach for a signal; None uses `signal_stop_s`.
    """

    node: str = Field(pattern=r"^\d{1,12}$")
    control: Literal["signal", "circle"]
    stop_s: float | None = Field(default=None, ge=10.0, le=180.0)


class RunRequest(BaseModel):
    """One baseline (+ optional scenario) simulation job.

    Attributes:
        place: Place name for the geocoder, or "lat, lon" typed by the user
            (never coordinates produced by the LLM).
        traffic: Traffic level, or None to derive it from the time of day.
        time: Start time "HH:MM".
        duration_s: Simulated seconds including warm-up.
        warmup_s: Seconds excluded from metrics.
        seed: Fixed random seed.
        dt: Engine time step (s).
        signal_stop_s: Red time per approach at every traffic signal (s).
        close_roads: Road-name fragments to close fully (scenario).
        close_edges: Exact directed graph edges "u-v-k" to close (scenario).
        close_lanes: Partial lane closures (scenario).
        map_edits: Flyovers, underpasses and extra lanes drawn on the map (scenario).
        junction_edits: Junctions turned into a signal or a circle (scenario).
    """

    place: str = Field(default="Silk Board Junction, Bengaluru", min_length=2, max_length=160)
    traffic: TrafficLevel | None = "moderate"
    time: str = "09:00"
    duration_s: float = Field(default=900.0, ge=120.0, le=3600.0)
    warmup_s: float = Field(default=180.0, ge=0.0, le=900.0)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    dt: float = Field(default=0.5, ge=0.1, le=1.0)
    signal_stop_s: float = Field(default=45.0, ge=10.0, le=180.0)
    close_roads: list[str] = Field(default_factory=list, max_length=20)
    close_edges: list[EdgeId] = Field(default_factory=list, max_length=200)
    close_lanes: list[LaneClosure] = Field(default_factory=list, max_length=20)
    map_edits: list[MapEdit] = Field(default_factory=list, max_length=10)
    junction_edits: list[JunctionEdit] = Field(default_factory=list, max_length=50)

    @field_validator("time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        """Accept only 24 h "HH:MM"."""
        if not _HHMM.match(v.strip()):
            raise ValueError("time must be HH:MM (24 h)")
        return v.strip()

    @property
    def hour(self) -> float:
        """Start time as fractional hours."""
        hh, mm = (int(x) for x in self.time.split(":"))
        return hh + mm / 60

    @property
    def has_scenario(self) -> bool:
        """True when any graph edit is requested."""
        return bool(self.close_roads or self.close_edges or self.close_lanes
                    or self.map_edits or self.junction_edits)


class ChatMessage(BaseModel):
    """One chat turn."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    """Whole conversation so far (the agent is stateless; context travels here).

    Attributes:
        messages: Chat history, oldest first.
        params: Parameters currently in the UI form (optional context).
        form_has_place: False when the form's Place box was empty or invalid
            (set from the raw request; the agent then asks for a place
            instead of using the default one).
    """

    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    params: RunRequest | None = None
    form_has_place: bool = True

    @model_validator(mode="before")
    @classmethod
    def _note_form_place(cls, data):
        """Record whether the raw form carried a usable place."""
        if isinstance(data, dict) and isinstance(p := data.get("params"), dict):
            place = p.get("place")
            data = {**data, "form_has_place": isinstance(place, str)
                    and 2 <= len(place.strip()) <= 160}
        return data

    @field_validator("params", mode="wrap")
    @classmethod
    def _lenient_params(cls, v, handler):
        """Form values are only context: drop invalid fields instead of failing.

        E.g. an empty Place box must not block the chat; the dropped field
        falls back to its default and the agent fills it from the conversation.
        """
        try:
            return handler(v)
        except ValidationError as exc:
            if not isinstance(v, dict):
                return None
            bad = {e["loc"][0] for e in exc.errors() if e["loc"]}
            try:
                return handler({k: x for k, x in v.items() if k not in bad})
            except ValidationError:
                return None


class ChatReply(BaseModel):
    """Agent answer plus the parameters it extracted.

    Attributes:
        reply: Text to show the user.
        params: Extracted run request, or None if nothing actionable yet.
        ready: True when `params` is complete enough to run.
        agent: Which agent answered: "llm", "rules" (fallback) or "error"
            (unexpected failure; the reply asks the user to rephrase).
    """

    reply: str
    params: RunRequest | None = None
    ready: bool = False
    agent: str = "rules"
