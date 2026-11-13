"""Overpass download: server fail-over, time budget and XML -> graph (offline)."""

import pytest
import requests

from app.config import get_settings
from app.geo import graph_builder, overpass
from app.geo.overpass import OverpassUnavailable

BBOX = (78.000, 17.000, 78.010, 17.010)  # west, south, east, north
A, B = "https://a.example/api", "https://b.example/api"

# Two ways inside BBOX: a two-way primary 1-2-3 and a one-way residential 2-4;
# node 2 is a traffic signal.
OSM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="test">
  <node id="1" lat="17.002" lon="78.002"/>
  <node id="2" lat="17.002" lon="78.005"><tag k="highway" v="traffic_signals"/></node>
  <node id="3" lat="17.002" lon="78.008"/>
  <node id="4" lat="17.006" lon="78.005"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="primary"/><tag k="lanes" v="4"/></way>
  <way id="11"><nd ref="2"/><nd ref="4"/>
    <tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way>
</osm>
"""


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, status: int, content: bytes = b""):
        self.status_code, self.content = status, content


class _Clock:
    """Fake monotonic clock; `sleep` and slow posts advance it."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
def servers(monkeypatch, data_dir):
    """Configure two fake servers; return (clock, posts log, behaviour dicts)."""
    monkeypatch.setenv("OVERPASS_URLS", f"{A},{B}")
    monkeypatch.setenv("OVERPASS_BUDGET_S", "240")
    get_settings.cache_clear()
    clock, posts = _Clock(), []
    up = {A: True, B: True}
    answers = {A: [_Resp(200, OSM_XML)], B: [_Resp(200, OSM_XML)]}

    def fake_post(url, data, timeout, headers):
        base = url.removesuffix("/interpreter")
        posts.append(base)
        reply = answers[base].pop(0) if answers[base] else _Resp(504)
        if isinstance(reply, Exception):
            clock.sleep(timeout[1])
            raise reply
        return reply

    monkeypatch.setattr(overpass, "reachable", lambda url, connect_s: up[url])
    monkeypatch.setattr(overpass.requests, "post", fake_post)
    yield clock, posts, up, answers
    get_settings.cache_clear()


def _fetch(clock, stages=None):
    return overpass.fetch_xml(BBOX, stages.append if stages is not None else None,
                              clock=clock, sleep=clock.sleep)


def test_unreachable_server_is_skipped_without_posting(servers):
    clock, posts, up, _ = servers
    up[A] = False
    stages = []
    assert _fetch(clock, stages) == OSM_XML
    assert posts == [B]
    assert stages and "b.example" in stages[-1]


def test_busy_server_falls_over_to_the_next(servers):
    clock, posts, _, answers = servers
    answers[A] = [_Resp(504)]
    assert _fetch(clock) == OSM_XML
    assert posts == [A, B]


def test_partial_answer_with_remark_is_rejected(servers):
    clock, posts, _, answers = servers
    answers[A] = [_Resp(200, OSM_XML.replace(b"</osm>", b"<remark>runtime error</remark></osm>"))]
    assert _fetch(clock) == OSM_XML
    assert posts == [A, B]


def test_all_servers_failing_raises_within_budget(servers):
    clock, posts, _, answers = servers
    answers[A] = [requests.Timeout()] * 4
    answers[B] = [_Resp(429)] * 4
    with pytest.raises(OverpassUnavailable) as err:
        _fetch(clock)
    assert clock.t <= get_settings().overpass_budget_s
    assert "a.example: timed out" in str(err.value) and "b.example: HTTP 429" in str(err.value)
    assert len(posts) <= 2 * overpass.PASSES


def test_query_targets_the_box_with_drive_filter():
    q = overpass.build_query(BBOX, 90)
    assert q.startswith("[out:xml][timeout:90];")
    assert "(17.0000000,78.0000000,17.0100000,78.0100000)" in q
    assert '"highway"!~"abandoned' in q and ">;" in q


def test_osm_xml_becomes_the_expected_graph():
    G = graph_builder.graph_from_osm_xml(OSM_XML, BBOX)
    assert sorted(G.nodes) == [1, 2, 3, 4]
    assert sorted((u, v) for u, v, _ in G.edges) == [(1, 2), (2, 1), (2, 3), (2, 4), (3, 2)]
    assert G.edges[1, 2, 0]["lanes"] == "4"
    assert len(graph_builder._signal_points(G)) == 1
