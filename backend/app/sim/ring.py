"""Roundabout (circle) logic: one circulating lane around an island.

Every turn path at a roundabout joins the ring, follows it and leaves at
its exit (see `movements._circle_path`). Positions on the ring are measured
as arc length in the driving direction, so all vehicles of one roundabout
can be compared whatever path they are on:

* entry: a vehicle may enter when no vehicle already on the ring (or
  already granted from another arm) reaches its joining point around the
  time it does, and the ring is free right at that point;
* circulating: every vehicle follows the nearest vehicle ahead of it on the
  part of the ring it still has to drive.
"""

from dataclasses import dataclass

import numpy as np

GAP_AHEAD_S = 1.5   # a ring vehicle must not reach my joining point this soon after me
GAP_BEHIND_S = 1.0  # ... nor this shortly before me
CRAWL_MS = 1.0      # speed floor when estimating arrival times
YIELD_SETBACK_M = 2.0  # a waiting vehicle stops this far short of the ring
EXIT_REACH_M = 6.0     # a vehicle leaving the ring still sweeps this far past its exit point


@dataclass
class RingState:
    """Vehicles on, or granted onto, roundabout turn paths.

    Attributes:
        rows: (k,) agent rows.
        node: (k,) roundabout node.
        inside: (k,) True once on the turn path (not still on the approach road).
        s: (k,) ring coordinate (m): where the vehicle is, or where it will join.
        to_join: (k,) distance (m) still to drive before it is on the ring.
        left: (k,) ring distance (m) it still drives before leaving at its exit.
        circ: (k,) ring circumference (m).
    """

    rows: np.ndarray
    node: np.ndarray
    inside: np.ndarray
    s: np.ndarray
    to_join: np.ndarray
    left: np.ndarray
    circ: np.ndarray


def ring_state(eng, nxt: np.ndarray) -> RingState:
    """Collect every vehicle that is on a ring or has a grant to enter one.

    Vehicles that already left the ring towards their exit are not included.

    Args:
        eng: Engine (uses ag, net).
        nxt: (n,) next link per agent.
    """
    ag, net = eng.ag, eng.net
    E = net.n_edges
    link, pos = ag.view("link"), ag.view("pos")
    on_mv = link >= E
    m_all = np.where(on_mv, link, np.where(nxt >= E, nxt, E)) - E
    if net.n_movements == 0:
        m_all = np.zeros(ag.n, dtype=np.int64)
        ring = np.zeros(ag.n, dtype=bool)
    else:
        ring = net.mv_ring[m_all] & (on_mv | (ag.view("granted") & (nxt >= E)))
    p = np.where(on_mv, pos, pos - net.link_len[link])  # negative while still approaching
    rows = np.flatnonzero(ring)
    m, p = m_all[rows], p[rows]
    r_in, r_len = net.mv_ring_in[m], net.mv_ring_len[m]
    keep = p <= r_in + r_len
    rows, m, p, r_in, r_len = rows[keep], m[keep], p[keep], r_in[keep], r_len[keep]
    circ = net.node_ring_circ[net.mv_node[m]]
    done = np.maximum(p - r_in, 0.0)
    return RingState(rows, net.mv_node[m], p >= 0, (net.mv_ring_s0[m] + done) % circ,
                     np.maximum(r_in - p, 0.0), r_len - done, circ)


def entry_blocked(eng, st: RingState, i: int, m: int, rem: float) -> bool:
    """True if agent `i` must not enter ring movement `m` now.

    Args:
        eng: Engine.
        st: Ring state of this step.
        i: Agent row (on its approach road).
        m: Ring movement it wants.
        rem: Distance from its front to the stop line (m).
    """
    ag, net, P = eng.ag, eng.net, eng.params
    sel = (st.node == net.mv_node[m]) & (st.rows != i)
    if not sel.any():
        return False
    v_ring = net.link_vmax[net.n_edges + m]
    circ = st.circ[sel][0]
    d = (net.mv_ring_s0[m] - st.s[sel]) % circ      # their ring distance to my joining point
    passes = d <= st.left[sel] + 1.0                 # ... which they do reach before leaving
    length = P["length"][ag.vtype[st.rows[sel]]]
    need = P["length"][ag.vtype[i]] + P["s0"][ag.vtype[i]]
    ahead = (st.s[sel] - net.mv_ring_s0[m]) % circ - length
    if (st.inside[sel] & (ahead < need) & (ahead > -length)).any():
        return True                                  # the ring is occupied right where I join
    t_me = (rem + net.mv_ring_in[m]) / max(0.5 * (ag.speed[i] + v_ring), CRAWL_MS)
    t_them = (st.to_join[sel] + d) / np.maximum(ag.speed[st.rows[sel]], CRAWL_MS)
    clash = passes & (t_them > t_me - GAP_BEHIND_S) & (t_them < t_me + GAP_AHEAD_S)
    squeezed = passes & st.inside[sel] & (d < need)  # right behind my joining point
    return bool((clash | squeezed).any())


def _yield_gap(eng, st: RingState, k: int, on_arc: np.ndarray) -> float | None:
    """Room for a vehicle still on its entry: it stops short of the ring while a
    circulating vehicle is about to pass (or sits right before) its joining point."""
    ag, P = eng.ag, eng.params
    if len(on_arc) == 0 or st.to_join[k] <= 0:
        return None
    up = (st.s[k] - st.s[on_arc]) % st.circ[k]       # their distance to my joining point
    need = P["length"][ag.vtype[st.rows[k]]] + P["s0"][ag.vtype[st.rows[k]]]
    reach = need + ag.speed[st.rows[on_arc]] * GAP_AHEAD_S
    near = (up <= st.left[on_arc] + EXIT_REACH_M) & (up < reach)
    # ... or is still passing it (its rear has not cleared the joining point).
    past = (st.s[on_arc] - st.s[k]) % st.circ[k]
    near |= past < P["length"][ag.vtype[st.rows[on_arc]]] + 1.0
    return float(st.to_join[k] - YIELD_SETBACK_M) if near.any() else None


def ring_leaders(eng, st: RingState) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Leader on the ring for every vehicle in `st` that has one.

    A vehicle follows the nearest vehicle ahead on the part of the ring it
    still drives. Circulating traffic has priority: vehicles still on their
    entry are not obstacles for it, and they give way at the joining point.

    Returns:
        (rows, gap, leader speed, dist) for vehicles that have a ring leader;
        `dist` is the distance before the follower is on the ring itself.
    """
    ag, P = eng.ag, eng.params
    out = ([], [], [], [])
    for node in np.unique(st.node):
        idx = np.flatnonzero(st.node == node)
        arc = idx[st.inside[idx] & (st.to_join[idx] <= 0)]
        for k in idx:
            others = arc[arc != k]
            best = None
            if len(others):
                d = (st.s[others] - st.s[k]) % st.circ[k]
                ok = d <= st.left[k] + 1.0
                if ok.any():
                    j = others[ok][np.argmin(d[ok])]
                    gap = st.to_join[k] + float(d[ok].min()) - P["length"][ag.vtype[st.rows[j]]]
                    best = (gap, ag.speed[st.rows[j]], st.to_join[k])
            stop = _yield_gap(eng, st, k, others)
            if stop is not None and (best is None or stop < max(best[0], best[2])):
                best = (stop, 0.0, 0.0)
            if best is not None:
                for lst, val in zip(out, (st.rows[k], *best), strict=True):
                    lst.append(val)
    return tuple(np.asarray(x) for x in out)
