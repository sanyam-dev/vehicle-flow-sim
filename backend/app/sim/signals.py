"""Fixed-time traffic signal plans: fixed red, equal greens.

Each signalised junction gets one phase per approach direction, which is
the usual practice at Indian junctions. In-edges whose headings differ by
less than APPROACH_ANGLE_DEG share a phase.

The one timing input is the *stop duration*: how long every approach waits
on red. With P phases, amber A and lost time L = amber + all-red per phase,
all phases get the same green

    g = (stop + A - P L) / (P - 1),   cycle = P (g + L)

so that red = cycle - g - A = stop. Green never drops below MIN_GREEN_S,
which makes the real red longer than asked at junctions with many
approaches (4 approaches: at least 47 s). A junction can carry its own stop
duration (`Network.node_stop_s`, set by a scenario edit). The scenario runs
the same timings as the baseline unless a junction was edited.

With `permissive_minor=True`, minor side streets with little expected flow
get no phase: they are always allowed but yield to all conflicting traffic
like a priority junction's minor arm (see junctions.admit).
"""

from dataclasses import dataclass

import numpy as np

from app.sim.geometry import end_heading
from app.sim.network import SIGNAL, Network

AMBER_S, ALL_RED_S = 3.0, 2.0
MIN_GREEN_S = 10.0
DEFAULT_STOP_S = 45.0
APPROACH_ANGLE_DEG = 30.0
MINOR_CLASSES = {"residential", "service", "living_street", "unclassified"}
MINOR_PHASE_MIN_VPS = 150.0 / 3600.0  # below this a minor approach gets no phase
RED, GREEN, AMBER = 0, 1, 2


@dataclass
class SignalPlans:
    """All controllers of a network.

    Attributes:
        nodes: (C,) node index per controller.
        cycle: (C,) cycle lengths (s).
        offset: (C,) start offsets (s).
        starts: per controller, (P,) phase start times within the cycle.
        greens: per controller, (P,) green durations.
        mv_ctrl: (M,) controller of each movement, -1 if unsignalised.
        mv_phase: (M,) phase index of each movement.

    A movement with mv_ctrl == -1 is unsignalised: either its node has no
    signal or it is a permissive minor approach of a signalised node.
    """

    nodes: np.ndarray
    cycle: np.ndarray
    offset: np.ndarray
    starts: list[np.ndarray]
    greens: list[np.ndarray]
    mv_ctrl: np.ndarray
    mv_phase: np.ndarray


def _conflicting_edges(net: Network, a: int, b: int) -> bool:
    """True if any movement from in-edge `a` conflicts with one from in-edge `b`."""
    mv_b = set(np.flatnonzero(net.mv_in == b).tolist())
    return any(mv_b.intersection(net.mv_conflicts[m].tolist())
               for m in np.flatnonzero(net.mv_in == a))


def _approach_groups(net: Network, in_edges: list[int]) -> list[list[int]]:
    """Group in-edges of one node into phases.

    In-edges share a phase only if they arrive from a similar heading
    (within APPROACH_ANGLE_DEG) and none of their movements conflict.
    """
    groups: list[tuple[np.ndarray, list[int]]] = []
    cos_max = np.cos(np.radians(APPROACH_ANGLE_DEG))
    for e in in_edges:
        d = end_heading(net.geom.polyline(e), at_end=True)
        for heading, members in groups:
            if float(np.dot(heading, d)) > cos_max and not any(
                    _conflicting_edges(net, e, o) for o in members):
                members.append(e)
                break
        else:
            groups.append((d, [e]))
    return [m for _, m in groups]


def _phased_in_edges(net: Network, in_edges: list[int], edge_flow: np.ndarray) -> list[int]:
    """In-edges that get a signal phase (busy or non-minor approaches).

    Args:
        net: Compiled network.
        in_edges: In-edges of one signalised node.
        edge_flow: (E,) expected flow (veh/s), shorter for scenario edges.
    Returns:
        Subset of `in_edges`; the rest are permissive.
    """
    def flow(e: int) -> float:
        return edge_flow[e] if e < len(edge_flow) else 0.0
    return [e for e in in_edges
            if net.edge_highway[e] not in MINOR_CLASSES or flow(e) >= MINOR_PHASE_MIN_VPS]


def phase_timing(n_phases: int, stop_s: float) -> tuple[float, float]:
    """Cycle length and green time for equal greens and a fixed red.

    Args:
        n_phases: Number of phases (>= 2).
        stop_s: Wanted red time per approach (s).
    Returns:
        (cycle, green) in seconds; green >= MIN_GREEN_S.
    """
    lost = AMBER_S + ALL_RED_S
    green = max(MIN_GREEN_S, (stop_s + AMBER_S - n_phases * lost) / (n_phases - 1))
    return n_phases * (green + lost), green


