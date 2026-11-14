"""Intelligent Driver Model (Treiber et al., 2000), vectorised.

    a_IDM = a * [1 - (v / v0)^4 - (s* / s)^2]
    s*    = s0 + max(0, v T + v dv / (2 sqrt(a b)))

where s is the bumper-to-bumper gap and dv = v - v_leader.
"""

import numpy as np

MAX_DECEL = 6.0  # emergency braking limit (m/s^2)
MIN_GAP = 0.1    # avoid division by zero when bumpers touch


def idm_accel(v: np.ndarray, v0: np.ndarray, a: np.ndarray, b: np.ndarray, T: np.ndarray,
              s0: np.ndarray, gap: np.ndarray, v_lead: np.ndarray) -> np.ndarray:
    """IDM acceleration for many vehicles at once.

    Args:
        v: Current speeds (m/s).
        v0: Desired speeds (m/s), > 0.
        a: Max accelerations (m/s^2).
        b: Comfortable decelerations (m/s^2).
        T: Safe time headways (s).
        s0: Minimum jam gaps (m).
        gap: Bumper-to-bumper gaps to the leader (m); np.inf for free road.
        v_lead: Leader speeds (m/s); ignored where gap is inf.
    Returns:
        Accelerations (m/s^2), clipped to [-MAX_DECEL, a].
    """
    free = 1.0 - (v / np.maximum(v0, 0.1)) ** 4
    dv = v - np.where(np.isfinite(gap), v_lead, v)
    s_star = s0 + np.maximum(0.0, v * T + v * dv / (2.0 * np.sqrt(a * b)))
    s = np.maximum(gap, MIN_GAP)
    interaction = np.where(np.isfinite(gap), (s_star / s) ** 2, 0.0)
    return np.clip(a * (free - interaction), -MAX_DECEL, a)
