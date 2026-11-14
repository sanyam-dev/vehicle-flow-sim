"""Leader lookup: what each vehicle is following this step.

`pos` is the front bumper position along the current link. The "leader"
of a vehicle is whichever comes first:

* the next actor in its own lane (a vehicle, or a closure obstacle that
  behaves like a stopped zero-length vehicle);
* the stop line at the end of a road edge, if the vehicle has a next
  movement, is within its approach horizon and has not been granted entry
  (a virtual stopped leader); at a red or late-amber signal this holds for
  every vehicle on the approach, so followers do not run the light;
* when granted / already inside a junction: the last vehicle on the
  movement, then the last vehicle in the chosen lane of the out-edge;
* inside a junction, vehicles on *other* turn paths that share road space:
  - merge: every vehicle heading for the same out-edge lane forms one queue
    ordered by distance to that lane, whatever path it is on;
  - diverge: vehicles that entered from the same in-edge lane follow each
    other until the one ahead has left the shared first metres;
  - lane drop: vehicles from different in-edge lanes that take the same
    lane of a narrower turn path enter it one after the other.

Vehicles at the end of their route see free road (they leave the network).
"""

from dataclasses import dataclass

import numpy as np

from app.sim.junctions import approach_horizon, must_stop_at_signal
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


def _queue_gaps(rows: np.ndarray, key: np.ndarray, x: np.ndarray, length: np.ndarray,
                speed: np.ndarray, vid: np.ndarray, dist: np.ndarray, q: "QueueGaps") -> None:
    """Make vehicles that share road space on different links follow each other.

    Within each `key` group the vehicles are ordered by `x` (larger = further
    ahead); each one's queue gap is tightened to the next vehicle ahead.

    Args:
        rows: (k,) agent rows taking part.
        key: (k,) group id (e.g. target out-lane).
        x: (k,) front-bumper coordinate along the shared path (m).
        length, speed, vid: (k,) vehicle lengths, speeds and ids (tie-break).
        dist: (k,) distance each vehicle still has before the shared space starts.
        q: Output, tightened in place.
    """
    if len(rows) < 2:
        return
    order = np.lexsort((-vid, x, key))
    f, a = order[:-1], order[1:]  # follower, vehicle ahead
    same = key[f] == key[a]
    f, a = f[same], a[same]
    g = x[a] - length[a] - x[f]
    # On a tie the nearer shared space wins (it limits the follower sooner).
    closer = (g < q.gap[rows[f]]) | ((g <= q.gap[rows[f]] + 1e-9) & (dist[f] < q.dist[rows[f]]))
    f, a, g = f[closer], a[closer], g[closer]
    q.gap[rows[f]], q.v_lead[rows[f]], q.dist[rows[f]] = g, speed[a], dist[f]


@dataclass
class QueueGaps:
    """Leaders on *other* turn paths that will share road space with a vehicle.

    Attributes:
        gap: (n,) gap to that leader once both are in the shared space (inf if none).
        v_lead: (n,) its speed.
        dist: (n,) distance before the shared space starts (0 = already shared).
            Until then the follower only needs to brake comfortably, or as
            hard as it takes to stop before the shared space.
    """

    gap: np.ndarray
    v_lead: np.ndarray
    dist: np.ndarray


def junction_queues(eng, nxt: np.ndarray) -> QueueGaps:
    """Merge and diverge queues inside junctions (see module docstring).

    Args:
        eng: Engine (uses ag, net, params).
        nxt: (n,) next link per agent.
    Returns:
        QueueGaps over all agents.
    """
    ag, net, P = eng.ag, eng.net, eng.params
    E = net.n_edges
    q = QueueGaps(np.full(ag.n, np.inf), np.zeros(ag.n), np.zeros(ag.n))
    link, pos = ag.view("link"), ag.view("pos")
    on_mv = link >= E
    coming = ~on_mv & ag.view("granted") & (nxt >= E)
    rows = np.flatnonzero(on_mv | coming)
    if len(rows) < 2:
        return q
    lk, mv = link[rows], np.where(on_mv[rows], link[rows], nxt[rows])
    length, speed = P["length"][ag.vtype[rows]], ag.speed[rows]
    rem = net.link_len[lk] - pos[rows]
    to_out = np.where(on_mv[rows], rem, rem + net.link_len[mv])
    ring = net.mv_ring[mv - E]  # a roundabout ring is one lane: everybody queues in it
    key = lane_key(net.mv_out[mv - E], np.where(ring, 0, ag.next_lane[rows]))
    _queue_gaps(rows, key, -to_out, length, speed, ag.vid[rows], to_out, q)
    # Diverge: same in-edge lane, until the rear of the one ahead leaves the shared part.
    x = np.where(on_mv[rows], pos[rows], -rem)
    lane_in = np.where(ring, 0, np.where(on_mv[rows], ag.lane_in[rows], ag.lane[rows]))
    sel = np.flatnonzero(x - length < net.mv_shared[mv - E])
    key = lane_key(net.mv_in[mv[sel] - E], lane_in[sel])
    _queue_gaps(rows[sel], key, x[sel], length[sel], speed[sel], ag.vid[rows[sel]],
                np.zeros(len(sel)), q)
    # Lane drop: several in-edge lanes can feed one lane of a narrower turn path.
    mv_lane = np.where(on_mv[rows], ag.lane[rows], net.movement_lane(mv, ag.lane[rows]))
    _queue_gaps(rows, lane_key(mv, mv_lane), x, length, speed, ag.vid[rows],
                np.where(on_mv[rows], 0.0, rem), q)
    return q


def stop_line_distance(eng, nxt: np.ndarray) -> np.ndarray:
    """Distance to the next stop line a vehicle has no grant for, beyond its junction.

    A vehicle that is granted, or already inside a junction, may meet the stop
    line at the end of the following road; it should not arrive there too fast.

    Args:
        eng: Engine (uses ag, net, routes).
        nxt: (n,) next link per agent.
    Returns:
        (n,) distances (m); inf where no such stop line lies ahead.
    """
    ag, net, rt = eng.ag, eng.net, eng.routes
    E = net.n_edges
    link = ag.view("link")
    rem = net.link_len[link] - ag.view("pos")
    on_mv = link >= E
    ahead = on_mv | (ag.view("granted") & (nxt >= E))
    # Route position of the road after the junction, and whether the route continues past it.
    p_out = ag.view("rptr") + np.where(on_mv, 1, 2)
    route = ag.view("route")
    more = ahead & (p_out + 1 < rt.length[route])
    idx = np.where(more, rt.off[route] + p_out, 0)
    d = rem + np.where(on_mv, 0.0, net.link_len[np.maximum(nxt, 0)]) + net.link_len[rt.links[idx]]
    return np.where(more, d, np.inf)


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
    # Stop line: approaching a junction without permission. Beyond the
    # approach horizon the vehicle has not asked yet and drives freely.
    stop = free & on_edge & ~granted & (rem < approach_horizon(eng))
    gap[stop] = rem[stop]
    # Signal telling the whole approach to stop, not only the first vehicle.
    red = must_stop_at_signal(eng, nxt) & ~granted & (rem < gap)
    gap[red], v_lead[red] = rem[red], 0.0
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