def red_time(n_phases: int, stop_s: float) -> float:
    """Actual red per approach (s) for a wanted stop duration (>= stop_s)."""
    cycle, green = phase_timing(n_phases, stop_s)
    return cycle - green - AMBER_S


def build_signal_plans(net: Network, edge_flow: np.ndarray, stop_s: float = DEFAULT_STOP_S,
                       permissive_minor: bool = False) -> SignalPlans:
    """Create a fixed-time plan for every signalised junction.

    Args:
        net: Compiled network.
        edge_flow: (E,) expected flow per edge (veh/s); edges beyond its
            length (scenario additions) count as zero. Only used to pick
            permissive approaches.
        stop_s: Red time per approach (s), unless the node has its own.
        permissive_minor: Give quiet minor approaches no phase (see module docstring).
    Returns:
        SignalPlans covering all movements.
    """
    M = net.n_movements
    mv_ctrl, mv_phase = np.full(M, -1, np.int32), np.zeros(M, np.int32)
    nodes, cycles, offsets, starts, greens = [], [], [], [], []
    for node in np.flatnonzero(net.node_kind == SIGNAL):
        mvs = np.flatnonzero(net.mv_node == node)
        in_edges = sorted({int(net.mv_in[m]) for m in mvs})
        if permissive_minor:
            in_edges = _phased_in_edges(net, in_edges, edge_flow)
        groups = _approach_groups(net, in_edges) if len(in_edges) >= 2 else []
        if len(groups) < 2:
            continue
        own = net.node_stop_s[node]
        cycle, green = phase_timing(len(groups), float(own) if np.isfinite(own) else stop_s)
        c = len(nodes)
        for p, members in enumerate(groups):
            sel = mvs[np.isin(net.mv_in[mvs], members)]
            mv_ctrl[sel], mv_phase[sel] = c, p
        nodes.append(int(node))
        cycles.append(cycle)
        offsets.append(float((int(node) * 37) % int(cycle)))
        starts.append(np.arange(len(groups)) * (green + AMBER_S + ALL_RED_S))
        greens.append(np.full(len(groups), green))
    return SignalPlans(np.asarray(nodes, np.int32), np.asarray(cycles), np.asarray(offsets),
                       starts, greens, mv_ctrl, mv_phase)


def plans_json(net: Network, plans: SignalPlans) -> list[dict]:
    """Signal timings for the map: one entry per controller.

    Returns:
        [{"node", "label", "cycle", "offset", "amber",
          "phases": [{"start", "green", "edges": [in-edge ids]}]}].
    """
    out = []
    for c, node in enumerate(plans.nodes.tolist()):
        mvs = np.flatnonzero(plans.mv_ctrl == c)
        phases = [{"start": round(float(plans.starts[c][p]), 2),
                   "green": round(float(plans.greens[c][p]), 2),
                   "edges": sorted({int(net.mv_in[m]) for m in mvs[plans.mv_phase[mvs] == p]})}
                  for p in range(len(plans.starts[c]))]
        out.append({"node": node, "label": net.node_label[node],
                    "cycle": round(float(plans.cycle[c]), 2),
                    "offset": round(float(plans.offset[c]), 2), "amber": AMBER_S,
                    "phases": phases})
    return out


def movement_states(plans: SignalPlans, t: float) -> tuple[np.ndarray, np.ndarray]:
    """Signal state of every movement at time t.

    Args:
        plans: Signal plans.
        t: Simulation time (s).
    Returns:
        (state (M,) int8 of RED / GREEN / AMBER, amber_left (M,) seconds of
        amber still to come, 0 unless the state is AMBER). Unsignalised
        movements are GREEN.
    """
    state = np.full(len(plans.mv_ctrl), GREEN, np.int8)
    left = np.zeros(len(plans.mv_ctrl))
    for c in range(len(plans.nodes)):
        tc = (t + plans.offset[c]) % plans.cycle[c]
        into = tc - plans.starts[c]
        amber_end = plans.greens[c] + AMBER_S
        ctrl_state = np.where(into < 0, RED,
                              np.where(into < plans.greens[c], GREEN,
                                       np.where(into < amber_end, AMBER, RED)))
        sel = plans.mv_ctrl == c
        phase = plans.mv_phase[sel]
        state[sel] = ctrl_state[phase]
        left[sel] = np.where(ctrl_state[phase] == AMBER, (amber_end - into)[phase], 0.0)
    return state, left
