"""Insert scheduled vehicles at entry nodes.

Due vehicles join a FIFO queue per entry. Each step, the head of every
queue enters its first road edge if some open lane has room at the start.
Vehicles that cannot enter keep waiting; that wait is part of their
travel time (entry delay). Trips without a route in the current network
(e.g. every path was closed) are counted as unserved and dropped.
"""

from collections import deque

import numpy as np

from app.sim.laneindex import LaneIndex, lane_key

INSERT_MARGIN_M = 2.0
# Wait for a gap that allows entering at this fraction of the leader's speed.
INSERT_SPEED_RATIO = 0.8


class Spawner:
    """Feeds the schedule into the engine.

    Attributes:
        unserved: Number of trips dropped for lack of a route.
        queued: Per-origin deque of vehicle ids waiting to enter.
    """

    def __init__(self, schedule, routes):
        """Prepare queues.

        Args:
            schedule: demand.Schedule (index = vehicle id).
            routes: routing.RouteTable of the network being simulated.
        """
        self.s = schedule
        self.routes = routes
        self.ptr = 0
        self.unserved = 0
        self.queued: dict[int, deque] = {}

    @property
    def waiting(self) -> int:
        """Vehicles currently queued at entries."""
        return sum(len(q) for q in self.queued.values())

    def _enqueue_due(self, t: float, metrics) -> None:
        """Move schedule entries with time <= t into the entry queues."""
        s, rt = self.s, self.routes
        while self.ptr < len(s) and s.t[self.ptr] <= t:
            vid = self.ptr
            self.ptr += 1
            if rt.pick(int(s.origin[vid]), int(s.dest[vid]), vid) is None:
                self.unserved += 1
                metrics.on_unserved(float(s.t[vid]))
                continue
            self.queued.setdefault(int(s.origin[vid]), deque()).append(vid)

    def _best_lane(self, eng, li: LaneIndex, edge: int,
                   need: float) -> tuple[int, float, float]:
        """Open lane at the start of `edge` with the most room.

        Returns:
            (lane, free metres, tail speed); lane is -1 if no lane has `need`
            metres free; free metres and tail speed are inf for an empty lane.
        """
        n = int(eng.net.link_lanes[edge])
        lanes = np.arange(n)
        space, tail = li.entry_space(lane_key(np.full(n, edge), lanes),
                                     np.full(n, eng.net.link_len[edge]))
        closed = eng.net.lane_blocked(np.full(n, edge), lanes, np.zeros(n), np.full(n, need))
        space = np.where(closed, -np.inf, space)
        k = int(np.argmax(space))
        if space[k] < need:
            return -1, 0.0, 0.0
        if tail[k] < 0:
            return k, np.inf, np.inf
        return k, float(space[k]), float(li.speed[tail[k]])

    def step(self, eng, li: LaneIndex, t: float) -> int:
        """Insert at most one vehicle per entry. Returns the number inserted."""
        self._enqueue_due(t, eng.metrics)
        P, s, rt = eng.params, self.s, self.routes
        cols = {k: [] for k in ("vid", "vtype", "link", "lane", "speed", "v0",
                                "speed_factor", "route", "t_sched", "t_free", "t_edge")}
        for q in self.queued.values():
            if not q:
                continue
            vid = q[0]
            vt = int(s.vtype[vid])
            r = rt.pick(int(s.origin[vid]), int(s.dest[vid]), vid)
            edge = int(rt.links[rt.off[r]])
            lane, space, v_tail = self._best_lane(eng, li, edge, P["length"][vt] + P["s0"][vt]
                                                  + INSERT_MARGIN_M)
            if lane < 0:
                continue
            v0 = P["v0"][vt] * s.speed_factor[vid]
            v_des = min(v0, eng.net.link_vmax[edge] * s.speed_factor[vid])
            # Enter at the IDM equilibrium speed for the gap ahead (s0 + v T = gap),
            # and only once that is close to the speed of the stream.
            gap = space - min(P["length"][vt], 0.5 * eng.net.link_len[edge])
            v_eq = max(gap - P["s0"][vt], 0.0) / P["T"][vt]
            if v_eq < INSERT_SPEED_RATIO * min(v_tail, v_des):
                continue
            q.popleft()
            # ... and slow enough to stop comfortably before the first stop line.
            run = eng.net.link_len[edge] - P["length"][vt] - P["s0"][vt]
            v_stop = np.sqrt(2.0 * P["b"][vt] * max(run, 0.0)) if rt.length[r] > 1 else np.inf
            v_in = min(v_des, v_eq, v_stop)
            for k, val in zip(cols, (vid, vt, edge, lane, v_in, v0, s.speed_factor[vid], r,
                                     s.t[vid], rt.free_time[r], t), strict=True):
                cols[k].append(val)
        if cols["vid"]:
            n = len(cols["vid"])
            arr = {k: np.asarray(v) for k, v in cols.items()}
            # Front bumper one vehicle length in, so the rear sits on the entry node.
            pos = np.minimum(P["length"][arr["vtype"]], 0.5 * eng.net.link_len[arr["link"]])
            eng.metrics.on_edge_enter(arr["link"])
            eng.ag.add(**arr, pos=pos, lat=arr["lane"].astype(float),
                       lc_timer=eng.rng.uniform(0, 2.0, n))
        return len(cols["vid"])
