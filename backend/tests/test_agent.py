"""LLM agent tests with a scripted provider (no Ollama, no network)."""

import json

import httpx
import pytest
from ollama import ResponseError

from app.api.schemas import ChatMessage, RunRequest
from app.config import get_settings
from app.llm import agent
from app.llm.provider import FakeProvider, LLMError, OllamaProvider
from tests.conftest import MISSING_PLACE, TEST_PLACE

HISTORY = [ChatMessage(role="user", content="Evening rush at the test grid, close main st")]


def turn(**kw) -> str:
    """JSON answer of the fake LLM (defaults: a ready, empty turn)."""
    return json.dumps({"reply": "Okay.", "ready_to_run": True, **kw})


@pytest.fixture
def offline(grid_area, monkeypatch):
    """Test Grid registered; any further HTTP call fails loudly."""
    def boom(*a, **kw):
        raise AssertionError("unexpected network call")
    monkeypatch.setattr(httpx, "get", boom)
    return RunRequest(place=TEST_PLACE)


def test_turn_becomes_validated_run_request(offline):
    llm = FakeProvider([turn(traffic="high", time="18:00", duration_min=10,
                             close_roads=["center avenue"],
                             close_lanes=[{"road": "Main Street", "lanes": [0]}])])
    r = agent.respond(HISTORY, offline, llm)
    assert r.agent == "llm" and r.ready
    p = r.params
    assert (p.place, p.traffic, p.time, p.duration_s) == (TEST_PLACE, "high", "18:00", 600.0)
    assert p.warmup_s == 150.0  # shortened to a quarter of the run
    assert p.close_roads == ["Centre Avenue"]  # fuzzy-matched to the area's road
    assert p.close_lanes[0].road == "Main Street" and p.close_lanes[0].lanes == [0]


def test_prompt_carries_context(offline):
    llm = FakeProvider([turn()])
    agent.respond(HISTORY, offline, llm)
    system, *rest = llm.calls[0]
    assert system["role"] == "system"
    assert TEST_PLACE in system["content"]  # current parameters
    assert "Main Street" in system["content"] and "Centre Avenue" in system["content"]
    assert rest == [m.model_dump() for m in HISTORY]


def test_null_fields_keep_current_values_and_ui_edits(offline):
    current = offline.model_copy(update={"traffic": "less", "time": "07:30",
                                         "close_edges": ["1-2-0"]})
    r = agent.respond(HISTORY, current, FakeProvider([turn(traffic=None, time=None)]))
    assert (r.params.traffic, r.params.time, r.params.close_edges) == ("less", "07:30", ["1-2-0"])
    r = agent.respond(HISTORY, current, FakeProvider([turn(traffic="auto")]))
    assert r.params.traffic is None  # derive from time of day


@pytest.mark.parametrize("bad_place", ["12.9170, 77.6230", "12.91 77.62", "1234"])
def test_coordinates_are_rejected_and_retried(offline, bad_place):
    llm = FakeProvider([turn(place=bad_place), turn(place=TEST_PLACE)])
    r = agent.respond(HISTORY, offline, llm)
    assert r.agent == "llm" and r.params.place == TEST_PLACE
    assert len(llm.calls) == 2
    assert "place" in llm.calls[1][-1]["content"]  # retry message names the bad field


def test_invalid_output_twice_falls_back_to_rules(offline):
    llm = FakeProvider(["not json", turn(time="25:99")])
    r = agent.respond(HISTORY, offline, llm)
    assert r.agent == "rules"
    assert "LLM agent unavailable" in r.reply


def test_provider_error_falls_back_to_rules(offline):
    llm = FakeProvider([LLMError("Ollama 402: Add usage credits")])
    r = agent.respond(HISTORY, offline, llm)
    assert r.agent == "rules" and "402" in r.reply


def test_unknown_road_is_dropped_and_asked_about(offline):
    llm = FakeProvider([turn(close_roads=["Hosur Road", "Side Road"])])
    r = agent.respond(HISTORY, offline, llm)
    assert r.params.close_roads == ["Side Road"]
    assert not r.ready and "Hosur Road" in r.reply


def test_unknown_place_is_reported(offline):
    r = agent.respond(HISTORY, offline, FakeProvider([turn(place=MISSING_PLACE)]))
    assert not r.ready and r.params is None
    assert "couldn't find" in r.reply and "latitude, longitude" in r.reply


# ---- missing place, user-typed coordinates -----------------------------------------

POINT = "21.170240, 72.831062"


