"""A small SVG of a puzzle's grid (black and white squares only), for puzzle cards."""
from __future__ import annotations

from .pdf import _white_cells


def grid_svg(puz, cell=8):
    cols, rows = puz["cols"], puz["rows"]
    white, _ = _white_cells(puz)
    w, h = cols * cell, rows * cell
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
             f'role="img" aria-label="{cols} by {rows} grid">',
             f'<rect width="{w}" height="{h}" fill="#111"/>']
    for (x, y) in sorted(white, key=lambda c: (c[1], c[0])):
        parts.append(f'<rect x="{x * cell + 0.5}" y="{y * cell + 0.5}" width="{cell - 1}" height="{cell - 1}" fill="#fff"/>')
    parts.append("</svg>")
    return "".join(parts)
