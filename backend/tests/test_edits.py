"""Scenario edits: signals everywhere, map editor, junction editor, and proof
that the simulated vehicles honour each edit (synthetic networks, offline)."""

import numpy as np
import pytest

from app.api.schemas import RunRequest
from app.scenario.builder import build_graphs, edit_usage
from app.scenario.edits import EditError
from app.sim.engine import Engine, SimConfig
from app.sim.movements import ROUNDABOUT_SPEED_MS
from app.sim.network import PRIORITY, ROUNDABOUT, SIGNAL, compile_network
from app.sim.ring import ring_state
from app.sim.runner import od_rates, prepare_baseline, prepare_scenario, run_pair
from app.sim.signals import RED, build_signal_plans, movement_states, phase_timing, red_time
from tests.synthetic import GRID_STEP_M, grid, latlon, merge

CFG = SimConfig(duration_s=600.0, warmup_s=60.0)
# Mid-points of the west and east stubs of Main Street (row 1 of the grid).
WEST, EAST = latlon(-100.0, GRID_STEP_M), latlon(2 * GRID_STEP_M + 100.0, GRID_STEP_M)
CENTRE = "11"


def req(**edits) -> RunRequest:
    """RunRequest on the test grid with the given scenario edits."""
    return RunRequest.model_validate({"place": "Test Grid", **edits})


def run(G, request: RunRequest, level: str = "moderate") -> tuple[dict, list, object]:
    """Run baseline + scenario; returns (result, edit usage, scenario network)."""
    G_base, Gs, manifest = build_graphs(G, request)
    result = run_pair(G_base, Gs, level, 9.0, CFG)
    net = compile_network(Gs, compile_network(G_base))
    return result, edit_usage(manifest, net, result["scenario"]), net


@pytest.fixture(scope="module")
def G():
    """The 3x3 synthetic grid."""
    return grid()


def test_crossing_junctions_get_a_signal_but_merges_do_not(G):
    """Default control: a light where paths cross, none at a ramp merge."""
    net = compile_network(G)
    junction = np.flatnonzero(np.isin(net.node_kind, (PRIORITY, SIGNAL, ROUNDABOUT)))
    assert len(junction) == 9 and (net.node_kind[junction] == SIGNAL).all()
    m = compile_network(merge())
    assert SIGNAL not in m.node_kind.tolist()
    assert PRIORITY in m.node_kind.tolist()


@pytest.mark.parametrize(("phases", "stop_s"), [(2, 45.0), (3, 45.0), (2, 20.0), (4, 60.0)])
def test_red_time_equals_the_stop_duration(phases, stop_s):
    """Fixed red, equal greens: every approach waits exactly `stop_s`."""
    assert red_time(phases, stop_s) == pytest.approx(stop_s)


def test_signal_shows_red_for_the_stop_duration(G):
    """In the running plan a movement is red/amber-free for exactly the red time."""
    net = compile_network(G)
    plans = build_signal_plans(net, np.zeros(net.n_edges), stop_s=60.0)
    assert len(plans.nodes) == 9
    m = int(np.flatnonzero(plans.mv_ctrl == 0)[0])
    cycle = float(plans.cycle[0])
    states = [(movement_states(plans, t)[0][m],) for t in np.arange(0.0, cycle, 0.25)]
    assert [s[0] for s in states].count(RED) * 0.25 == pytest.approx(red_time(4, 60.0), abs=0.5)
    assert red_time(4, 60.0) == pytest.approx(60.0)
    assert red_time(4, 20.0) == pytest.approx(47.0)  # 10 s minimum green sets a floor


