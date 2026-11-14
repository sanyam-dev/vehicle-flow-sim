"""Agent-based traffic engine: one fixed time step at a time.

Per step (dt = 0.5 s):

1. spawn      scheduled vehicles enter at their entry edge if there is room
2. index      sort all vehicles + closure obstacles by (lane, position)
3. admit      junction control grants movements (signals, conflicts, priority)
4. leaders    gap and speed of whatever each vehicle follows
5. IDM        car-following acceleration (+ slowing down for turns)
6. MOBIL      lane changes; leaders/IDM are refreshed for those that moved
7. integrate  ballistic update, never overlapping the leader
8. transfer   edge -> movement -> edge hand-over, exits, junction delay
8b. separate  safety net: no vehicle may overlap the one ahead in its lane
9. gridlock   vehicles at a standstill for GRIDLOCK_S are removed (as SUMO's
              teleport does) so a local deadlock cannot freeze the network;
              they are counted in the `gridlock_removed` metric

Capacity is never set anywhere: it emerges from IDM headways, lane counts,
MOBIL and junction control.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.sim.agents import AgentArrays
from app.sim.idm import MAX_DECEL, idm_accel
from app.sim.junctions import admit
from app.sim.laneindex import LaneIndex
from app.sim.leaders import junction_queues, leader_gaps, stop_line_distance
from app.sim.metrics import MetricsCollector
from app.sim.mobil import lane_changes
from app.sim.recorder import FrameRecorder
from app.sim.signals import RED, movement_states
from app.sim.spawn import Spawner
from app.sim.vehicles import param_array

PARAM_NAMES = ("length", "v0", "a", "b", "T", "s0", "politeness", "threshold", "b_safe",
               "lc_interval", "critical_gap")
STOPPED_MS = 0.5
GRIDLOCK_S = 300.0  # continuous standstill longer than any signal cycle
OVERLAP_EPS_M = 0.05
LANE_CHANGE_S = 2.0  # seconds a lane change takes on screen
HARD_BRAKE = 4.0  # deceleration (m/s^2) counted as a hard-braking event
TURN_BRAKE_SHARE = 0.7  # start slowing for a turn at this share of comfortable braking
_LANE_BIG = 1e7  # separates lanes when taking a running minimum over all actors


@dataclass
class SimConfig:
    """Run settings.

    Attributes:
        duration_s: Simulated seconds (including warm-up).
        warmup_s: Seconds excluded from metrics.
        dt: Time step (s).
        frame_dt: Seconds between recorded frames / metric samples.
        seed: Random seed (lane-change jitter; demand has its own seed use).
        signal_stop_s: Red time per approach at traffic signals (s).
    """

    duration_s: float = 1800.0
    warmup_s: float = 300.0
    dt: float = 0.5
    frame_dt: float = 1.0
    seed: int = 42
    signal_stop_s: float = 45.0


class Engine:
    """Runs one simulation (baseline or scenario) on a compiled network."""

    def __init__(self, net, routes, schedule, plans, cfg: SimConfig,
                 out_dir: Path | None = None):
        """Set up state.

        Args:
            net: Compiled Network.
            routes: RouteTable for `net`.
            schedule: Spawn schedule (shared by baseline and scenario).
            plans: SignalPlans for `net`.
            cfg: Run settings.
            out_dir: Folder for recorded frames, or None for metrics only.
        """
        self.net, self.routes, self.plans, self.cfg = net, routes, plans, cfg
        self.params = {k: param_array(k) for k in PARAM_NAMES}
        self.ag = AgentArrays()
        self.rng = np.random.default_rng(cfg.seed + 1)
        self.metrics = MetricsCollector(net, cfg.duration_s, cfg.warmup_s)
        self.recorder = FrameRecorder(net, out_dir, cfg.frame_dt)
        self.spawner = Spawner(schedule, routes)
        self.t = 0.0
        self.n_steps = 0
        self.v0_eff = np.zeros(0)
        self.queue_room = np.zeros(0)
        self.sig = np.zeros(net.n_movements, np.int8)
        self.amber_left = np.zeros(net.n_movements)

    def _index(self) -> LaneIndex:
        """Lane index of all vehicles plus closure obstacles."""
        ag, net = self.ag, self.net
        return LaneIndex(ag.view("link"), ag.view("lane"), ag.view("pos"), ag.view("speed"),
                         self.params["length"][ag.view("vtype")],
                         (net.clo_link, net.clo_lane, net.clo_s0))

    def _next_links(self) -> np.ndarray:
        """Next link on each agent's route, -1 when on the last link."""
        rt, ag = self.routes, self.ag
        route, p = ag.view("route"), ag.view("rptr") + 1
        has = p < rt.length[route]
        idx = np.where(has, rt.off[route] + p, 0)
        return np.where(has, rt.links[idx], -1)

    def _accelerations(self, gap: np.ndarray, v_lead: np.ndarray,
                       nxt: np.ndarray) -> np.ndarray:
        """IDM acceleration, capped so vehicles slow down before tight turns."""
        ag, net, P = self.ag, self.net, self.params
        vt, v, link = ag.view("vtype"), ag.view("speed"), ag.view("link")
        factor = ag.view("speed_factor")
        self.v0_eff = np.minimum(ag.view("v0"), net.link_vmax[link] * factor)
        acc = idm_accel(v, self.v0_eff, P["a"][vt], P["b"][vt], P["T"][vt], P["s0"][vt],
                        gap, v_lead)
        turn = nxt >= net.n_edges
        v_turn = np.where(turn, net.link_vmax[np.maximum(nxt, 0)] * factor, np.inf)
        rem = np.maximum(net.link_len[link] - ag.view("pos"), 0.5)
        need = np.where(v > v_turn, (v**2 - v_turn**2) / (2 * rem), 0.0)
        # Also stay able to stop at the stop line after the junction ahead.
        d_stop = np.maximum(stop_line_distance(self, nxt) - P["s0"][vt], 0.5)
        need = np.maximum(need, v**2 / (2 * d_stop))
        # Brake for these only once it takes a real deceleration (not from afar).
        brake = np.where(need >= TURN_BRAKE_SHARE * P["b"][vt], -need, np.inf)
        acc = np.minimum(np.minimum(acc, brake), self._queue_accel(nxt))
        return np.maximum(acc, -MAX_DECEL)

    def _queue_accel(self, nxt: np.ndarray) -> np.ndarray:
        """Acceleration limit from vehicles on other turn paths (merge / diverge).

        Until the shared space starts, the follower may always drive up to
        where it starts (as if that were a stop line). Also stores the
        room each vehicle has (`self.queue_room`) for the integration clamp.
        """
        ag, P = self.ag, self.params
        vt, v = ag.view("vtype"), ag.view("speed")
        q = junction_queues(self, nxt)
        self.queue_room = q.room
        acc = idm_accel(v, self.v0_eff, P["a"][vt], P["b"][vt], P["T"][vt], P["s0"][vt],
                        q.gap, q.v_lead)
        # ... never more restrictive than stopping where the shared space starts.
        to_start = idm_accel(v, self.v0_eff, P["a"][vt], P["b"][vt], P["T"][vt], P["s0"][vt],
                             np.where(q.dist > 0, q.dist, np.inf), np.zeros(ag.n))
        return np.where(q.dist > 0, np.maximum(acc, to_start), acc)

    def _integrate(self, acc: np.ndarray, gap: np.ndarray, v_lead: np.ndarray) -> None:
        """Ballistic position/speed update (v >= 0) that never overruns the leader."""
        ag, dt = self.ag, self.cfg.dt
        v = ag.view("speed")
        v_new = v + acc * dt
        stops = v_new < 0
        ds = np.where(stops, -v**2 / (2 * np.minimum(acc, -1e-9)), v * dt + 0.5 * acc * dt**2)
        v_new = np.maximum(v_new, 0.0)
        room = np.maximum(np.minimum(gap, self.queue_room) - OVERLAP_EPS_M, 0.0)
        clamp = ds > room
        ds = np.where(clamp, room, ds)
        v_new = np.where(clamp, np.minimum(v_new, v_lead), v_new)
        ag.view("pos")[:] += ds
        ag.view("acc")[:] = (v_new - v) / dt
        ag.view("speed")[:] = v_new
        if self.t >= self.cfg.warmup_s:
            self.metrics.hard_brakes += int((ag.view("acc") < -HARD_BRAKE).sum())
        # Lane changes are drawn over LANE_CHANGE_S: `lat` eases towards `lane`.
        lat, on_edge = ag.view("lat"), ag.view("link") < self.net.n_edges
        step = np.clip(ag.view("lane") - lat, -dt / LANE_CHANGE_S, dt / LANE_CHANGE_S)
        lat[:] = np.where(on_edge, lat + step, lat)
        still = v_new < STOPPED_MS
        slow = still & (ag.view("link") < self.net.n_edges)
        wait, stuck = ag.view("wait"), ag.view("stuck")
        wait[:] = np.where(slow, wait + dt, wait)
        stuck[:] = np.where(still, stuck + dt, 0.0)

    def _enter_movement(self, rows: np.ndarray, nxt: np.ndarray) -> None:
        """Hand `rows` from their road edge to the granted movement."""
        ag, net = self.ag, self.net
        e = ag.link[rows]
        free_t = net.link_len[e] / np.minimum(net.link_vmax[e] * ag.speed_factor[rows],
                                              ag.v0[rows])
        delay = (self.t - ag.t_edge[rows]) - free_t
        m = nxt[rows] - net.n_edges
        self.metrics.on_junction(self.t, net.mv_node[m], delay, self.sig[m] == RED)
        ag.pos[rows] -= net.link_len[e]
        ag.lane_in[rows] = ag.lane[rows]
        ag.lane[rows] = net.movement_lane(nxt[rows], ag.lane[rows])
        ag.link[rows] = nxt[rows]
        ag.rptr[rows] += 1
        ag.granted[rows], ag.wait[rows] = False, 0.0

    def _transfer(self, nxt: np.ndarray) -> None:
        """Move vehicles past the end of their link onto the next one, or out."""
        ag, net = self.ag, self.net
        exited = np.zeros(ag.n, dtype=bool)
        for _ in range(3):  # a vehicle may cross a short movement in one step
            link = ag.view("link")
            over = (ag.view("pos") >= net.link_len[link]) & ~exited
            if not over.any():
                break
            on_edge = link < net.n_edges
            out = over & (nxt < 0)
            exited |= out
            stuck = over & on_edge & (nxt >= 0) & ~ag.view("granted")
            ag.pos[np.flatnonzero(stuck)] = net.link_len[link[stuck]] - OVERLAP_EPS_M
            go_mv = np.flatnonzero(over & on_edge & (nxt >= 0) & ag.view("granted"))
            self._enter_movement(go_mv, nxt)
            go_edge = np.flatnonzero(over & ~on_edge)
            ag.pos[go_edge] -= net.link_len[link[go_edge]]
            ag.link[go_edge], ag.lane[go_edge] = nxt[go_edge], ag.next_lane[go_edge]
            ag.lat[go_edge] = ag.next_lane[go_edge]
            ag.rptr[go_edge] += 1
            ag.t_edge[go_edge] = self.t
            self.metrics.on_edge_enter(nxt[go_edge])
            nxt = self._next_links()
        if exited.any():
            self.metrics.on_exit(self.t, ag.view("t_sched")[exited], ag.view("t_free")[exited])
            ag.remove(exited)

    def _separate(self) -> None:
        """Safety net: pull back any vehicle that overlaps the actor ahead in its lane.

        For actors sorted by (lane, position) the furthest allowed front of
        actor i is min over j >= i of (pos_j - S_j) + S_i, with S the running
        sum of lengths; one reversed running minimum gives all of them.
        A corrected vehicle also drops to its leader's speed.
        """
        ag = self.ag
        if ag.n < 2:
            return
        li = self._index()
        S = np.cumsum(li.length)
        lane = np.cumsum(np.concatenate([[0], li.key[1:] != li.key[:-1]])) * _LANE_BIG
        w = li.pos - S + lane
        allowed = np.minimum.accumulate(w[::-1])[::-1] - lane + S
        fix = np.flatnonzero(li.is_veh & (allowed < li.pos - OVERLAP_EPS_M))
        if len(fix) == 0:
            return
        rows = li.agent[fix]
        ag.pos[rows] = np.maximum(allowed[fix], 0.0)
        lead = np.minimum(fix + 1, len(li.pos) - 1)
        ag.speed[rows] = np.minimum(ag.speed[rows], li.speed[lead])
        if self.t >= self.cfg.warmup_s:
            self.metrics.overlap_fixes += len(rows)
            self.metrics.overlaps += int((allowed[fix] < -OVERLAP_EPS_M).sum())

    def _clear_gridlock(self) -> None:
        """Remove vehicles that have not moved for GRIDLOCK_S (deadlock breaker)."""
        gone = self.ag.view("stuck") >= GRIDLOCK_S
        if gone.any():
            self.metrics.on_gridlock(self.t, int(gone.sum()))
            self.ag.remove(gone)

    def step(self) -> None:
        """Advance the simulation by one time step (see module docstring)."""
        self.spawner.step(self, self._index(), self.t)
        li = self._index()
        nxt = self._next_links()
        self.sig, self.amber_left = movement_states(self.plans, self.t)
        admit(self, li, nxt)
        gap, v_lead = leader_gaps(self, li, nxt)
        acc = self._accelerations(gap, v_lead, nxt)
        if len(lane_changes(self, li, nxt, gap, v_lead, acc)):
            li = self._index()
            gap, v_lead = leader_gaps(self, li, nxt)
            acc = self._accelerations(gap, v_lead, nxt)
        self._integrate(acc, gap, v_lead)
        self.t += self.cfg.dt
        self.n_steps += 1
        self._transfer(nxt)
        self._separate()
        self._clear_gridlock()
        if self.n_steps % max(1, round(self.cfg.frame_dt / self.cfg.dt)) == 0:
            self.metrics.sample(self.t, self.ag)
            self.metrics.sample_usage(self.ag)
            self.recorder.capture(self.t, self.ag)

    def run(self) -> dict:
        """Run to `cfg.duration_s` and return the metrics summary."""
        while self.t < self.cfg.duration_s - 1e-9:
            self.step()
        self.recorder.close()
        return self.metrics.summary(self.ag.n, self.spawner.waiting)