class _Reverse:
    """httpx.get stand-in answering Nominatim reverse lookups."""

    def __init__(self, payload: dict):
        self.payload, self.calls = payload, []

    def __call__(self, url, params, **kw):
        assert url.endswith("/reverse"), url
        self.calls.append(params)
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.fixture
def reverse_ok(offline, monkeypatch):
    fake = _Reverse({"display_name": "Ring Road, Athwa, Surat, Gujarat, India",
                     "address": {"country_code": "in"}})
    monkeypatch.setattr(httpx, "get", fake)
    return fake


def user(*texts) -> list[ChatMessage]:
    """Conversation alternating user texts with a short assistant answer."""
    out = []
    for t in texts:
        out += [ChatMessage(role="user", content=t), ChatMessage(role="assistant", content="Ok.")]
    return out[:-1]


def test_missing_place_is_asked_for(offline):
    llm = FakeProvider([turn(place=None, ready_to_run=False, reply="Sure.")])
    r = agent.respond(user("simulate heavy traffic"), RunRequest(), llm, place_known=False)
    assert r.params is None and not r.ready
    assert "Which place" in r.reply and "latitude, longitude" in r.reply
    assert '"place": null' in llm.calls[0][0]["content"]  # default place not offered


def test_place_from_message_when_form_is_empty(offline):
    llm = FakeProvider([turn(place=TEST_PLACE)])
    r = agent.respond(user(f"Run simulation for {TEST_PLACE}"), RunRequest(), llm,
                      place_known=False)
    assert r.ready and r.params.place == TEST_PLACE


def test_typed_coordinates_become_the_place(reverse_ok):
    llm = FakeProvider([turn(place=None, ready_to_run=False, reply="Which place?")])
    r = agent.respond(user(f"Simulate around {POINT} at 6pm"), RunRequest(), llm,
                      place_known=False)
    assert r.params.place == POINT and r.ready
    assert "Got it" in r.reply and POINT in r.reply
    assert reverse_ok.calls and reverse_ok.calls[0]["lat"] == "21.170240"


def test_earlier_coordinates_are_kept_until_a_new_place_is_named(reverse_ok):
    r = agent.respond(user(f"use {POINT}", "make it heavy traffic"), RunRequest(),
                      FakeProvider([turn(place=None, traffic="high")]), place_known=False)
    assert (r.params.place, r.params.traffic) == (POINT, "high")
    r = agent.respond(user(f"use {POINT}", f"actually {TEST_PLACE}"), RunRequest(),
                      FakeProvider([turn(place=TEST_PLACE)]), place_known=False)
    assert r.params.place == TEST_PLACE


def test_coordinates_in_the_sea_are_questioned(offline, monkeypatch):
    monkeypatch.setattr(httpx, "get", _Reverse({"error": "Unable to geocode"}))
    r = agent.respond(user("try 10.000000, 65.000000"), RunRequest(),
                      FakeProvider([turn(place=None)]), place_known=False)
    assert r.params is None and "nothing on OpenStreetMap" in r.reply


def test_rules_agent_reads_typed_coordinates(offline):
    from app.api import chat as rules

    r = rules.respond(user(f"heavy traffic at {POINT}"))
    assert r.params.place == POINT and r.params.traffic == "high"


def test_rules_provider_setting(offline, monkeypatch):
    monkeypatch.setattr(agent, "get_provider", lambda: None)
    assert agent.respond(HISTORY, offline).agent == "rules"


