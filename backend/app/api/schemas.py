"""Pydantic request / response models shared by the API and the chat agent.

`RunRequest` is the single validated description of a simulation job.
Both the chat endpoint and the hyper-parameter form produce one.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TrafficLevel = Literal["less", "moderate", "high"]
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class LaneClosure(BaseModel):
    """Close some lanes of every edge whose name contains `road`.

    Attributes:
        road: Road name fragment (matched case-insensitively).
        lanes: Lane indices to close, 0 = kerb lane. Empty closes all lanes.
    """

    road: str = Field(min_length=2, max_length=80)
    lanes: list[int] = Field(default_factory=list, max_length=8)


class RunRequest(BaseModel):
    """One baseline (+ optional scenario) simulation job.

    Attributes:
        place: Place name for the geocoder; never coordinates.
        traffic: Traffic level, or None to derive it from the time of day.
        time: Start time "HH:MM".
        duration_s: Simulated seconds including warm-up.
        warmup_s: Seconds excluded from metrics.
        seed: Fixed random seed.
        dt: Engine time step (s).
        close_roads: Road-name fragments to close fully (scenario).
        close_edges: Exact directed OSM edges "u-v-k" to close (scenario).
        close_lanes: Partial lane closures (scenario).
    """

    place: str = Field(default="Silk Board Junction, Bengaluru", min_length=2, max_length=160)
    traffic: TrafficLevel | None = "moderate"
    time: str = "09:00"
    duration_s: float = Field(default=900.0, ge=120.0, le=3600.0)
    warmup_s: float = Field(default=180.0, ge=0.0, le=900.0)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    dt: float = Field(default=0.5, ge=0.1, le=1.0)
    close_roads: list[str] = Field(default_factory=list, max_length=20)
    close_edges: list[str] = Field(default_factory=list, max_length=200)
    close_lanes: list[LaneClosure] = Field(default_factory=list, max_length=20)

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
        return bool(self.close_roads or self.close_edges or self.close_lanes)


class ChatMessage(BaseModel):
    """One chat turn."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    """Whole conversation so far (the agent is stateless; context travels here).

    Attributes:
        messages: Chat history, oldest first.
        params: Parameters currently in the UI form (optional context).
    """

    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    params: RunRequest | None = None


class ChatReply(BaseModel):
    """Agent answer plus the parameters it extracted.

    Attributes:
        reply: Text to show the user.
        params: Extracted run request, or None if nothing actionable yet.
        ready: True when `params` is complete enough to run.
        agent: Which agent answered: "llm" or "rules" (fallback).
    """

    reply: str
    params: RunRequest | None = None
    ready: bool = False
    agent: str = "rules"
