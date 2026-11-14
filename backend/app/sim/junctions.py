"""Junction control: who may enter which movement, and when.

Every step, the front vehicle of each lane that is close to its stop line
asks to enter its next movement. A request is granted when all hold:

1. Signal: the movement shows GREEN, or AMBER and the vehicle will be over
   the stop line before the amber ends. Unsignalised movements are always GREEN.
2. Conflicts: no vehicle on a crossing movement still has to clear the
   conflict point (where the paths cross), and no
   vehicle granted onto a conflicting movement arrives before the requester
   has cleared that point (gap acceptance: the larger of the critical gap
   and the requester's own clearing time, so slow trucks need longer gaps).
3. Priority (unsignalised only): no higher-priority vehicle (higher road
   class; long waiters are boosted) has claimed a conflicting movement.
4. Keep clear: the out-edge has room for the vehicle in some open lane.

Movements that merge into the same out-edge are not exclusive: merging
vehicles follow each other in one queue (see `leaders.junction_queues`).

The grant fixes the lane the vehicle takes on the out-edge (`next_lane`).
At signals, grants are revoked on RED, and on AMBER unless the vehicle
makes it over the line in time (`must_stop_at_signal`).
Every grant is re-checked each step: if a conflicting movement is still
occupied when the vehicle is about to arrive, the grant is withdrawn while
the vehicle can still brake (`_revalidate_grants`).
"""

import numpy as np

from app.sim.laneindex import MAX_KEY_LANES, LaneIndex, lane_key
from app.sim.movements import ANY_LANE, LAST_LANE
from app.sim.signals import AMBER, RED

STARVATION_S = 15.0
CLAIM_ETA_S = 4.0
CLAIM_DIST_M = 10.0  # a vehicle this close to the stop line always claims
APPROACH_MARGIN_M = 15.0
CLEAR_MARGIN_M = 1.0  # rear bumper must be this far past a conflict point
KEEP_LANE_BONUS_M = 15.0
OUT_ANTICIPATE_S = 2.0  # a moving out-lane tail frees this many seconds of road
CLEAR_TIME_MARGIN_S = 1.0  # spare time after the requester has cleared a conflict point
FIRM_DECEL = 4.0  # braking (m/s^2) accepted to avoid entering an occupied junction


def time_to_cover(d: np.ndarray, v: np.ndarray, a: np.ndarray, vmax: np.ndarray) -> np.ndarray:
    """Seconds to travel `d` metres, accelerating at `a` from `v` up to `vmax`.

    Args:
        d: Distances (m), >= 0.
        v: Current speeds (m/s).
        a: Accelerations (m/s^2), > 0.
        vmax: Speed caps (m/s).
    Returns:
        Travel times (s); works on scalars and arrays alike.
    """
    vmax = np.maximum(vmax, 0.5)
    v = np.minimum(v, vmax)
    d = np.maximum(d, 0.0)
    t_acc = (vmax - v) / a
    d_acc = v * t_acc + 0.5 * a * t_acc**2
    t_in = (-v + np.sqrt(v * v + 2.0 * a * d)) / a
    return np.where(d <= d_acc, t_in, t_acc + (d - d_acc) / vmax)


def _out_lane_space(eng, li: LaneIndex, e_out: int, reserved: np.ndarray) -> np.ndarray:
    """Free length at the start of each lane of `e_out` (-inf for closed lanes)."""
    net = eng.net
    lanes = np.arange(net.link_lanes[e_out])
    keys = lane_key(np.full(len(lanes), e_out), lanes)
    space, _ = li.entry_space(keys, np.full(len(lanes), net.link_len[e_out]), OUT_ANTICIPATE_S)
    space = space - reserved[keys]
    blocked = net.lane_blocked(np.full(len(lanes), e_out), lanes, np.zeros(len(lanes)),
                               np.full(len(lanes), 10.0))
    return np.where(blocked, -np.inf, space)


def _pick_out_lane(eng, i: int, e_out: int, space: np.ndarray, need: float) -> int:
    """Best lane on the out-edge with room for `need` metres.

    Most free space wins, with bonuses for keeping the current lane (no
    merging across lanes inside the junction) and for the lane that suits
    the turn after.
    """
    ag, rt = eng.ag, eng.routes
    after = int(ag.rptr[i]) + 3
    score = space.copy()
    score[min(int(ag.lane[i]), len(space) - 1)] += KEEP_LANE_BONUS_M
    if after < rt.length[ag.route[i]]:
        pref = int(eng.net.mv_pref[rt.links[rt.off[ag.route[i]] + after] - eng.net.n_edges])
        if pref != ANY_LANE:
            target = 0 if pref == 0 else len(space) - 1
            score[target] += 25.0
    score[space < need] = -np.inf
    return int(np.argmax(score))


