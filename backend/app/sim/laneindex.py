"""Per-step spatial index of everything that occupies a lane.

"Actors" are vehicles plus static closure obstacles (a closed lane span
acts like a stopped zero-length vehicle at its start). Actors are sorted
by (lane key, position); lane key = link * MAX_KEY_LANES + lane. All
queries (own leader, leader/follower in another lane, tail of a lane)
are `searchsorted` lookups on that order.
"""

import numpy as np

MAX_KEY_LANES = 8
_POS_SCALE = 1e5  # composite = key * _POS_SCALE + pos  (links are < 100 km)
# An empty lane always admits one vehicle, even if the link is shorter than it.
EMPTY_LANE_MIN_M = 15.0


def lane_key(link: np.ndarray, lane: np.ndarray) -> np.ndarray:
    """Integer key of (link, lane) pairs."""
    return link.astype(np.int64) * MAX_KEY_LANES + lane.astype(np.int64)


class LaneIndex:
    """Sorted actors with vectorised neighbour queries.

    Attributes:
        key, pos, speed, length: Actor arrays in sorted order.
        is_veh: True for vehicles, False for obstacles.
        agent: Agent row index for vehicles, -1 for obstacles.
        rank_of_agent: Sorted position of each agent row.
    """

    def __init__(self, link, lane, pos, speed, length, obstacles):
        """Build the index.

        Args:
            link, lane, pos, speed, length: (n,) active-vehicle arrays.
            obstacles: (link, lane, s0) arrays of closure starts.
        """
        o_link, o_lane, o_pos = obstacles
        # A sentinel obstacle with key -1 (sorts first, never queried) keeps
        # the arrays non-empty, so index -1 lookups are always in bounds.
        o_link = np.append(np.asarray(o_link, np.int64), -1)
        o_lane = np.append(np.asarray(o_lane, np.int64), MAX_KEY_LANES - 1)
        o_pos = np.append(np.asarray(o_pos, float), 0.0)
        n, k = len(link), len(o_link)
        key = np.concatenate([lane_key(link, lane), lane_key(o_link, o_lane)])
        p = np.concatenate([pos, o_pos])
        order = np.lexsort((p, key))
        self.key = key[order]
        self.pos = p[order]
        self.speed = np.concatenate([speed, np.zeros(k)])[order]
        self.length = np.concatenate([length, np.zeros(k)])[order]
        self.agent = np.concatenate([np.arange(n), np.full(k, -1)])[order]
        self.is_veh = self.agent >= 0
        self.comp = self.key * _POS_SCALE + self.pos
        self.rank_of_agent = np.empty(n, dtype=np.int64)
        self.rank_of_agent[self.agent[self.is_veh]] = np.flatnonzero(self.is_veh)
        self._ukey, self._ufirst = np.unique(self.key, return_index=True)

    def own_leader(self) -> np.ndarray:
        """Sorted index of each agent's leader in its own lane, or -1.

        Returns:
            (n_agents,) indices into the sorted arrays.
        """
        r = self.rank_of_agent
        nxt = r + 1
        ok = nxt < len(self.key)
        same = np.zeros(len(r), dtype=bool)
        same[ok] = self.key[nxt[ok]] == self.key[r[ok]]
        return np.where(same, nxt, -1)

    def own_follower(self) -> np.ndarray:
        """Sorted index of each agent's follower in its own lane, or -1."""
        r = self.rank_of_agent
        prv = r - 1
        ok = prv >= 0
        same = np.zeros(len(r), dtype=bool)
        same[ok] = self.key[prv[ok]] == self.key[r[ok]]
        return np.where(same, prv, -1)

    def around(self, key: np.ndarray, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Leader (first actor at >= pos) and follower (last actor < pos) in lanes `key`.

        Args:
            key: (m,) lane keys to look in.
            pos: (m,) positions.
        Returns:
            (leader idx, follower idx), each -1 when absent.
        """
        j = np.searchsorted(self.comp, key * _POS_SCALE + pos, side="left")
        lead = np.where((j < len(self.key)) & (self.key[np.minimum(j, len(self.key) - 1)] == key),
                        j, -1)
        f = j - 1
        foll = np.where((f >= 0) & (self.key[np.maximum(f, 0)] == key), f, -1)
        return lead, foll

    def tail(self, key: np.ndarray) -> np.ndarray:
        """Sorted index of the rear-most actor in each lane `key`, or -1."""
        if len(self._ukey) == 0:
            return np.full(len(key), -1)
        i = np.clip(np.searchsorted(self._ukey, key), 0, len(self._ukey) - 1)
        return np.where(self._ukey[i] == key, self._ufirst[i], -1)

    def entry_space(self, key: np.ndarray, link_len: np.ndarray,
                    anticipate_s: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        """Free length at the start of lanes `key`, and the rear-most actor there.

        Args:
            key: (m,) lane keys.
            link_len: (m,) length of each lane's link.
            anticipate_s: Count the distance a moving tail covers in this many
                seconds as free (a discharging queue is not a blocked lane).
        Returns:
            (space (m,), tail idx (m,) or -1). Empty lanes report at least
            EMPTY_LANE_MIN_M so short links never lock out long vehicles.
        """
        tail = self.tail(key)
        rear = self.pos[tail] - self.length[tail] + self.speed[tail] * anticipate_s
        space = np.where(tail >= 0, rear, np.maximum(link_len, EMPTY_LANE_MIN_M))
        return space, tail
