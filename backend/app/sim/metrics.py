"""Metric accumulators and the baseline vs scenario comparison.

Collected during a run (everything before `warmup_s` is ignored):

* Travel time per completed trip (from scheduled departure, so entry
  queueing counts) and delay = travel time - free-flow time.
* Throughput: completed trips per minute (network) and vehicles served
  per junction.
* Queue length: vehicles slower than QUEUE_SPEED_MS within QUEUE_RANGE_M
  of a junction's stop lines, sampled every second, per junction.
* Delay per junction: time spent on the approach edge minus its
  free-flow time, recorded when a vehicle enters the junction.
* Speed heatmap: mean speed per road edge per minute.
"""

import numpy as np

from app.sim.network import JUNCTION_KINDS, ROUNDABOUT, SIGNAL

QUEUE_SPEED_MS = 2.0
QUEUE_RANGE_M = 100.0
TT_BIN_S = 30.0
TT_MAX_S = 1200.0
BIN_S = 60.0
MAX_LANES = 8
CONTROL_NAMES = {SIGNAL: "signal", ROUNDABOUT: "circle"}


class MetricsCollector:
    """Accumulates metrics for one run.

    Attributes:
        warmup_s: Seconds excluded from all statistics.
        tt, tt_free: Travel and free-flow times of completed trips.
        exits_per_bin: Completed trips per minute.
        jn_delay_sum, jn_delay_cnt: Approach delay sums/counts per node.
        q_sum: (bins, N) summed queue samples per node; q_max: (N,) max queue.
        sp_sum, sp_cnt: (bins, E) speed sums/counts per edge.
        hard_brakes: Vehicle-steps braking harder than the engine's HARD_BRAKE.
        overlap_fixes: Corrections made by the engine's separation safety net.
        overlaps: Vehicles found overlapping their lane leader at a sample
            (after the safety net; expected 0).
    """

    def __init__(self, net, duration_s: float, warmup_s: float):
        """Allocate accumulators.

        Args:
            net: Network being simulated.
            duration_s: Total simulated seconds.
            warmup_s: Seconds at the start excluded from metrics.
        """
        self.net, self.warmup_s, self.duration_s = net, warmup_s, duration_s
        n_bins = int(np.ceil(duration_s / BIN_S)) + 1
        N, E = len(net.node_xy), net.n_edges
        self.tt, self.tt_free, self.unserved, self.gridlock = [], [], 0, 0
        self.hard_brakes = self.overlap_fixes = self.overlaps = 0
        self.exits_per_bin = np.zeros(n_bins)
        self.jn_delay_sum, self.jn_delay_cnt = np.zeros(N), np.zeros(N)
        self.q_sum, self.q_samples = np.zeros((n_bins, N)), np.zeros(n_bins)
        self.q_max = np.zeros(N)
        self.sp_sum, self.sp_cnt = np.zeros((n_bins, E)), np.zeros((n_bins, E))
        self.junctions = np.flatnonzero(np.isin(net.node_kind, JUNCTION_KINDS))
        self.edge_entries = np.zeros(E, np.int64)
        self.lane_seconds = np.zeros((E, MAX_LANES), np.int64)
        self.closed_lane_seconds = self.red_entries = 0

    def _bin(self, t: float) -> int:
        return int(t // BIN_S)

    def on_unserved(self, t_sched: float) -> None:
        """A trip had no route in this network."""
        if t_sched >= self.warmup_s:
            self.unserved += 1

    def on_gridlock(self, t: float, count: int) -> None:
        """`count` vehicles were removed after being stuck too long."""
        if t >= self.warmup_s:
            self.gridlock += count

    def on_exit(self, t: float, t_sched: np.ndarray, t_free: np.ndarray) -> None:
        """Vehicles left the network at time t."""
        self.exits_per_bin[self._bin(t)] += len(t_sched)
        keep = t_sched >= self.warmup_s
        self.tt.extend((t - t_sched[keep]).tolist())
        self.tt_free.extend(t_free[keep].tolist())

    def on_edge_enter(self, edges: np.ndarray) -> None:
        """Vehicles entered these road edges (spawned there or arrived from a junction)."""
        np.add.at(self.edge_entries, edges, 1)

    def on_junction(self, t: float, nodes: np.ndarray, delays: np.ndarray,
                    on_red: np.ndarray | None = None) -> None:
        """Vehicles entered junction movements.

        Args:
            t: Simulation time (s).
            nodes: Junction node per vehicle.
            delays: Approach delays (s).
            on_red: True where the vehicle's signal showed red as it entered.
        """
        if on_red is not None:
            self.red_entries += int(on_red.sum())
        if t < self.warmup_s or len(nodes) == 0:
            return
        np.add.at(self.jn_delay_sum, nodes, np.maximum(delays, 0.0))
        np.add.at(self.jn_delay_cnt, nodes, 1)

    def sample(self, t: float, ag) -> None:
        """Once-per-second queue and speed sample of all active vehicles."""
        if t < self.warmup_s:
            return
        net, b = self.net, self._bin(t)
        link, speed = ag.view("link"), ag.view("speed")
        on_edge = link < net.n_edges
        e, v = link[on_edge], speed[on_edge]
        np.add.at(self.sp_sum[b], e, v)
        np.add.at(self.sp_cnt[b], e, 1)
        rem = net.link_len[e] - ag.view("pos")[on_edge]
        queued = (v < QUEUE_SPEED_MS) & (rem < QUEUE_RANGE_M)
        q = np.bincount(net.link_to[e[queued]], minlength=len(net.node_xy))
        self.q_sum[b] += q
        self.q_samples[b] += 1
        self.q_max = np.maximum(self.q_max, q)

    def sample_usage(self, ag) -> None:
        """Once-per-second count of who is where (from t = 0, for the edit checks)."""
        net = self.net
        link, lane, pos = ag.view("link"), ag.view("lane"), ag.view("pos")
        on_edge = link < net.n_edges
        np.add.at(self.lane_seconds, (link[on_edge], lane[on_edge]), 1)
        rear = pos - 2.0  # at least this much of the vehicle counts as "inside"
        self.closed_lane_seconds += int(net.lane_blocked(link, lane, rear, rear).sum())

    def _bins_after_warmup(self) -> np.ndarray:
        """Indices of whole minute bins after the warm-up."""
        first = int(np.ceil(self.warmup_s / BIN_S))
        last = int(self.duration_s // BIN_S)
        return np.arange(first, max(first, last))

    def _kpis(self, in_network: int, waiting: int) -> dict:
        """Headline numbers of the run."""
        tt, free = np.asarray(self.tt), np.asarray(self.tt_free)
        bins = self._bins_after_warmup()
        q_tot = (self.q_sum[bins].sum(axis=1) / np.maximum(self.q_samples[bins], 1))
        sp = self.sp_sum[bins].sum() / max(self.sp_cnt[bins].sum(), 1)
        observed_h = max(len(bins) * BIN_S / 3600.0, 1e-9)
        return {
            "trips_completed": int(len(tt)),
            "mean_travel_time_s": _r(tt.mean()) if len(tt) else None,
            "p50_travel_time_s": _r(np.percentile(tt, 50)) if len(tt) else None,
            "p90_travel_time_s": _r(np.percentile(tt, 90)) if len(tt) else None,
            "mean_delay_s": _r((tt - free).mean()) if len(tt) else None,
            "throughput_vph": _r(self.exits_per_bin[bins].sum() / observed_h),
            "mean_queue_veh": _r(q_tot.mean()) if len(bins) else None,
            "max_queue_veh": _r(q_tot.max()) if len(bins) else None,
            "mean_speed_kmh": _r(sp * 3.6),
            "unserved_trips": int(self.unserved),
            "gridlock_removed": int(self.gridlock),
            "hard_brakes": int(self.hard_brakes),
            "overlaps": int(self.overlaps),
            "in_network_at_end": int(in_network),
            "waiting_at_entries_at_end": int(waiting),
        }

    def _junction_rows(self) -> list[dict]:
        """Per-junction delay, throughput and queue statistics."""
        bins = self._bins_after_warmup()
        q_mean = self.q_sum[bins].sum(axis=0) / max(self.q_samples[bins].sum(), 1)
        rows = []
        for n in self.junctions:
            cnt = self.jn_delay_cnt[n]
            rows.append({
                "node": int(n), "label": self.net.node_label[n],
                "signalised": bool(self.net.node_kind[n] == SIGNAL),
                "control": CONTROL_NAMES.get(int(self.net.node_kind[n]), "priority"),
                "delay_mean_s": _r(self.jn_delay_sum[n] / cnt) if cnt else None,
                "vehicles": int(cnt),
                "queue_mean_veh": _r(q_mean[n]), "queue_max_veh": int(self.q_max[n]),
            })
        return rows

    def summary(self, in_network: int, waiting: int) -> dict:
        """JSON-serialisable result of the run.

        Args:
            in_network: Vehicles still driving when the run ended.
            waiting: Vehicles still queued at entries when the run ended.
        Returns:
            Dict with `kpi`, `series`, `travel_time_hist`, `junctions`,
            `queue_by_junction` and `speed_heatmap`.
        """
        bins = self._bins_after_warmup()
        q_bins = self.q_sum[bins] / np.maximum(self.q_samples[bins], 1)[:, None]
        edges = np.arange(0.0, TT_MAX_S + TT_BIN_S, TT_BIN_S)
        counts = np.histogram(np.minimum(self.tt, TT_MAX_S - 1e-6), bins=edges)[0]
        with np.errstate(invalid="ignore", divide="ignore"):
            heat = self.sp_sum[bins] / self.sp_cnt[bins] * 3.6
        return {
            "kpi": self._kpis(in_network, waiting),
            "series": {"minute": bins.tolist(),
                       "throughput_per_min": self.exits_per_bin[bins].tolist(),
                       "queue_total": [_r(x) for x in q_bins.sum(axis=1)]},
            "travel_time_hist": {"bin_edges_s": edges.tolist(), "counts": counts.tolist()},
            "junctions": self._junction_rows(),
            "queue_by_junction": {self.net.node_label[n]: [_r(x) for x in q_bins[:, n]]
                                  for n in self.junctions},
            "speed_heatmap": {"minute": bins.tolist(),
                              "kmh": [[None if not np.isfinite(x) else round(float(x), 1)
                                       for x in row] for row in heat]},
            "usage": self._usage(),
        }

    def _usage(self) -> dict:
        """Counts that show whether vehicles honoured the scenario edits."""
        lanes = self.net.link_lanes[:self.net.n_edges]
        return {"edge_entries": self.edge_entries.tolist(),
                "lane_seconds": [row[:n].tolist() for row, n in
                                 zip(self.lane_seconds, lanes, strict=True)],
                "closed_lane_seconds": int(self.closed_lane_seconds),
                "red_entries": int(self.red_entries)}


def _r(x: float, nd: int = 2) -> float:
    """Round a numpy scalar to a plain float."""
    return round(float(x), nd)


def compare(base: dict, scen: dict) -> dict:
    """Baseline vs scenario KPI table and per-junction delay deltas.

    Args:
        base: `summary()` of the baseline run.
        scen: `summary()` of the scenario run.
    Returns:
        {"kpi": {name: {baseline, scenario, delta, delta_pct}},
         "junctions": [{label, baseline_delay_s, scenario_delay_s, delta_s}]}.
    """
    kpi = {}
    for k, b in base["kpi"].items():
        s = scen["kpi"].get(k)
        num = isinstance(b, int | float) and isinstance(s, int | float)
        delta = _r(s - b) if num else None
        pct = _r(100.0 * (s - b) / b, 1) if num and b else None
        kpi[k] = {"baseline": b, "scenario": s, "delta": delta, "delta_pct": pct}
    by_label = {j["label"]: j for j in scen["junctions"]}
    jn = []
    for j in base["junctions"]:
        s = by_label.get(j["label"], {})
        b_d, s_d = j["delay_mean_s"], s.get("delay_mean_s")
        jn.append({"label": j["label"], "baseline_delay_s": b_d, "scenario_delay_s": s_d,
                   "delta_s": _r(s_d - b_d) if b_d is not None and s_d is not None else None})
    return {"kpi": kpi, "junctions": jn}
