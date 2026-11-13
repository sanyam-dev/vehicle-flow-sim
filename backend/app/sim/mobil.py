"""MOBIL lane-change model (Kesting, Treiber & Helbing, 2007), vectorised.

A vehicle i considers moving one lane left or right. With new follower n
and old follower o, it changes when

    safety:    a_n(after) >= -b_safe
    incentive: a_i(after) - a_i(now) + p [ (a_n(after) - a_n(now))
                                         + (a_o(after) - a_o(now)) ] > threshold - bias

The bias adds strategic and mandatory goals: get into the lane that serves
the next turn (stronger near the stop line), leave a lane that is closed
ahead, and a weak keep-to-kerb bias for slow vehicles. Overtaking is
symmetric (both sides allowed), as in Indian traffic.

Decisions are staggered: each vehicle re-evaluates every `lc_interval`
seconds (with jitter), so bikes and autos weave more than trucks.
"""

import numpy as np

from app.sim.idm import idm_accel
from app.sim.junctions import preferred_lane
from app.sim.laneindex import LaneIndex, lane_key
from app.sim.vehicles import AUTO, TRUCK

CLOSURE_LOOKAHEAD_M = 150.0
CLOSURE_BIAS = 3.0
TURN_BIAS_FAR, TURN_BIAS_NEAR, TURN_BIAS_RANGE_M = 0.3, 2.5, 150.0
KERB_BIAS = 0.1
MIN_SIDE_GAP_M = 0.5
# No lane changes this close to the start of an edge: vehicles still
# leaving the junction behind could not see the cut-in.
NO_LC_ZONE_M = 15.0


def _idm_for(eng, rows: np.ndarray, v: np.ndarray, gap: np.ndarray,
             v_lead: np.ndarray) -> np.ndarray:
    """IDM acceleration of agent `rows` for hypothetical gap/leader speed."""
    P, vt = eng.params, eng.ag.vtype[rows]
    return idm_accel(v, eng.v0_eff[rows], P["a"][vt], P["b"][vt], P["T"][vt], P["s0"][vt],
                     gap, v_lead)


def _bias(eng, rows: np.ndarray, tgt: np.ndarray, nxt: np.ndarray) -> np.ndarray:
    """Strategic bias (m/s^2) for moving `rows` into lanes `tgt`."""
    ag, net = eng.ag, eng.net
    link, lane, pos = ag.link[rows], ag.lane[rows], ag.pos[rows]
    rem = net.link_len[link] - pos
    bias = np.zeros(len(rows))
    pref = preferred_lane(eng, rows, nxt)
    has = pref >= 0
    closer = np.abs(tgt - pref) < np.abs(lane - pref)
    farther = np.abs(tgt - pref) > np.abs(lane - pref)
    near = np.clip(1.0 - rem / TURN_BIAS_RANGE_M, 0.0, 1.0)
    strength = TURN_BIAS_FAR + (TURN_BIAS_NEAR - TURN_BIAS_FAR) * near
    bias += np.where(has & closer, strength, 0.0) - np.where(has & farther, strength, 0.0)
    ahead = pos + CLOSURE_LOOKAHEAD_M
    cur_closed = net.lane_blocked(link, lane, pos, ahead)
    tgt_closed = net.lane_blocked(link, tgt, pos - eng.params["length"][ag.vtype[rows]], ahead)
    bias += np.where(cur_closed & ~tgt_closed, CLOSURE_BIAS, 0.0)
    bias -= np.where(tgt_closed, CLOSURE_BIAS, 0.0)
    slow = np.isin(ag.vtype[rows], (AUTO, TRUCK))
    bias += np.where(slow & (tgt < lane), KERB_BIAS, 0.0)
    return bias


