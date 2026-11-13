"""Leader lookup: what each vehicle is following this step.

`pos` is the front bumper position along the current link. The "leader"
of a vehicle is whichever comes first:

* the next actor in its own lane (a vehicle, or a closure obstacle that
  behaves like a stopped zero-length vehicle);
* the stop line at the end of a road edge, if the vehicle has a next
  movement but has not been granted entry (a virtual stopped leader);
* when granted / already inside a junction: the last vehicle on the
  movement, then the last vehicle in the chosen lane of the out-edge.

Vehicles at the end of their route see free road (they leave the network).
"""

import numpy as np

from app.sim.laneindex import LaneIndex, lane_key


def _look_into(li: LaneIndex, rows: np.ndarray, link: np.ndarray, lane: np.ndarray,
               dist: np.ndarray, gap: np.ndarray, v_lead: np.ndarray) -> np.ndarray:
    """Set leader gap/speed from the rear-most actor of another lane.

    Args:
        li: Lane index.
        rows: (k,) agent rows to update.
        link, lane: (k,) lane to look into.
        dist: (k,) distance from each vehicle's front to the start of that lane.
        gap, v_lead: (n,) output arrays, written at `rows` where a tail exists.
    Returns:
        (k,) bool, True where a tail was found.
    """
    tail = li.tail(lane_key(link, lane))
    found = tail >= 0
    t = tail[found]
    gap[rows[found]] = dist[found] + li.pos[t] - li.length[t]
    v_lead[rows[found]] = li.speed[t]
    return found


def leader_gaps(eng, li: LaneIndex, nxt: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Gap to, and speed of, every vehicle's leader (see module docstring).

    Args:
        eng: Engine (uses ag, net).
        li: Lane index built for the current positions.
        nxt: (n,) next link per agent, -1 at the end of its route.
    Returns:
        (gap (n,), v_lead (n,)); gap is np.inf on free road.
    """
    ag, net = eng.ag, eng.net
    E = net.n_edges
    link, pos = ag.view("link"), ag.view("pos")
    rem = net.link_len[link] - pos
    gap, v_lead = np.full(ag.n, np.inf), np.zeros(ag.n)

    own = li.own_leader()
    has = own >= 0
    j = own[has]
    gap[has] = li.pos[j] - li.length[j] - pos[has]
    v_lead[has] = li.speed[j]

    free = ~has & (nxt >= 0)
    on_edge = link < E
    granted = ag.view("granted")
    # Stop line: approaching a junction without permission.
    stop = free & on_edge & ~granted
    gap[stop] = rem[stop]
    # Granted on an edge: look into the movement, then into the out-edge lane.
    rows = np.flatnonzero(free & on_edge & granted)
    if len(rows):
        mv = nxt[rows]
        mv_lane = net.movement_lane(mv, ag.lane[rows])
        found = _look_into(li, rows, mv, mv_lane, rem[rows], gap, v_lead)
        r2 = rows[~found]
        out = net.mv_out[nxt[r2] - E]
        _look_into(li, r2, out, ag.next_lane[r2], rem[r2] + net.link_len[nxt[r2]], gap, v_lead)
    # Inside a movement: look into the chosen out-edge lane.
    rows = np.flatnonzero(free & ~on_edge)
    if len(rows):
        _look_into(li, rows, nxt[rows], ag.next_lane[rows], rem[rows], gap, v_lead)
    return gap, v_lead
