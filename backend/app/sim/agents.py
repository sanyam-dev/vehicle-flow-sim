"""Agent storage: one numpy array per attribute (structure of arrays).

Only "active" vehicles live here. Slots are compacted after removals so
the first `n` entries are always the active vehicles, which keeps every
engine kernel a plain vectorised operation over `[:n]`.
"""

import numpy as np

# name -> dtype of each per-agent attribute
FIELDS: dict[str, type] = {
    "vid": np.int64,          # unique vehicle id (index into the spawn schedule)
    "vtype": np.int8,         # BIKE / AUTO / CAR / TRUCK
    "link": np.int32,         # current link (edge or movement)
    "lane": np.int8,          # lane on the current link (0 = kerb side)
    "pos": np.float64,        # distance along the link (m)
    "speed": np.float64,      # m/s
    "acc": np.float64,        # last acceleration (m/s^2)
    "v0": np.float64,         # personal max desired speed (m/s)
    "speed_factor": np.float64,  # multiplier on link speed limits
    "route": np.int32,        # route id in the RouteTable
    "rptr": np.int32,         # index of the current link inside the route
    "granted": np.bool_,      # admitted into the next junction movement
    "next_lane": np.int8,     # lane to take on the out-edge after the movement
    "t_sched": np.float64,    # scheduled departure time (s), includes entry queueing
    "t_free": np.float64,     # free-flow travel time of the whole route (s)
    "t_edge": np.float64,     # time the current/last road edge was entered (s)
    "wait": np.float64,       # seconds spent waiting at the current stop line
    "stuck": np.float64,      # seconds continuously at standstill (gridlock check)
    "lc_timer": np.float64,   # seconds until the next lane-change decision
    "lat": np.float64,        # drawn lateral position in lane units (eases towards `lane`)
    "lane_in": np.int8,       # lane on the in-edge when the current movement was entered
}


class AgentArrays:
    """Growable structure-of-arrays for active vehicles.

    Attributes:
        n: Number of active vehicles (valid rows are [:n]).
        <field>: One numpy array per entry of FIELDS.
    """

    def __init__(self, capacity: int = 1024):
        """Allocate empty storage.

        Args:
            capacity: Initial number of slots (grows by doubling).
        """
        self.n = 0
        self._cap = capacity
        for name, dt in FIELDS.items():
            setattr(self, name, np.zeros(capacity, dtype=dt))

    def _grow(self, needed: int) -> None:
        """Double capacity until `needed` slots fit."""
        cap = self._cap
        while cap < needed:
            cap *= 2
        if cap == self._cap:
            return
        for name in FIELDS:
            old = getattr(self, name)
            new = np.zeros(cap, dtype=old.dtype)
            new[: self.n] = old[: self.n]
            setattr(self, name, new)
        self._cap = cap

    def add(self, **values: np.ndarray) -> None:
        """Append k new vehicles.

        Args:
            **values: field name -> (k,) array; fields not given are zero.
        """
        k = len(next(iter(values.values())))
        if k == 0:
            return
        self._grow(self.n + k)
        for name, arr in values.items():
            getattr(self, name)[self.n:self.n + k] = arr
        for name in FIELDS.keys() - values.keys():
            getattr(self, name)[self.n:self.n + k] = 0
        self.n += k

    def remove(self, mask: np.ndarray) -> None:
        """Drop active vehicles where `mask` is True (order is preserved).

        Args:
            mask: (n,) bool array over the active rows.
        """
        if not mask.any():
            return
        keep = np.flatnonzero(~mask)
        for name in FIELDS:
            arr = getattr(self, name)
            arr[: len(keep)] = arr[keep]
        self.n = len(keep)

    def view(self, name: str) -> np.ndarray:
        """Active slice of one field (a view, writes go through)."""
        return getattr(self, name)[: self.n]
