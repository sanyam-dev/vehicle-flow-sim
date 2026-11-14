"""Engine tests on synthetic networks (no API, no OSM, no network access)."""

import pytest

from app.scenario.modifier import close_edges, close_lanes, edges_named, scenario_copy
from app.sim.demand import LEVEL_VPH, entry_rates, main_road_vph
from app.sim.engine import SimConfig
from app.sim.network import SIGNAL, compile_network
from app.sim.runner import prepare_baseline, run_pair
from tests.synthetic import corridor, grid

CFG = SimConfig(duration_s=420.0, warmup_s=60.0)


@pytest.fixture(scope="module")
def G():
    """The 3x3 synthetic grid (built once per module)."""
    return grid()


@pytest.fixture(scope="module")
def baseline(G):
    """Baseline-only result on the grid at moderate traffic, 09:00."""
    return run_pair(G, None, "moderate", 9.0, CFG)


def _edges_between(Gs, a: int, b: int) -> list:
    """Both directed edges of the road a<->b."""
    return [(u, v, k) for u, v, k in Gs.edges(keys=True) if {u, v} == {a, b}]


def test_level_definitions_per_main_road():
    """Main-road rates sit inside the user's ranges, at night and at peak."""
    for hour in (3.0, 9.0):
        assert 500 <= main_road_vph("less", hour) <= 700
        assert 1000 <= main_road_vph("moderate", hour) <= 1400
        assert main_road_vph("high", hour) >= 2000
    assert main_road_vph("normal", 9.0) == main_road_vph("moderate", 9.0)  # alias
    assert LEVEL_VPH["less"][1] < LEVEL_VPH["moderate"][0] < LEVEL_VPH["high"][0]


def test_entry_rates_scale_with_level(G):
    """Every entry spawns more at higher levels; side streets less than main roads."""
    net = compile_network(G)
    less, mod, high = (entry_rates(net, lvl, 9.0) for lvl in ("less", "moderate", "high"))
    assert (less < mod).all() and (mod < high).all()
    main = [i for i, n in enumerate(net.entries) if "primary" in
            [net.edge_highway[e] for e in range(net.n_edges) if net.link_from[e] == n]]
    assert 0 < len(main) < len(net.entries)
    assert mod[main].min() > mod[[i for i in range(len(mod)) if i not in main]].max()


def test_grid_compiles_with_signal_and_entries(G):
    """Every crossing junction is signalised; all 12 stub ends are entries and exits."""
    net = compile_network(G)
    assert (net.node_kind == SIGNAL).sum() == 9
    assert len(net.entries) == 12 and len(net.exits) == 12
    _, _, flow = prepare_baseline(G, "moderate", 9.0, CFG)
    assert flow.shape == (net.n_edges,) and flow.sum() > 0


def test_free_corridor_flows_at_speed():
    """A lone 1.5 km primary road carries traffic near its speed limit."""
    res = run_pair(corridor(), None, "less", 3.0, CFG)
    kpi = res["baseline"]["kpi"]
    assert kpi["trips_completed"] > 0
    assert kpi["mean_speed_kmh"] > 35
    assert kpi["gridlock_removed"] == 0 and kpi["unserved_trips"] == 0


def test_baseline_summary_shape(baseline):
    """Summary carries every metric the UI shows."""
    s = baseline["baseline"]
    assert set(s) >= {"kpi", "series", "travel_time_hist", "junctions",
                      "queue_by_junction", "speed_heatmap"}
    assert s["kpi"]["trips_completed"] > 0
    assert s["kpi"]["unserved_trips"] == 0
    assert any(j["signalised"] for j in s["junctions"])
    assert baseline["scenario"] is None and baseline["comparison"] is None


def test_same_seed_same_result(G, baseline):
    """Fixed seed: a rerun reproduces the KPIs exactly."""
    again = run_pair(G, None, "moderate", 9.0, CFG)
    assert again["baseline"]["kpi"] == baseline["baseline"]["kpi"]


def test_side_road_closure_reroutes_without_losing_trips(G):
    """Closing an inner side road recomputes only affected routes; all trips still served."""
    Gs = scenario_copy(G)
    close_edges(Gs, _edges_between(Gs, 0, 1))  # side road between junctions 0 and 1
    res = run_pair(G, Gs, "moderate", 9.0, CFG)
    assert 0 < res["info"]["routes_recomputed"] < 12 * 11
    assert res["scenario"]["kpi"]["unserved_trips"] == 0
    assert set(res["comparison"]["kpi"]) == set(res["baseline"]["kpi"])


def test_closing_an_entry_road_marks_trips_unserved(G):
    """Regression: a closure that cuts OD pairs off must not crash routing."""
    Gs = scenario_copy(G)
    close_edges(Gs, edges_named(Gs, "Main Street"))  # includes both Main Street stubs
    res = run_pair(G, Gs, "moderate", 9.0, CFG)
    assert res["scenario"]["kpi"]["unserved_trips"] > 0
    assert res["baseline"]["kpi"]["unserved_trips"] == 0