def test_chat_endpoint_uses_llm_agent(client, monkeypatch):
    llm = FakeProvider([turn(traffic="moderate", reply="Simulating the test grid.")])
    monkeypatch.setattr(agent, "get_provider", lambda: llm)
    body = {"messages": [m.model_dump() for m in HISTORY],
            "params": RunRequest(place=TEST_PLACE).model_dump()}
    r = client.post("/api/chat", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["agent"] == "llm" and j["ready"] and j["reply"] == "Simulating the test grid."
    assert j["params"]["traffic"] == "moderate"


def test_chat_with_empty_form_place_is_not_rejected(client, monkeypatch):
    """Regression: an empty Place box made /api/chat answer 422."""
    llm = FakeProvider([turn(place=TEST_PLACE)])
    monkeypatch.setattr(agent, "get_provider", lambda: llm)
    form = {**RunRequest(place=TEST_PLACE).model_dump(), "place": "", "time": "9am"}
    r = client.post("/api/chat", json={"messages": [m.model_dump() for m in HISTORY],
                                       "params": form})
    assert r.status_code == 200, r.text
    assert r.json()["params"]["place"] == TEST_PLACE
    assert '"place": null' in llm.calls[0][0]["content"]
    assert '"time": "09:00"' in llm.calls[0][0]["content"]  # invalid field -> default


# ---- Ollama provider (client calls stubbed) --------------------------------------

class _Msg:
    content = '{"reply": "hi"}'


class _Resp:
    message = _Msg()


def test_ollama_provider_returns_content_and_sends_schema(monkeypatch):
    prov = OllamaProvider("http://ollama.test", "qwen3:8b", think=False)
    seen = {}

    def chat(**kw):
        seen.update(kw)
        return _Resp()
    monkeypatch.setattr(prov.client, "chat", chat)
    assert prov.complete([{"role": "user", "content": "x"}], {"type": "object"}) == _Msg.content
    assert seen["model"] == "qwen3:8b" and seen["format"] == {"type": "object"}
    assert seen["think"] is False


@pytest.mark.parametrize("exc", [ResponseError("Add usage credits", 402),
                                 httpx.ConnectError("down")])
def test_ollama_provider_maps_failures_to_llm_error(monkeypatch, exc):
    prov = OllamaProvider("http://ollama.test", "m")

    def chat(**kw):
        raise exc
    monkeypatch.setattr(prov.client, "chat", chat)
    with pytest.raises(LLMError):
        prov.complete([], {})


# ---- Authentication to the Ollama server --------------------------------------

def test_auth_headers_per_mode(monkeypatch):
    from app.llm import gcp_auth
    from app.llm.provider import _auth_headers

    calls = []

    class _Tok:
        text = "tok-1\n"

        def raise_for_status(self):
            pass

    def fake_get(url, params, headers, timeout):
        calls.append(params["audience"])
        assert headers == {"Metadata-Flavor": "Google"}
        return _Tok()

    monkeypatch.setattr(gcp_auth.httpx, "get", fake_get)
    monkeypatch.setattr(gcp_auth, "_cache", {})
    host = "https://ollama-1.run.app"
    assert _auth_headers("none", host, "") == {}
    assert _auth_headers("key", host, "k") == {"Authorization": "Bearer k"}
    assert _auth_headers("gcp", host, "") == {"Authorization": "Bearer tok-1"}
    assert _auth_headers("gcp", host, "") == {"Authorization": "Bearer tok-1"}
    assert calls == [host]  # second call served from the cache
    with pytest.raises(LLMError):
        _auth_headers("key", host, "")
    with pytest.raises(LLMError):
        _auth_headers("magic", host, "")


def test_gcp_token_refreshes_before_expiry(monkeypatch):
    from app.llm import gcp_auth

    tokens = iter(["a", "b"])

    class _Tok:
        def __init__(self):
            self.text = next(tokens)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(gcp_auth.httpx, "get", lambda *a, **kw: _Tok())
    monkeypatch.setattr(gcp_auth, "_cache", {})
    assert gcp_auth.id_token("aud", now=0) == "a"
    assert gcp_auth.id_token("aud", now=3000) == "a"
    assert gcp_auth.id_token("aud", now=3301) == "b"


def test_gcp_auth_off_cloud_falls_back_to_rules(grid_area, monkeypatch):
    from app.llm import gcp_auth

    def down(*a, **kw):
        raise httpx.ConnectError("no metadata server")

    monkeypatch.setattr(gcp_auth.httpx, "get", down)
    monkeypatch.setattr(gcp_auth, "_cache", {})
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_AUTH", "gcp")
    get_settings.cache_clear()
    try:
        reply = agent.respond([ChatMessage(role="user", content="simulate Test Grid")])
    finally:
        get_settings.cache_clear()
    assert reply.agent == "rules" and "Google ID token" in reply.reply


def test_llm_schema_requires_every_field_reply_last():
    schema = agent.llm_schema()
    assert schema["required"] == list(schema["properties"])
    assert list(schema["properties"])[-1] == "reply"
    assert set(schema["properties"]) == set(agent.AgentTurn.model_fields)


# ---- Warm-up before the first chat --------------------------------------------

def test_claim_warm_debounces(monkeypatch):
    monkeypatch.setattr(agent, "_last_warm", float("-inf"))
    assert agent.claim_warm(now=1000.0)
    assert not agent.claim_warm(now=1000.0 + agent.WARM_INTERVAL_S - 1)
    assert agent.claim_warm(now=1000.0 + agent.WARM_INTERVAL_S)


def test_warm_sends_a_full_agent_prompt():
    llm = FakeProvider(['{"reply": "hi"}'])
    assert agent.warm(llm)
    (messages,) = llm.calls
    assert messages[0]["role"] == "system" and "traffic simulator" in messages[0]["content"]


def test_warm_ignores_failures():
    assert not agent.warm(FakeProvider([LLMError("402")]))


def test_unexpected_agent_error_becomes_a_question(client, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.api.routes.respond", broken)
    r = client.post("/api/chat", json={"messages": [m.model_dump() for m in HISTORY]})
    assert r.status_code == 200
    assert r.json()["agent"] == "error" and "latitude, longitude" in r.json()["reply"]
