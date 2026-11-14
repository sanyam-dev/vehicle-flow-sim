"""Frame recorder: vehicle positions at a fixed rate, written in chunks.

Each frame stores, per active vehicle: id, lon/lat (as float32 offsets
from the area origin, ~1 cm precision), compass heading, type and speed.
Lane positions are drawn with a lateral offset towards the driving side
(lane 0 = kerb lane). The offset uses the agent's eased `lat` value, so a
lane change is a smooth drift, and inside a junction it blends from the
lane the vehicle came from to the lane it is heading for. Frames are
grouped into CHUNK_FRAMES-sized `.npz` files so the API can serve them
piecewise:

    <out_dir>/chunk_00000.npz   t (F,), off (F+1,), vid, dlon, dlat, hdg, vtype, speed
    <out_dir>/meta.json         origin, frame_dt, n_chunks, n_frames
"""

import json
from pathlib import Path

import numpy as np

from app.sim.geometry import LANE_WIDTH_M, side_normal

CHUNK_FRAMES = 60


class FrameRecorder:
    """Buffers frames and flushes them to `out_dir` (no-op if out_dir is None)."""

    def __init__(self, net, out_dir: Path | None, frame_dt: float = 1.0):
        """Prepare lane offsets and the output folder.

        Args:
            net: Network being simulated.
            out_dir: Folder for chunk files, or None to skip recording.
            frame_dt: Seconds between frames.
        """
        self.net, self.out_dir, self.frame_dt = net, out_dir, frame_dt
        self.buf: list[dict] = []
        self.n_chunks = self.n_frames = 0
        E = net.n_edges
        pairs = set(zip(net.link_from[:E].tolist(), net.link_to[:E].tolist(), strict=True))
        two_way = np.array([(int(v), int(u)) in pairs for u, v in
                            zip(net.link_from[:E], net.link_to[:E], strict=True)], dtype=bool)
        lanes = net.link_lanes.astype(float)
        self.cw_offset = np.zeros(net.n_links)
        self.cw_offset[:E] = np.where(two_way, lanes[:E] * LANE_WIDTH_M / 2, 0.0)
        cx, cy = net.node_xy.mean(axis=0)
        lon0, lat0 = net.to_lonlat.transform(cx, cy)
        self.origin = (float(lon0), float(lat0))
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)

    def _lateral(self, ag) -> np.ndarray:
        """Offset (m) of every agent from its link polyline, towards the driving side.

        On a road: carriageway offset + lane offset. On a movement (whose path
        joins the two carriageway centres): a smooth blend from the offset of
        the lane it left to the offset of the lane it will take.
        """
        net, E = self.net, self.net.n_edges
        link, lat, pos = ag.view("link"), ag.view("lat"), ag.view("pos")
        lanes = net.link_lanes.astype(float)
        out = self.cw_offset[link] + (lanes[link] / 2 - lat - 0.5) * LANE_WIDTH_M
        on_mv = np.flatnonzero(link >= E)
        if len(on_mv):
            m = link[on_mv] - E
            a = (lanes[net.mv_in[m]] / 2 - lat[on_mv] - 0.5) * LANE_WIDTH_M
            b = (lanes[net.mv_out[m]] / 2 - ag.view("next_lane")[on_mv] - 0.5) * LANE_WIDTH_M
            u = np.clip(pos[on_mv] / net.link_len[link[on_mv]], 0.0, 1.0)
            out[on_mv] = a + (b - a) * (u * u * (3 - 2 * u))
        return out

    def _positions(self, ag) -> tuple[np.ndarray, np.ndarray]:
        """Projected (x, y) with lane offset, and unit directions of all agents."""
        net = self.net
        xy, d = net.geom.locate(ag.view("link"), ag.view("pos"))
        return xy + side_normal(d, net.driving_side) * self._lateral(ag)[:, None], d

    def capture(self, t: float, ag) -> None:
        """Record one frame of all active vehicles at time t."""
        if self.out_dir is None:
            return
        xy, d = self._positions(ag)
        lon, lat = self.net.to_lonlat.transform(xy[:, 0], xy[:, 1])
        self.buf.append({
            "t": t, "vid": ag.view("vid").astype(np.int32),
            "dlon": (np.asarray(lon) - self.origin[0]).astype(np.float32),
            "dlat": (np.asarray(lat) - self.origin[1]).astype(np.float32),
            "hdg": (np.degrees(np.arctan2(d[:, 0], d[:, 1])) % 360).astype(np.float32),
            "vtype": ag.view("vtype").astype(np.uint8),
            "speed": ag.view("speed").astype(np.float32),
        })
        self.n_frames += 1
        if len(self.buf) >= CHUNK_FRAMES:
            self.flush()

    def flush(self) -> None:
        """Write buffered frames as one chunk file."""
        if self.out_dir is None or not self.buf:
            return
        sizes = [len(f["vid"]) for f in self.buf]
        data = {"t": np.asarray([f["t"] for f in self.buf]),
                "off": np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)}
        for k in ("vid", "dlon", "dlat", "hdg", "vtype", "speed"):
            data[k] = np.concatenate([f[k] for f in self.buf])
        np.savez_compressed(Path(self.out_dir) / f"chunk_{self.n_chunks:05d}.npz", **data)
        self.n_chunks += 1
        self.buf = []

    def close(self) -> None:
        """Flush the last chunk and write meta.json."""
        self.flush()
        if self.out_dir is None:
            return
        meta = {"origin": self.origin, "frame_dt": self.frame_dt,
                "chunk_frames": CHUNK_FRAMES, "n_chunks": self.n_chunks,
                "n_frames": self.n_frames}
        (Path(self.out_dir) / "meta.json").write_text(json.dumps(meta))
