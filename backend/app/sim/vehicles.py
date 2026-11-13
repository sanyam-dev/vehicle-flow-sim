"""Vehicle types and their behavioural parameters (IDM + MOBIL).

Defaults are tuned for Indian urban traffic: two-wheelers and autos are
short, accept small gaps, are not very polite and change lanes often;
trucks and buses are long, sluggish and conservative. Capacity is never
set directly; it emerges from these parameters.
"""

from dataclasses import dataclass

import numpy as np

BIKE, AUTO, CAR, TRUCK = 0, 1, 2, 3
TYPE_NAMES = ("bike", "auto", "car", "truck")


@dataclass(frozen=True)
class VehicleType:
    """Per-type model parameters.

    Attributes:
        length: Vehicle length (m).
        v0: Max desired speed (m/s); the actual desired speed is capped by the link.
        v0_sd: Relative std-dev of a vehicle's speed factor vs the speed limit.
        a: IDM max acceleration (m/s^2).
        b: IDM comfortable deceleration (m/s^2).
        T: IDM safe time headway (s).
        s0: IDM minimum jam gap (m).
        politeness: MOBIL politeness p (0 = selfish).
        threshold: MOBIL switching threshold (m/s^2).
        b_safe: MOBIL max deceleration imposed on the new follower (m/s^2).
        lc_interval: Seconds between lane-change decisions.
        critical_gap: Seconds of gap accepted at priority junctions.
    """

    length: float
    v0: float
    v0_sd: float
    a: float
    b: float
    T: float
    s0: float
    politeness: float
    threshold: float
    b_safe: float
    lc_interval: float
    critical_gap: float


TYPES: tuple[VehicleType, ...] = (
    VehicleType(2.0, 60 / 3.6, 0.12, 2.0, 3.0, 0.9, 1.0, 0.1, 0.05, 4.5, 1.0, 2.5),   # bike
    VehicleType(2.8, 45 / 3.6, 0.10, 1.2, 2.5, 1.1, 1.5, 0.2, 0.10, 4.0, 1.5, 3.0),   # auto
    VehicleType(4.3, 70 / 3.6, 0.10, 1.5, 2.5, 1.4, 2.0, 0.5, 0.20, 4.0, 2.0, 3.5),   # car
    VehicleType(10.0, 50 / 3.6, 0.08, 0.8, 2.0, 1.8, 3.0, 0.7, 0.30, 3.0, 4.0, 4.5),  # truck/bus
)

# Share of each type in the fleet (bike, auto, car, truck) by time-of-day band.
MIX_DAY = np.array([0.45, 0.15, 0.32, 0.08])
MIX_NIGHT = np.array([0.30, 0.10, 0.35, 0.25])


def param_array(name: str) -> np.ndarray:
    """Vector of one parameter indexed by vehicle type.

    Args:
        name: VehicleType attribute name, e.g. "length".
    Returns:
        (4,) float array; use as `param_array("a")[agent_type]`.
    """
    return np.array([getattr(t, name) for t in TYPES], dtype=float)


def fleet_mix(hour: float) -> np.ndarray:
    """Type shares for a time of day (more trucks at night).

    Args:
        hour: Hour of day, 0-24.
    Returns:
        (4,) probabilities summing to 1.
    """
    night = hour < 6 or hour >= 22
    return MIX_NIGHT if night else MIX_DAY