def test_split_keeps_edge_ids_and_junction_labels(G):
    """A flyover between two mid-road points: baseline ids and labels are unchanged."""
    G_base, Gs, manifest = build_graphs(G, req(map_edits=[
        {"kind": "flyover", "a": WEST, "b": EAST, "lanes": 2}]))
    base = compile_network(G_base)
    scen = compile_network(Gs, base)
    assert len(G_base.nodes) == len(G.nodes) + 2  # one split node per point
    assert scen.edge_uvk[:base.n_edges] == base.edge_uvk
    assert scen.n_edges == base.n_edges + 2 and scen.edge_added[base.n_edges:].all()
    assert (scen.link_grade[base.n_edges:scen.n_edges] == 1).all()  # bridge
    plain = compile_network(G)
    old = dict(zip(plain.node_id.tolist(), plain.node_label, strict=True))
    new = dict(zip(scen.node_id.tolist(), scen.node_label, strict=True))
    assert all(new[n] == label for n, label in old.items() if label[0] == "J")
    assert len(manifest) == 1 and len(manifest[0]["edges"]) == 2


def test_flyover_never_conflicts_with_roads_it_crosses(G):
    """The new road only touches the network at its two ends."""
    G_base, Gs, _ = build_graphs(G, req(map_edits=[
        {"kind": "flyover", "a": WEST, "b": EAST}]))
    base = compile_network(G_base)
    net = compile_network(Gs, base)
    added = set(range(base.n_edges, net.n_edges))
    ends = {int(net.link_from[e]) for e in added} | {int(net.link_to[e]) for e in added}
    touching = [m for m in range(net.n_movements)
                if net.mv_in[m] in added or net.mv_out[m] in added]
    assert touching and {int(net.mv_node[m]) for m in touching} <= ends
    assert all(net.node_kind[n] != SIGNAL for n in ends)  # ramp ends merge, no light


def test_vehicles_use_the_new_flyover(G):
    """Honour: a flyover that skips three signals attracts traffic and saves time."""
    result, usage, _ = run(G, req(map_edits=[{"kind": "flyover", "a": WEST, "b": EAST}]))
    assert usage[0]["kind"] == "flyover" and usage[0]["vehicles"] > 20
    kpi = result["comparison"]["kpi"]
    assert kpi["mean_travel_time_s"]["scenario"] < kpi["mean_travel_time_s"]["baseline"]
    assert kpi["overlaps"]["scenario"] == 0


def test_underpass_is_marked_and_used(G):
    """Honour: an underpass carries traffic and is graded as one."""
    _, usage, net = run(G, req(map_edits=[{"kind": "underpass", "a": WEST, "b": EAST,
                                           "lanes": 1}]))
    assert (net.link_grade[:net.n_edges][net.edge_added] == 2).all()
    assert usage[0]["vehicles"] > 0


def test_vehicles_use_the_extra_lane(G):
    """Honour: the widened road has one more lane and vehicles drive in it."""
    request = req(map_edits=[{"kind": "add_lane", "a": WEST, "b": EAST}])
    G_base, Gs, _ = build_graphs(G, request)
    base = compile_network(G_base)
    net = compile_network(Gs, base)
    wider = np.flatnonzero(net.link_lanes[:net.n_edges] > base.link_lanes[:base.n_edges])
    assert len(wider) >= 8 and (net.link_lanes[wider] == 3).all()
    result, usage, _ = run(G, request, "high")
    assert usage[0]["kind"] == "add_lane" and usage[0]["vehicles"] > 0
    assert sum(result["scenario"]["usage"]["lane_seconds"][e][2] for e in wider) > 0


def test_no_vehicle_enters_a_closed_road_or_lane(G):
    """Honour: closed roads see no entries, closed lanes no vehicle-seconds."""
    result, usage, net = run(G, req(close_roads=["Centre Avenue"],
                                    close_lanes=[{"road": "Main Street", "lanes": [0]}]))
    by_kind = {u["kind"]: u for u in usage}
    assert by_kind["close_road"]["edges"] > 0 and by_kind["close_road"]["vehicles"] == 0
    assert result["scenario"]["usage"]["closed_lane_seconds"] == 0
    closed = np.flatnonzero(net.edge_closed)
    assert sum(result["scenario"]["usage"]["edge_entries"][e] for e in closed) == 0


