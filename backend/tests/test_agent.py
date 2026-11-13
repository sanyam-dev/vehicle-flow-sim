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
    assert "couldn't find" in r.reply


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
