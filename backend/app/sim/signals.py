"""Fixed-time traffic signal plans (Webster cycle length).

Each signalised junction gets one phase per approach direction, which is
the usual practice at Indian junctions. In-edges whose headings differ by
less than APPROACH_ANGLE_DEG share a phase. Green splits follow Webster:

    C = (1.5 L + 5) / (1 - Y),   g_i = (C - L) * y_i / Y

with y_i = approach flow / saturation flow and L the lost time per cycle.
Flows come from the baseline route assignment, so the scenario runs the
*same* signal timings (real signals don't retime when a road closes).

Minor side streets with little expected flow get no phase of their own
(a phase plus intergreen would waste ~15 s of every cycle); they are
"permissive": always allowed, but they yield to all conflicting traffic
like a priority junction's minor arm (see junctions.admit).
"""

from dataclasses import dataclass

import numpy as np

from app.sim.geometry import end_heading
from app.sim.network import SIGNAL, Network

AMBER_S, ALL_RED_S = 3.0, 2.0
MIN_GREEN_S, MIN_CYCLE_S, MAX_CYCLE_S = 10.0, 40.0, 180.0
SAT_FLOW_VPS_PER_LANE = 1800.0 / 3600.0
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


def _webster(ratios: np.ndarray) -> tuple[float, np.ndarray]:
    """Cycle length and greens from per-phase critical flow ratios."""
    lost = len(ratios) * (AMBER_S + ALL_RED_S)
    Y = float(min(ratios.sum(), 0.9))
    cycle = float(np.clip((1.5 * lost + 5.0) / (1.0 - Y), MIN_CYCLE_S, MAX_CYCLE_S))
    share = ratios / ratios.sum() if ratios.sum() > 0 else np.full(len(ratios), 1 / len(ratios))
    greens = np.maximum(MIN_GREEN_S, (cycle - lost) * share)
    return float(greens.sum() + lost), greens


def build_signal_plans(net: Network, edge_flow: np.ndarray) -> SignalPlans:
    """Create a fixed-time plan for every signalised junction.

    Args:
        net: Compiled network.
        edge_flow: (E,) expected flow per edge (veh/s); edges beyond its
            length (scenario additions) count as zero.
    Returns:
        SignalPlans covering all movements.
    """
    M = net.n_movements
    mv_ctrl, mv_phase = np.full(M, -1, np.int32), np.zeros(M, np.int32)
    nodes, cycles, offsets, starts, greens = [], [], [], [], []
    for node in np.flatnonzero(net.node_kind == SIGNAL):
        mvs = np.flatnonzero(net.mv_node == node)
        in_edges = _phased_in_edges(net, sorted({int(net.mv_in[m]) for m in mvs}), edge_flow)
        if len(in_edges) < 2:
            continue
        groups = _approach_groups(net, in_edges)
        flow = np.array([max((edge_flow[e] if e < len(edge_flow) else 0.0)
                             / (SAT_FLOW_VPS_PER_LANE * net.link_lanes[e]) for e in g)
                         for g in groups])
        cycle, g = _webster(flow)
        c = len(nodes)
        for p, members in enumerate(groups):
            sel = mvs[np.isin(net.mv_in[mvs], members)]
            mv_ctrl[sel], mv_phase[sel] = c, p
        nodes.append(int(node))
        cycles.append(cycle)
        offsets.append(float((int(node) * 37) % int(cycle)))
        starts.append(np.concatenate([[0.0], np.cumsum(g + AMBER_S + ALL_RED_S)[:-1]]))
        greens.append(g)
    return SignalPlans(np.asarray(nodes, np.int32), np.asarray(cycles), np.asarray(offsets),
                       starts, greens, mv_ctrl, mv_phase)


def movement_states(plans: SignalPlans, t: float) -> np.ndarray:
    """Signal state of every movement at time t.

    Args:
        plans: Signal plans.
        t: Simulation time (s).
    Returns:
        (M,) int8 of RED / GREEN / AMBER; unsignalised movements are GREEN.
    """
    state = np.full(len(plans.mv_ctrl), GREEN, np.int8)
    for c in range(len(plans.nodes)):
        tc = (t + plans.offset[c]) % plans.cycle[c]
        into = tc - plans.starts[c]
        ctrl_state = np.where(into < 0, RED,
                              np.where(into < plans.greens[c], GREEN,
                                       np.where(into < plans.greens[c] + AMBER_S, AMBER, RED)))
        sel = plans.mv_ctrl == c
        state[sel] = ctrl_state[plans.mv_phase[sel]]
    return state
