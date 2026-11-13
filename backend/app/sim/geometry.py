"""Polyline helpers and a vectorised geometry table for all links.

All coordinates are projected metres (UTM). A "link" is either a road
edge or a junction movement; each has one polyline. The GeomTable packs
every polyline into flat arrays so that thousands of (link, s) positions
can be turned into (x, y, heading) with a single `searchsorted`.
"""

import numpy as np

LANE_WIDTH_M = 3.2


def polyline_length(pts: np.ndarray) -> float:
    """Total length of a polyline.

    Args:
        pts: (K, 2) array of points.
    """
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


def _cumlen(pts: np.ndarray) -> np.ndarray:
    """Cumulative distance at each vertex, starting at 0."""
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])


def point_at(pts: np.ndarray, d: float) -> tuple[np.ndarray, np.ndarray]:
    """Point and unit direction at distance `d` along a polyline.

    Args:
        pts: (K, 2) polyline.
        d: Distance from the start (clamped to [0, length]).
    Returns:
        (point (2,), unit direction (2,)).
    """
    cl = _cumlen(pts)
    d = float(np.clip(d, 0.0, cl[-1]))
    i = int(np.clip(np.searchsorted(cl, d, side="right") - 1, 0, len(pts) - 2))
    seg = pts[i + 1] - pts[i]
    seg_len = max(float(np.linalg.norm(seg)), 1e-9)
    t = (d - cl[i]) / seg_len
    return pts[i] + t * seg, seg / seg_len


def substring(pts: np.ndarray, d0: float, d1: float) -> np.ndarray:
    """Part of a polyline between distances d0 < d1.

    Args:
        pts: (K, 2) polyline.
        d0: Start distance.
        d1: End distance.
    Returns:
        (K', 2) polyline with at least two points.
    """
    cl = _cumlen(pts)
    p0, _ = point_at(pts, d0)
    p1, _ = point_at(pts, d1)
    inner = pts[(cl > d0) & (cl < d1)]
    return np.vstack([p0, inner, p1])


def drop_end_hooks(pts: np.ndarray, tol: float = 3.0) -> np.ndarray:
    """Remove vertices within `tol` metres of either end point.

    Intersection consolidation leaves short "hooks" where a road's geometry
    is extended to the cluster centroid; their direction can be reversed and
    would corrupt turn and heading calculations.

    Args:
        pts: (K, 2) polyline.
        tol: Distance (m) under which a near-end vertex is dropped.
    Returns:
        Polyline with the same end points and at least two points.
    """
    keep = np.ones(len(pts), dtype=bool)
    near_start = np.linalg.norm(pts - pts[0], axis=1) < tol
    near_end = np.linalg.norm(pts - pts[-1], axis=1) < tol
    keep[1:-1] = ~(near_start[1:-1] | near_end[1:-1])
    return pts[keep]


def end_heading(pts: np.ndarray, at_end: bool, chord: float = 10.0) -> np.ndarray:
    """Unit heading over the first/last `chord` metres of a polyline.

    Args:
        pts: (K, 2) polyline.
        at_end: True for the heading arriving at the last point,
            False for the heading leaving the first point.
        chord: Chord length (m); shorter polylines use their full length.
    Returns:
        (2,) unit vector.
    """
    length = polyline_length(pts)
    c = min(chord, length)
    if at_end:
        a, _ = point_at(pts, length - c)
        d = pts[-1] - a
    else:
        b, _ = point_at(pts, c)
        d = b - pts[0]
    return d / max(float(np.linalg.norm(d)), 1e-9)


def side_normal(direction: np.ndarray, driving_side: str) -> np.ndarray:
    """Unit normal pointing to the driving side of travel.

    Args:
        direction: (..., 2) unit direction vectors.
        driving_side: "left" or "right".
    Returns:
        (..., 2) unit normals (left normal for left-hand traffic).
    """
    dx, dy = direction[..., 0], direction[..., 1]
    if driving_side == "left":
        return np.stack([-dy, dx], axis=-1)
    return np.stack([dy, -dx], axis=-1)


def bezier(p0: np.ndarray, c: np.ndarray, p1: np.ndarray, n: int = 9) -> np.ndarray:
    """Quadratic Bezier curve sampled at `n` points (used for turn paths)."""
    t = np.linspace(0.0, 1.0, n)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * c + t**2 * p1


class GeomTable:
    """Flat, vectorised storage of every link polyline.

    Each link occupies the global-distance interval [g0[l], g0[l] + len[l]];
    intervals are separated by a gap so a lookup never leaks into a
    neighbouring link.
    """

    GAP = 10.0

    def __init__(self, polylines: list[np.ndarray]):
        """Pack polylines.

        Args:
            polylines: One (K, 2) array per link, in link-index order.
        """
        pts, gd, g0, first = [], [], [], []
        offset, n_pts = 0.0, 0
        for p in polylines:
            cl = _cumlen(p)
            g0.append(offset)
            first.append(n_pts)
            pts.append(p)
            gd.append(cl + offset)
            offset += cl[-1] + self.GAP
            n_pts += len(p)
        self.pts = np.vstack(pts)
        self.gd = np.concatenate(gd)
        self.g0 = np.asarray(g0)
        self.first = np.asarray(first)
        # index of the last *segment start* of each link
        self.last_seg = np.append(self.first[1:], n_pts) - 2

    def polyline(self, link: int) -> np.ndarray:
        """The (K, 2) polyline of one link."""
        return self.pts[self.first[link]: self.last_seg[link] + 2]

    def locate(self, link: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Positions and unit directions for many (link, s) pairs.

        Args:
            link: (n,) link indices.
            s: (n,) distances along each link.
        Returns:
            (xy (n, 2), direction (n, 2)).
        """
        g = self.g0[link] + np.maximum(s, 0.0)
        i = np.searchsorted(self.gd, g, side="right") - 1
        i = np.clip(i, self.first[link], self.last_seg[link])
        seg = self.pts[i + 1] - self.pts[i]
        seg_len = np.maximum(self.gd[i + 1] - self.gd[i], 1e-9)
        t = np.clip((g - self.gd[i]) / seg_len, 0.0, 1.0)[:, None]
        direction = seg / seg_len[:, None]
        # Zero-length segments (vertex duplicates / link gaps) get a dummy direction.
        bad = ~np.isfinite(direction).all(axis=1) | (np.abs(direction).sum(axis=1) < 1e-6)
        direction[bad] = (1.0, 0.0)
        return self.pts[i] + t * seg, direction
