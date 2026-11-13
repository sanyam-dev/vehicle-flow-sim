"""Junction control: who may enter which movement, and when.

Every step, the front vehicle of each lane that is close to its stop line
asks to enter its next movement. A request is granted when all hold:

1. Signal: the movement shows GREEN (or AMBER and the vehicle can no
   longer stop comfortably). Unsignalised movements are always GREEN.
2. Conflicts: no vehicle on a conflicting movement still has to clear the
   conflict point (where the paths cross, or the end for merges), and no
   vehicle granted onto a conflicting movement arrives within the
   requester's critical gap (gap acceptance).
3. Priority (unsignalised only): no higher-priority vehicle (higher road
   class; long waiters are boosted) has claimed a conflicting movement.
4. Keep clear: the out-edge has room for the vehicle in some open lane.

The grant fixes the lane the vehicle takes on the out-edge (`next_lane`).
At signals, grants are revoked on AMBER/RED if the vehicle can still stop.
"""

import numpy as np

from app.sim.laneindex import MAX_KEY_LANES, LaneIndex, lane_key
from app.sim.movements import ANY_LANE, LAST_LANE
from app.sim.signals import AMBER, GREEN, RED

STARVATION_S = 15.0
CLAIM_ETA_S = 4.0
CLAIM_DIST_M = 10.0  # a vehicle this close to the stop line always claims
APPROACH_MARGIN_M = 15.0
CLEAR_MARGIN_M = 1.0  # rear bumper must be this far past a conflict point
KEEP_LANE_BONUS_M = 15.0
OUT_ANTICIPATE_S = 2.0  # a moving out-lane tail frees this many seconds of road


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


def _candidates(eng, li: LaneIndex, nxt: np.ndarray) -> np.ndarray:
    """Agent rows that may request a movement this step, in priority order."""
    ag, net, P = eng.ag, eng.net, eng.params
    link, v, vt = ag.view("link"), ag.view("speed"), ag.view("vtype")
    rem = net.link_len[link] - ag.view("pos")
    horizon = P["s0"][vt] + v * P["T"][vt] + v**2 / (2 * P["b"][vt]) + APPROACH_MARGIN_M
    cand = ((link < net.n_edges) & (nxt >= 0) & ~ag.view("granted")
            & (li.own_leader() == -1) & (rem < horizon))
    rows = np.flatnonzero(cand)
    eta = rem[rows] / np.maximum(v[rows], 0.5)
    boost = ag.view("wait")[rows] > STARVATION_S
    order = np.lexsort((ag.view("vid")[rows], eta, -net.link_rank[link[rows]], ~boost))
    return rows[order]


def _revoke_on_red(eng, sig: np.ndarray, nxt: np.ndarray) -> None:
    """Withdraw signal grants that are now AMBER/RED if the vehicle can still stop."""
    ag, net, P = eng.ag, eng.net, eng.params
    g = np.flatnonzero(ag.view("granted") & (ag.view("link") < net.n_edges))
    if len(g) == 0:
        return
    m = nxt[g] - net.n_edges
    v = ag.speed[g]
    rem = net.link_len[ag.link[g]] - ag.pos[g]
    can_stop = rem > v**2 / (2 * P["b"][ag.vtype[g]]) + 2.0
    revoke = (sig[m] != GREEN) & can_stop
    ag.granted[g[revoke]] = False


def _occupancy(eng, nxt: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-movement rear-most vehicle rear, earliest granted ETA, reserved out-lane length.

    Returns:
        (rear_min (M,), eta_min (M,), reserved (keys,)); rear_min is the
        smallest (rear bumper) position of any vehicle on the movement, inf if empty.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    E, M = net.n_edges, net.n_movements
    link = ag.view("link")
    on_mv = link >= E
    rear_min = np.full(M, np.inf)
    rear = ag.view("pos")[on_mv] - P["length"][ag.view("vtype")[on_mv]]
    np.minimum.at(rear_min, link[on_mv] - E, rear)
    granted = ag.view("granted") & ~on_mv
    eta_min = np.full(M, np.inf)
    rem = net.link_len[link[granted]] - ag.view("pos")[granted]
    np.minimum.at(eta_min, nxt[granted] - E, rem / np.maximum(ag.view("speed")[granted], 0.5))
    moving = on_mv | granted
    mv_out = net.mv_out if M else np.zeros(1, np.int32)  # guard: network without junctions
    out_edge = np.where(on_mv, nxt, mv_out[np.clip(nxt - E, 0, max(M - 1, 0))])
    keys = lane_key(out_edge[moving], ag.view("next_lane")[moving])
    need = P["length"][ag.view("vtype")[moving]] + P["s0"][ag.view("vtype")[moving]]
    reserved = np.bincount(keys, weights=need, minlength=(net.n_links + 1) * MAX_KEY_LANES)
    return rear_min, eta_min, reserved


def admit(eng, li: LaneIndex, nxt: np.ndarray, sig: np.ndarray) -> None:
    """Grant junction entry for this step (see module docstring).

    Args:
        eng: Engine (uses ag, net, params, routes).
        li: Lane index for this step.
        nxt: (n,) next link of each agent, -1 at the end of its route.
        sig: (M,) signal state per movement.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    _revoke_on_red(eng, sig, nxt)
    rear_min, eta_min, reserved = _occupancy(eng, nxt)
    claimed = np.zeros(net.n_movements, dtype=bool)
    for i in _candidates(eng, li, nxt):
        m = int(nxt[i] - net.n_edges)
        vt, v = ag.vtype[i], ag.speed[i]
        rem = net.link_len[ag.link[i]] - ag.pos[i]
        # Permissive minor approaches at signals yield like a priority arm.
        signalised = eng.plans.mv_ctrl[m] >= 0
        if sig[m] == RED or (sig[m] == AMBER and rem > v**2 / (2 * P["b"][vt])):
            continue
        conf = net.mv_conflicts[m]
        uncleared = rear_min[conf] < net.mv_conflict_s[m] + CLEAR_MARGIN_M
        blocked = uncleared.any() or (eta_min[conf] < P["critical_gap"][vt]).any()
        if not signalised:
            blocked = blocked or claimed[conf].any()
        e_out = int(net.mv_out[m])
        space = _out_lane_space(eng, li, e_out, reserved)
        need = P["length"][vt] + P["s0"][vt]
        if not blocked and space.max() >= need:
            lane = _pick_out_lane(eng, i, e_out, space, need)
            ag.granted[i], ag.next_lane[i] = True, lane
            eta_min[m] = min(eta_min[m], rem / max(v, 0.5))
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