def test_no_vehicle_enters_a_junction_on_red(G):
    """Honour: with a signal at every junction nobody crosses a stop line on red."""
    result = run_pair(G, None, "moderate", 9.0, CFG)
    assert result["baseline"]["usage"]["red_entries"] == 0
    assert result["baseline"]["kpi"]["trips_completed"] > 0


def test_junction_signal_edit_uses_its_own_stop_duration(G):
    """A junction edited to a signal with its own stop duration cycles differently."""
    _, Gs, _ = build_graphs(G, req(junction_edits=[
        {"node": CENTRE, "control": "signal", "stop_s": 90.0}]))
    net = compile_network(Gs)
    plans = build_signal_plans(net, np.zeros(net.n_edges), stop_s=45.0)
    centre = int(np.flatnonzero(net.node_id == int(CENTRE))[0])
    by_node = dict(zip(plans.nodes.tolist(), plans.cycle.tolist(), strict=True))
    assert by_node[centre] == pytest.approx(phase_timing(4, 90.0)[0])
    assert red_time(4, 90.0) == pytest.approx(90.0)
    others = [c for n, c in by_node.items() if n != centre]
    assert others and all(c < by_node[centre] for c in others)


def test_circle_has_no_signal_and_vehicles_circulate_slowly(G):
    """Honour: a circle has no lights, traffic passes it slowly and never overlaps."""
    request = req(junction_edits=[{"node": CENTRE, "control": "circle"}])
    G_base, Gs, _ = build_graphs(G, request)
    base, schedule, flow = prepare_baseline(G_base, "moderate", 9.0, CFG)
    scen = prepare_scenario(Gs, base, flow, CFG, od_rates(schedule, CFG.duration_s))
    net = scen.net
    centre = int(np.flatnonzero(net.node_id == int(CENTRE))[0])
    assert net.node_kind[centre] == ROUNDABOUT and centre not in scen.plans.nodes.tolist()
    assert net.node_label[centre] == base.net.node_label[centre]
    eng = Engine(net, scen.routes, schedule, scen.plans, CFG)
    ring = np.flatnonzero(net.mv_node == centre) + net.n_edges
    seen, v_max, tightest = 0, 0.0, np.inf
    while eng.t < CFG.duration_s - 1e-9:
        eng.step()
        inside = np.flatnonzero(np.isin(eng.ag.view("link"), ring))
        seen += len(inside)
        if len(inside):
            v_max = max(v_max, float((eng.ag.speed[inside] / eng.ag.speed_factor[inside]).max()))
        st = ring_state(eng, eng._next_links())
        on = np.flatnonzero(st.inside & (st.to_join <= 0))  # vehicles on the ring itself
        if len(on) > 1:
            order = on[np.argsort(st.s[on])]
            ahead = np.roll(order, -1)
            length = eng.params["length"][eng.ag.vtype[st.rows[ahead]]]
            gaps = (st.s[ahead] - st.s[order]) % st.circ[order] - length
            tightest = min(tightest, float(gaps.min()))
    assert seen > 200
    assert v_max <= ROUNDABOUT_SPEED_MS + 2.0  # entry overshoot only; the ring is 20 km/h
    assert tightest > -0.5  # single file: nobody overlaps the vehicle ahead on the ring
    assert eng.metrics.overlaps == 0


def test_bad_edits_are_reported_plainly(G):
    """Edits that cannot be applied raise EditError with a message for the user."""
    far = latlon(-5000.0, -5000.0)
    with pytest.raises(EditError, match="not on a road"):
        build_graphs(G, req(map_edits=[{"kind": "flyover", "a": far, "b": EAST}]))
    with pytest.raises(EditError, match="does not exist"):
        build_graphs(G, req(junction_edits=[{"node": "99999", "control": "circle"}]))
    near = latlon(-80.0, GRID_STEP_M)
    with pytest.raises(EditError, match="50 m"):
        build_graphs(G, req(map_edits=[{"kind": "underpass", "a": WEST, "b": near}]))
