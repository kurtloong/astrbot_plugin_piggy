"""Encode replay frames as an MP4 that QQ can play inline."""

import tempfile
from pathlib import Path

try:
    import av
except ImportError:  # The plugin still works without video; only the replay is skipped.
    av = None

MAX_BYTES = 8 * 1024 * 1024
# Constant-quality first; a bitrate cap is the fallback when the result is too big.
CRF = 28


def available() -> bool:
    return av is not None


def encode(frames, fps: int, crf: int = CRF, bitrate: int | None = None) -> bytes:
    if av is None:
        raise RuntimeError("PyAV is not installed")
    # faststart rewrites the file to put the index first, so it needs a real file.
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "replay.mp4"
        _encode(path, frames, fps, crf, bitrate)
        return path.read_bytes()


def _encode(path: Path, frames, fps: int, crf: int, bitrate: int | None):
    with av.open(str(path), mode="w", format="mp4", options={"movflags": "faststart"}) as container:
        stream = None
        for frame in frames:
            if stream is None:
                stream = container.add_stream("libx264", rate=fps)
                stream.width, stream.height = frame.size
                stream.pix_fmt = "yuv420p"
                stream.options = {"preset": "veryfast", "tune": "animation"}
                if bitrate:
                    stream.bit_rate = bitrate
                else:
                    stream.options["crf"] = str(crf)
            for packet in stream.encode(av.VideoFrame.from_image(frame)):
                container.mux(packet)
        if stream is None:
            raise ValueError("no frames to encode")
        for packet in stream.encode():
            container.mux(packet)


def render(make_frames, fps: int, seconds: float) -> bytes:
    """Encode `make_frames()`; re-encode once at a capped bitrate if over MAX_BYTES."""
    data = encode(make_frames(), fps)
    if len(data) > MAX_BYTES:
        data = encode(make_frames(), fps, bitrate=int(MAX_BYTES * 8 * 0.85 / max(1.0, seconds)))
    return data