def test_lane_closure_runs_and_keeps_road_open(G):
    """Closing the kerb lane of Centre Avenue keeps every trip served."""
    Gs = scenario_copy(G)
    close_lanes(Gs, edges_named(Gs, "Centre Avenue"), [0])
    res = run_pair(G, Gs, "moderate", 9.0, CFG)
    assert res["scenario"]["kpi"]["unserved_trips"] == 0
    assert res["scenario"]["kpi"]["trips_completed"] > 0


def test_frames_are_recorded(G, tmp_path):
    """Frames are written as chunks plus meta.json for each variant."""
    run_pair(G, None, "less", 9.0, SimConfig(duration_s=150.0, warmup_s=30.0), tmp_path)
    out = tmp_path / "baseline"
    assert (out / "meta.json").exists()
    assert len(list(out.glob("chunk_*.npz"))) == 3  # 150 frames / 60 per chunk


# ---- geocoder: user-typed coordinates ----------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("21.170240, 72.831062", (21.17024, 72.831062)),
    ("around 21.17 72.83 please", (21.17, 72.83)),
    ("-33.8688,151.2093", (-33.8688, 151.2093)),
    ("at 9.30 for 15 min", None),           # one decimal number is not a point
    ("12, 77", None),                       # integers are not coordinates
    ("95.00, 10.00", None),                 # latitude out of range
])
def test_parse_coordinates(text, expected):
    from app.geo.geocoder import parse_coordinates

    got = parse_coordinates(text)
    assert got == (pytest.approx(expected) if expected else None)


def test_area_around_typed_coordinates(data_dir, monkeypatch):
    import httpx

    from app.config import get_settings
    from app.geo.geocoder import resolve_area

    class Fake:
        def __call__(self, url, params, **kw):
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"display_name": "Gaurav Path Road, Sachin, Surat, Gujarat",
                    "address": {"country_code": "in"}}

    monkeypatch.setattr(httpx, "get", Fake())
    a = resolve_area("21.170240, 72.831062")
    assert (a.south + a.north) / 2 == pytest.approx(21.17024)
    assert (a.west + a.east) / 2 == pytest.approx(72.831062)
    half_m = (a.north - a.south) / 2 * 111_320
    assert half_m == pytest.approx(get_settings().default_half_side_m, rel=1e-3)
    assert a.driving_side == "left" and a.name.startswith("Gaurav Path Road, Sachin, Surat")


@pytest.mark.parametrize("level", ["less", "moderate", "high"])
def test_no_vehicle_overlaps_its_leader(G, level):
    """At every traffic level no vehicle ever overlaps the one ahead in its lane,
    and the separation safety net itself is never needed."""
    from app.sim.engine import Engine

    base, schedule, _ = prepare_baseline(G, level, 9.0, CFG)
    eng = Engine(base.net, base.routes, schedule, base.plans, CFG)
    worst = 0.0
    while eng.t < CFG.duration_s - 1e-9:
        eng.step()
        li = eng._index()
        own = li.own_leader()
        has = own >= 0
        gap = li.pos[own[has]] - li.length[own[has]] - eng.ag.view("pos")[has]
        worst = min(worst, float(gap.min()) if has.any() else 0.0)
    assert worst > -0.1
    assert eng.metrics.overlaps == 0 and eng.metrics.overlap_fixes == 0


def test_braking_stays_within_the_emergency_limit(G):
    """No vehicle decelerates harder than the model's limit, and hard braking is rare."""
    from app.sim.engine import Engine
    from app.sim.idm import MAX_DECEL

    base, schedule, _ = prepare_baseline(G, "moderate", 9.0, CFG)
    eng = Engine(base.net, base.routes, schedule, base.plans, CFG)
    steps = hard = 0
    while eng.t < CFG.duration_s - 1e-9:
        v0 = dict(zip(eng.ag.view("vid").tolist(), eng.ag.view("speed").tolist(), strict=True))
        eng.step()
        for vid, v in zip(eng.ag.view("vid").tolist(), eng.ag.view("speed").tolist(),
                          strict=True):
            if vid in v0:
                steps += 1
                hard += (v0[vid] - v) / CFG.dt > MAX_DECEL + 0.5
    assert steps > 10_000
    assert hard / steps < 0.002


def test_lane_change_is_drawn_as_a_drift(G):
    """The drawn lateral position moves at most a fraction of a lane per step."""
    from app.sim.engine import LANE_CHANGE_S, Engine

    base, schedule, _ = prepare_baseline(G, "moderate", 9.0, CFG)
    eng = Engine(base.net, base.routes, schedule, base.plans, CFG)
    changed = 0
    for _ in range(600):
        before = dict(zip(eng.ag.view("vid").tolist(),
                          zip(eng.ag.view("link").tolist(), eng.ag.view("lat").tolist(),
                              strict=True), strict=True))
        eng.step()
        for vid, link, lat in zip(eng.ag.view("vid").tolist(), eng.ag.view("link").tolist(),
                                  eng.ag.view("lat").tolist(), strict=True):
            if vid in before and before[vid][0] == link:
                step = abs(lat - before[vid][1])
                changed += step > 0
                assert step <= CFG.dt / LANE_CHANGE_S + 1e-9
    assert changed > 0
