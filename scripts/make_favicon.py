#!/usr/bin/env python
"""Generate the favicon set from the AXIOMA logo mark.

The artwork is the one inlined in app/static/index.html: a diamond (the market
instrument) with an ascending bar pair behind it, stroked with a blue-to-green
gradient.

Browsers get favicon.svg, which is the same vector art and stays crisp. The PNG
and ICO fallbacks are for older browsers, bookmark bars and iOS home screens,
which need real rasters at fixed sizes.

Run:  python scripts/make_favicon.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

# Geometry lifted from the inline SVG in index.html (viewBox 0 0 40 40).
VIEW = 40.0
CENTRE = VIEW / 2
DIAMOND_R = 17.0            # |dx| + |dy| <= 17 spans the diamond's vertices
DIAMOND_HALF_STROKE = 1.25  # stroke-width 2.5
BAR_HALF_STROKE = 1.5       # stroke-width 3
BARS = [((14.0, 19.0), (14.0, 25.0)), ((20.0, 14.0), (20.0, 25.0))]

GRAD_FROM = (0x4F, 0x9D, 0xFF)   # #4f9dff
GRAD_TO = (0x22, 0xD3, 0xA7)     # #22d3a7

# Supersampling factor per axis: 4 hides the staircase on the diamond edges
# without making generation slow.
SS = 4


def gradient(t: float) -> tuple[int, int, int]:
    """Linear gradient along the diagonal, matching the SVG x1,y1 -> x2,y2."""
    t = min(max(t, 0.0), 1.0)
    return tuple(round(a + (b - a) * t) for a, b in zip(GRAD_FROM, GRAD_TO))


def distance_to_segment(px, py, a, b) -> float:
    """Shortest distance from a point to a line segment."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    # Project onto the segment, clamped to its endpoints.
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = min(max(t, 0.0), 1.0)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def covered(x: float, y: float) -> bool:
    """True where the artwork is painted (inside a stroke of either shape)."""
    # Diamond outline: distance to its edge, from the L1 norm scaled by sqrt(2).
    l1 = abs(x - CENTRE) + abs(y - CENTRE)
    if abs(DIAMOND_R - l1) / math.sqrt(2) <= DIAMOND_HALF_STROKE:
        return True
    return any(distance_to_segment(x, y, a, b) <= BAR_HALF_STROKE for a, b in BARS)


def render_rgba(size: int) -> bytes:
    """Rasterise the mark at `size` px square, returning PNG scanline data."""
    step = VIEW / (size * SS)
    # Samples taken per output pixel - NOT size * SS * SS, which is the whole
    # canvas and would drive every alpha to zero.
    total = SS * SS
    rows = bytearray()
    for py in range(size):
        rows.append(0)  # PNG filter byte: 0 = None
        for px in range(size):
            hits = 0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * step
                for sx in range(SS):
                    if covered((px * SS + sx + 0.5) * step, y):
                        hits += 1
            if not hits:
                rows += b"\x00\x00\x00\x00"
                continue
            # Colour from the gradient at the pixel centre.
            r, g, b = gradient(((px + 0.5) + (py + 0.5)) / (2 * size))
            rows += bytes((r, g, b, round(255 * hits / total)))
    return bytes(rows)


def png_bytes(size: int) -> bytes:
    """A minimal, valid 8-bit RGBA PNG of the mark."""
    raw = render_rgba(size)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    # Fixed compression level keeps output byte-for-byte reproducible.
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_bytes(pngs: dict) -> bytes:
    """Bundle PNG frames into an .ico container.

    Vista and later read PNG-compressed ICO entries directly, so the frames are
    embedded as-is rather than converted to BMP.
    """
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in sorted(pngs.items()):
        # 256 is stored as 0: the directory field is a single byte.
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)
    return struct.pack("<HHH", 0, 1, len(pngs)) + entries + blobs


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"
     role="img" aria-label="AXIOMA">
  <title>AXIOMA</title>
  <defs>
    <linearGradient id="ax-g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#4f9dff"/>
      <stop offset="100%" stop-color="#22d3a7"/>
    </linearGradient>
  </defs>
  <path d="M20 3 L33 20 L20 37 L7 20 Z" fill="none"
        stroke="url(#ax-g)" stroke-width="2.5" stroke-linejoin="round"/>
  <path d="M14 25 L14 19 M20 25 L20 14" fill="none"
        stroke="url(#ax-g)" stroke-width="3" stroke-linecap="round"/>
</svg>
"""


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "favicon.svg").write_text(SVG, encoding="utf-8")
    print("wrote favicon.svg")

    frames = {size: png_bytes(size) for size in (16, 32, 48, 64, 128, 256)}
    (STATIC / "favicon.ico").write_bytes(ico_bytes(frames))
    print(f"wrote favicon.ico ({len(frames)} sizes)")

    for size in (32, 180, 512):
        path = STATIC / f"icon-{size}.png"
        path.write_bytes(frames.get(size) or png_bytes(size))
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()