def _evaluate(eng, li: LaneIndex, rows: np.ndarray, tgt: np.ndarray, nxt: np.ndarray,
              gap: np.ndarray, v_lead: np.ndarray, acc: np.ndarray) -> np.ndarray:
    """MOBIL score of moving `rows` into lanes `tgt`; -inf where unsafe.

    Args:
        eng: Engine.
        li: Lane index.
        rows: (k,) agent rows.
        tgt: (k,) target lanes (valid indices).
        nxt: (n,) next links.
        gap, v_lead, acc: (n,) current leader gaps/speeds and IDM accelerations.
    Returns:
        (k,) incentive minus threshold; > 0 means "change".
    """
    ag, net, P = eng.ag, eng.net, eng.params
    vt, v, pos = ag.vtype[rows], ag.speed[rows], ag.pos[rows]
    length = P["length"][vt]
    lead, foll = li.around(lane_key(ag.link[rows], tgt), pos)
    # Self in the target lane (stop line if no leader there).
    stop_gap = np.where(nxt[rows] >= 0, net.link_len[ag.link[rows]] - pos, np.inf)
    g_new = np.where(lead >= 0, li.pos[lead] - li.length[lead] - pos, stop_gap)
    vl_new = np.where(lead >= 0, li.speed[lead], 0.0)
    a_self = _idm_for(eng, rows, v, g_new, vl_new) - acc[rows]
    # New follower (vehicles only; obstacles never move).
    f_veh = (foll >= 0) & li.is_veh[np.maximum(foll, 0)]
    fa = li.agent[np.maximum(foll, 0)]
    g_f = np.where(foll >= 0, pos - length - li.pos[np.maximum(foll, 0)], np.inf)
    a_f_after = np.where(f_veh, _idm_for(eng, fa, ag.speed[fa], g_f, v), 0.0)
    d_new = np.where(f_veh, a_f_after - acc[fa], 0.0)
    # Old follower: after we leave it follows our current leader.
    of = li.own_follower()[rows]
    o_veh = (of >= 0) & li.is_veh[np.maximum(of, 0)]
    oa = li.agent[np.maximum(of, 0)]
    g_o = pos + gap[rows] - li.pos[np.maximum(of, 0)]
    a_o_after = np.where(o_veh, _idm_for(eng, oa, ag.speed[oa], g_o, v_lead[rows]), 0.0)
    d_old = np.where(o_veh, a_o_after - acc[oa], 0.0)
    score = (a_self + P["politeness"][vt] * (d_new + d_old) - P["threshold"][vt]
             + _bias(eng, rows, tgt, nxt))
    safe = (g_new > MIN_SIDE_GAP_M) & (g_f > MIN_SIDE_GAP_M) & (a_f_after >= -P["b_safe"][vt])
    safe &= ~net.lane_blocked(ag.link[rows], tgt, pos - length, pos)
    return np.where(safe, score, -np.inf)


def _deconflict(eng, rows: np.ndarray, tgt: np.ndarray) -> np.ndarray:
    """Keep only changers that do not cut into the same gap this step.

    Two vehicles moving into the same lane closer than one vehicle length
    plus jam gap apart would collide; the one further ahead wins.

    Returns:
        (k,) bool mask of accepted changes.
    """
    ag, P = eng.ag, eng.params
    key = lane_key(ag.link[rows], tgt)
    pos = ag.pos[rows]
    need = P["length"][ag.vtype[rows]] + P["s0"][ag.vtype[rows]]
    order = np.lexsort((-pos, key))
    ok = np.ones(len(rows), dtype=bool)
    last_key, last_rear = -1, np.inf
    for i in order:
        if key[i] == last_key and pos[i] > last_rear - need[i]:
            ok[i] = False
            continue
        last_key, last_rear = key[i], pos[i] - need[i]
    return ok


def lane_changes(eng, li: LaneIndex, nxt: np.ndarray, gap: np.ndarray,
                 v_lead: np.ndarray, acc: np.ndarray) -> np.ndarray:
    """Decide and apply lane changes for vehicles due to re-evaluate.

    Only vehicles on multi-lane road edges, not yet granted junction entry,
    are considered. Changes are instantaneous; the recorder smooths them.

    Args:
        eng: Engine (ag, net, params, rng, cfg.dt).
        li: Lane index for this step (before the changes).
        nxt, gap, v_lead, acc: (n,) next links, leader gaps/speeds, IDM accels.
    Returns:
        Agent rows that changed lane.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    timer = ag.view("lc_timer")
    timer -= eng.cfg.dt
    link = ag.view("link")
    due = (timer <= 0) & (link < net.n_edges) & ~ag.view("granted")
    due &= (net.link_lanes[link] > 1) & (ag.view("pos") > NO_LC_ZONE_M)
    rows = np.flatnonzero(due)
    if len(rows) == 0:
        return rows
    lane = ag.lane[rows].astype(np.int64)
    best, best_tgt = np.full(len(rows), -np.inf), lane.copy()
    for d in (-1, 1):
        tgt = lane + d
        valid = (tgt >= 0) & (tgt < net.link_lanes[link[rows]])
        score = np.full(len(rows), -np.inf)
        if valid.any():
            score[valid] = _evaluate(eng, li, rows[valid], tgt[valid], nxt, gap, v_lead, acc)
        better = score > np.maximum(best, 0.0)
        best[better], best_tgt[better] = score[better], tgt[better]
    jitter = eng.rng.uniform(0.75, 1.25, len(rows))
    timer[rows] = P["lc_interval"][ag.vtype[rows]] * jitter
    move = best > 0
    rows, tgt = rows[move], best_tgt[move]
    ok = _deconflict(eng, rows, tgt)
    ag.lane[rows[ok]] = tgt[ok]
    return rows[ok]