def approach_horizon(eng) -> np.ndarray:
    """Distance (m) before a stop line at which each vehicle starts to care about it.

    Covers the jam gap, the headway and a comfortable stop, plus a margin, so a
    vehicle refused entry at this distance can still stop smoothly.
    """
    ag, P = eng.ag, eng.params
    v, vt = ag.view("speed"), ag.view("vtype")
    return P["s0"][vt] + v * P["T"][vt] + v**2 / (2 * P["b"][vt]) + APPROACH_MARGIN_M


def _candidates(eng, li: LaneIndex, nxt: np.ndarray) -> np.ndarray:
    """Agent rows that may request a movement this step, in priority order."""
    ag, net = eng.ag, eng.net
    link, v = ag.view("link"), ag.view("speed")
    rem = net.link_len[link] - ag.view("pos")
    cand = ((link < net.n_edges) & (nxt >= 0) & ~ag.view("granted")
            & (li.own_leader() == -1) & (rem < approach_horizon(eng)))
    rows = np.flatnonzero(cand)
    eta = rem[rows] / np.maximum(v[rows], 0.5)
    boost = ag.view("wait")[rows] > STARVATION_S
    order = np.lexsort((ag.view("vid")[rows], eta, -net.link_rank[link[rows]], ~boost))
    return rows[order]


def must_stop_at_signal(eng, nxt: np.ndarray) -> np.ndarray:
    """(n,) bool: vehicles on a road whose signal tells them to stop at the line.

    RED: always. AMBER: unless the vehicle will be over the line before the
    amber ends (at the mean of its speed and its turn speed), so nobody is
    asked to brake hard and nobody enters on red. Applies to every vehicle on
    the approach, not only the first in each lane.

    Args:
        eng: Engine (uses ag, net, sig, amber_left, cfg.dt).
        nxt: (n,) next link per agent.
    """
    ag, net = eng.ag, eng.net
    link, v = ag.view("link"), ag.view("speed")
    to_mv = (link < net.n_edges) & (nxt >= net.n_edges)
    m = np.where(to_mv, nxt - net.n_edges, 0)
    if net.n_movements == 0:
        return np.zeros(ag.n, dtype=bool)
    rem = net.link_len[link] - ag.view("pos")
    v_turn = net.link_vmax[np.where(to_mv, nxt, 0)] * ag.view("speed_factor")
    eta = rem / np.maximum(0.5 * (v + np.minimum(v, v_turn)), 0.5)
    late = eta > eng.amber_left[m] - eng.cfg.dt
    return to_mv & ((eng.sig[m] == RED) | ((eng.sig[m] == AMBER) & late))


def _revoke_on_red(eng, stop: np.ndarray) -> None:
    """Withdraw the grants of vehicles that must stop at their signal."""
    eng.ag.view("granted")[stop] = False


def _occupancy(eng, nxt: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-movement occupancy and reserved out-lane length.

    Returns:
        (rear_min (M,), eta_min (M,), exit_t (M,), reserved (keys,)).
        rear_min: smallest rear-bumper position of any vehicle on the movement
        (inf if empty). eta_min: earliest arrival at the stop line of a vehicle
        granted onto the movement. exit_t: seconds until the movement is empty.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    E, M = net.n_edges, net.n_movements
    link, vt, v = ag.view("link"), ag.view("vtype"), ag.view("speed")
    on_mv = link >= E
    rear_min, exit_t = np.full(M, np.inf), np.zeros(M)
    rear = ag.view("pos")[on_mv] - P["length"][vt[on_mv]]
    np.minimum.at(rear_min, link[on_mv] - E, rear)
    np.maximum.at(exit_t, link[on_mv] - E,
                  (net.link_len[link[on_mv]] - rear) / np.maximum(v[on_mv], 1.0))
    granted = ag.view("granted") & ~on_mv
    eta_min = np.full(M, np.inf)
    rem = net.link_len[link[granted]] - ag.view("pos")[granted]
    vmax = np.minimum(ag.view("v0")[granted],
                      net.link_vmax[link[granted]] * ag.view("speed_factor")[granted])
    np.minimum.at(eta_min, nxt[granted] - E,
                  time_to_cover(rem, v[granted], P["a"][vt[granted]], vmax))
    moving = on_mv | granted
    mv_out = net.mv_out if M else np.zeros(1, np.int32)  # guard: network without junctions
    out_edge = np.where(on_mv, nxt, mv_out[np.clip(nxt - E, 0, max(M - 1, 0))])
    keys = lane_key(out_edge[moving], ag.view("next_lane")[moving])
    need = P["length"][vt[moving]] + P["s0"][vt[moving]]
    reserved = np.bincount(keys, weights=need, minlength=(net.n_links + 1) * MAX_KEY_LANES)
    return rear_min, eta_min, exit_t, reserved


def _revalidate_grants(eng, nxt: np.ndarray, rear_min: np.ndarray, exit_t: np.ndarray) -> None:
    """Withdraw grants whose junction will still be occupied on arrival.

    A granted vehicle keeps its grant unless a conflicting movement holds a
    vehicle before the conflict point that will not be gone in time, and the
    granted vehicle can still stop before its line at FIRM_DECEL.

    Args:
        eng: Engine.
        nxt: (n,) next link per agent.
        rear_min, exit_t: (M,) outputs of `_occupancy`.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    for i in np.flatnonzero(ag.view("granted") & (ag.view("link") < net.n_edges)):
        m = int(nxt[i] - net.n_edges)
        conf = net.mv_cross[m]
        if len(conf) == 0:
            continue
        v, vt = ag.speed[i], ag.vtype[i]
        rem = net.link_len[ag.link[i]] - ag.pos[i]
        eta = float(time_to_cover(rem, v, P["a"][vt], max(v, net.link_vmax[ag.link[i]])))
        busy = (rear_min[conf] < net.mv_cross_s[m] + CLEAR_MARGIN_M) & (exit_t[conf] > eta)
        if busy.any() and rem > v**2 / (2 * FIRM_DECEL) + 1.0:
            ag.granted[i] = False


def _conflict_blocked(eng, i: int, m: int, rem: float, rear_min: np.ndarray,
                      eta_min: np.ndarray) -> bool:
    """True if agent `i` must not enter movement `m` because of conflicting traffic.

    Args:
        eng: Engine.
        i: Agent row.
        m: Movement index.
        rem: Distance from the vehicle's front to its stop line (m).
        rear_min, eta_min: (M,) outputs of `_occupancy`.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    conf = net.mv_cross[m]
    if len(conf) == 0:
        return False
    if (rear_min[conf] < net.mv_cross_s[m] + CLEAR_MARGIN_M).any():
        return True
    vt, v = ag.vtype[i], ag.speed[i]
    dist = rem + net.mv_cross_own[m] + P["length"][vt] + CLEAR_MARGIN_M
    v_box = net.link_vmax[net.n_edges + m] * ag.speed_factor[i]
    clear_t = time_to_cover(dist, min(v, v_box), P["a"][vt], v_box) + CLEAR_TIME_MARGIN_S
    return bool((eta_min[conf] < np.maximum(clear_t, P["critical_gap"][vt])).any())


def admit(eng, li: LaneIndex, nxt: np.ndarray) -> None:
    """Grant junction entry for this step (see module docstring).

    Args:
        eng: Engine (uses ag, net, params, routes).
        li: Lane index for this step.
        nxt: (n,) next link of each agent, -1 at the end of its route.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    stop = must_stop_at_signal(eng, nxt)
    _revoke_on_red(eng, stop)
    rear_min, eta_min, exit_t, reserved = _occupancy(eng, nxt)
    _revalidate_grants(eng, nxt, rear_min, exit_t)
    rear_min, eta_min, exit_t, reserved = _occupancy(eng, nxt)
    claimed = np.zeros(net.n_movements, dtype=bool)
    for i in _candidates(eng, li, nxt):
        m = int(nxt[i] - net.n_edges)
        vt, v = ag.vtype[i], ag.speed[i]
        rem = net.link_len[ag.link[i]] - ag.pos[i]
        # Permissive minor approaches at signals yield like a priority arm.
        signalised = eng.plans.mv_ctrl[m] >= 0
        if stop[i]:
            continue
        blocked = _conflict_blocked(eng, i, m, rem, rear_min, eta_min)
        if not signalised:
            blocked = blocked or claimed[net.mv_cross[m]].any()
        e_out = int(net.mv_out[m])
        space = _out_lane_space(eng, li, e_out, reserved)
        need = P["length"][vt] + P["s0"][vt]
        if not blocked and space.max() >= need:
            lane = _pick_out_lane(eng, i, e_out, space, need)
            ag.granted[i], ag.next_lane[i] = True, lane
            vmax = min(ag.v0[i], net.link_vmax[ag.link[i]] * ag.speed_factor[i])
            eta_min[m] = min(eta_min[m], float(time_to_cover(rem, v, P["a"][vt], vmax)))
            reserved[e_out * MAX_KEY_LANES + lane] += need
            claimed[m] = True
        elif (not signalised and space.max() >= need
              and (rem < CLAIM_DIST_M or rem / max(v, 0.5) < CLAIM_ETA_S)):
            # Only a vehicle that could actually go claims; one stuck behind a
            # full exit must not lock out crossing traffic.
            claimed[m] = True


def preferred_lane(eng, rows: np.ndarray, nxt: np.ndarray) -> np.ndarray:
    """Lane each agent should be in for its next movement (-1 = any).

    Args:
        eng: Engine.
        rows: Agent rows (on road edges).
        nxt: (n,) next link per agent.
    Returns:
        (len(rows),) target lane or -1.
    """
    net = eng.net
    out = np.full(len(rows), -1)
    has = nxt[rows] >= net.n_edges
    pref = np.full(len(rows), ANY_LANE)
    pref[has] = net.mv_pref[nxt[rows][has] - net.n_edges]
    lanes = net.link_lanes[eng.ag.link[rows]]
    out[pref == 0] = 0
    out[pref == LAST_LANE] = lanes[pref == LAST_LANE] - 1
    return out
