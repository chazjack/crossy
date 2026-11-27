"""Lays puzzles out on A4: one puzzle per half page, two per sheet, extra pages as needed.

  build_pdf(puzzles, answers=False) -> PDF bytes

With answers=True, answer pages follow the puzzles (same half-page layout, grids filled in),
for the puzzles whose answers are published. Puzzles without answers are left off those pages.
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from .sources.guardian import clean

PAGE_W, PAGE_H = A4
MARGIN = 11 * mm
SLOT_GAP = 10 * mm            # space between the two halves (the fold line sits in the middle)
HEADER_H = 22                 # points: title line and rule
CLUE_GAP = 6 * mm             # between grid and clues
COL_GAP = 4 * mm              # between two clue columns
TRICKS_MIN_H = 40 * mm        # room kept under the grid for "Today's tricks"
FONT_SIZES = [10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6]
GRID_WIDTHS = [84 * mm, 76 * mm, 68 * mm, 60 * mm, 52 * mm]


class LayoutError(Exception):
    pass


# ---------------------------------------------------------------- geometry
def slot_box(index):
    """(page, x0, y_top, width, height) of the index-th half page, counting from 0."""
    page = index // 2
    h = (PAGE_H - 2 * MARGIN) / 2 - SLOT_GAP / 2
    y_top = PAGE_H - MARGIN if index % 2 == 0 else PAGE_H / 2 - SLOT_GAP / 2
    return page, MARGIN, y_top, PAGE_W - 2 * MARGIN, h


# ---------------------------------------------------------------- text helpers
def wrap(text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def answer_text(entry):
    """'FRENCHMUSTARD' with a ',' break at 6 -> 'FRENCH MUSTARD'; '-' breaks become hyphens."""
    sol = entry.get("solution") or ""
    breaks = {}
    for sep, locs in (entry.get("separators") or {}).items():
        for k in locs:
            breaks[k] = "-" if sep == "-" else " "
    return "".join((breaks.get(i, "") + ch) for i, ch in enumerate(sol))


def date_label(puz, fmt="long"):
    if not puz.get("date"):
        return ""
    d = dt.date.fromisoformat(puz["date"])
    if fmt == "short":
        return f"{d:%a} {d.day} {d:%b}"
    return f"{d:%A} {d.day} {d:%B %Y}"


def has_answers(puz):
    return bool(puz.get("solution_available")) and all(e.get("solution") for e in puz["entries"])


# ---------------------------------------------------------------- layout planning
@dataclass
class ClueLayout:
    size: float
    colw: float
    numw: float
    lead: float
    columns: list = field(default_factory=list)   # each a list of (kind, payload, height)


@dataclass
class Plan:
    cell: float
    grid_w: float
    grid_h: float
    clues: ClueLayout
    tricks: str | None


def _clue_groups(puz, answers):
    groups = []
    for title, direction in (("Across", "across"), ("Down", "down")):
        es = [e for e in puz["entries"] if e["direction"] == direction]
        groups.append((title, [(e["label"], answer_text(e) if answers else clean(e["clue"])) for e in es]))
    return groups


def layout_clues(groups, width, height):
    """The largest font (and 1 or 2 columns) at which the clues fit the box, or None."""
    for size in FONT_SIZES:
        for ncols in (1, 2):
            colw = (width - (ncols - 1) * COL_GAP) / ncols
            if colw < 25 * mm:
                continue
            lead = size * 1.22
            numw = max([stringWidth("00", "Helvetica-Bold", size)] +
                       [stringWidth(label, "Helvetica-Bold", size) for _, cl in groups for label, _ in cl]) + 3
            items = []
            for title, clues in groups:
                items.append(("head", title, lead * 1.35))
                for label, text in clues:
                    lines = wrap(text, "Helvetica", size, colw - numw) or [""]
                    items.append(("clue", (label, lines), lead * len(lines) + size * 0.25))
            if ncols == 1:
                cols_out = [items]
            else:                           # Across in one column, Down in the other
                split = next(i for i, it in enumerate(items) if it[0] == "head" and it[1] == "Down")
                cols_out = [items[:split], items[split:]]
            if all(sum(i[2] for i in col) <= height for col in cols_out):
                return ClueLayout(size, colw, numw, lead, cols_out)
    return None


def plan_puzzle(puz, w, h, answers=False):
    """Choose grid size and clue font so the puzzle fits a w x h box. Raises LayoutError."""
    avail_h = h - HEADER_H
    cols, rows = puz["cols"], puz["rows"]
    tricks = None if answers else (puz.get("instructions") or None)
    groups = _clue_groups(puz, answers)
    widths = [g for g in GRID_WIDTHS if not tricks or g <= 76 * mm]
    for gw in widths:
        cell = min(gw / cols, (avail_h - (TRICKS_MIN_H if tricks else 0)) / rows)
        grid_w, grid_h = cell * cols, cell * rows
        clues = layout_clues(groups, w - grid_w - CLUE_GAP, avail_h)
        if clues:
            return Plan(cell, grid_w, grid_h, clues, tricks)
    raise LayoutError(f"{puz.get('title', 'puzzle')}: the clues don't fit on half a page even at "
                      f"{FONT_SIZES[-1]}pt")


# ---------------------------------------------------------------- drawing
def _white_cells(puz):
    white, numbers = {}, {}
    for e in puz["entries"]:
        x, y = e["x"], e["y"]
        numbers[(x, y)] = e["label"]
        sol = e.get("solution") or ""
        for i in range(e["length"]):
            cell = (x + i, y) if e["direction"] == "across" else (x, y + i)
            white[cell] = sol[i] if i < len(sol) else white.get(cell, "")
    return white, numbers


def draw_grid(c, puz, x0, y_top, cell, letters=False):
    cols, rows = puz["cols"], puz["rows"]
    white, numbers = _white_cells(puz)

    c.setFillGray(0)
    c.rect(x0, y_top - rows * cell, cols * cell, rows * cell, stroke=0, fill=1)
    c.setLineWidth(0.4)
    for (x, y) in white:
        c.setFillGray(1)
        c.rect(x0 + x * cell, y_top - (y + 1) * cell, cell, cell, stroke=1, fill=1)
    c.setFillGray(0)
    c.setFont("Helvetica", cell * 0.30)
    for (x, y), n in numbers.items():
        c.drawString(x0 + x * cell + cell * 0.07, y_top - y * cell - cell * 0.30, n)
    if letters:
        c.setFont("Helvetica-Bold", cell * 0.55)
        for (x, y), ch in white.items():
            if ch:
                c.drawCentredString(x0 + (x + 0.55) * cell, y_top - (y + 0.82) * cell, ch)

    # word separators: "," = thick bar, "-" = short dash
    for e in puz["entries"]:
        x, y = e["x"], e["y"]
        across = e["direction"] == "across"
        for sep, locs in (e.get("separators") or {}).items():
            for k in locs:
                if across:
                    bx, by1, by2 = x0 + (x + k) * cell, y_top - y * cell, y_top - (y + 1) * cell
                    if sep == ",":
                        c.setLineWidth(2.8); c.line(bx, by1, bx, by2)
                    else:
                        c.setLineWidth(1.2); c.line(bx - cell * .18, (by1 + by2) / 2, bx + cell * .18, (by1 + by2) / 2)
                else:
                    by, bx1, bx2 = y_top - (y + k) * cell, x0 + x * cell, x0 + (x + 1) * cell
                    if sep == ",":
                        c.setLineWidth(2.8); c.line(bx1, by, bx2, by)
                    else:
                        c.setLineWidth(1.2); c.line((bx1 + bx2) / 2, by - cell * .18, (bx1 + bx2) / 2, by + cell * .18)
    c.setLineWidth(1.2)
    c.rect(x0, y_top - rows * cell, cols * cell, rows * cell, stroke=1, fill=0)


def draw_clues(c, layout, x0, y_top):
    size, colw, numw, lead = layout.size, layout.colw, layout.numw, layout.lead
    for ci, col in enumerate(layout.columns):
        x, y = x0 + ci * (colw + COL_GAP), y_top
        for kind, payload, h in col:
            if kind == "head":
                c.setFont("Helvetica-Bold", size + 1)
                c.drawString(x, y - size - 1, payload.upper())
            else:
                num, lines = payload
                c.setFont("Helvetica-Bold", size)
                c.drawString(x, y - size, num)
                c.setFont("Helvetica", size)
                for i, ln in enumerate(lines):
                    c.drawString(x + numw, y - size - i * lead, ln)
            y -= h


def draw_tricks(c, text, x0, y_top, width, height):
    """The Quick Cryptic's 'Today's tricks' box (bold labels kept). Never draws below the box."""
    rows = [r.strip() for r in (text or "").split("\n") if r.strip()]
    for size in [8.5, 8, 7.5, 7, 6.5, 6, 5.5]:
        lead, out = size * 1.25, []
        for r in rows:
            m = re.match(r"<b>(.*?)</b>\s*(.*)", r)
            bold, rest = (clean(m.group(1)), clean(m.group(2))) if m else ("", clean(r))
            font = "Helvetica-Oblique" if rest.startswith(("‘", "'")) and not bold else "Helvetica"
            lines = wrap((bold + " " + rest).strip(), font, size, width) or [""]
            out.append((bold, font, lines))
        if sum(len(l) for _, _, l in out) * lead + len(out) * size * 0.15 <= height:
            break
    y, bottom = y_top - size, y_top - height
    for bold, font, lines in out:
        for i, ln in enumerate(lines):
            if y - size * 0.25 < bottom:
                return
            if i == 0 and bold and ln.startswith(bold):
                c.setFont("Helvetica-Bold", size); c.drawString(x0, y, bold)
                bw = stringWidth(bold, "Helvetica-Bold", size)
                c.setFont(font, size); c.drawString(x0 + bw, y, ln[len(bold):])
            else:
                c.setFont(font, size); c.drawString(x0, y, ln)
            y -= lead
        y -= size * 0.15


def draw_puzzle(c, puz, x0, y_top, w, h, answers=False):
    plan = plan_puzzle(puz, w, h, answers)
    title = f"Answers: {puz['title']}" if answers else puz["title"]
    when = date_label(puz)
    byline = f"{puz['setter']}  ·  {when}" if puz.get("setter") else when
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x0, y_top - 13, title)
    c.setFont("Helvetica", 9)
    c.drawRightString(x0 + w, y_top - 12, byline)
    c.setLineWidth(0.6)
    c.line(x0, y_top - 17, x0 + w, y_top - 17)

    top = y_top - HEADER_H
    avail_h = h - HEADER_H
    draw_grid(c, puz, x0, top, plan.cell, letters=answers)
    if plan.tricks:
        draw_tricks(c, plan.tricks, x0, top - plan.grid_h - 4 * mm, plan.grid_w,
                    avail_h - plan.grid_h - 4 * mm)
    draw_clues(c, plan.clues, x0 + plan.grid_w + CLUE_GAP, top)
    return plan


def _fold_line(c):
    c.setStrokeGray(0.7); c.setDash(2, 3); c.setLineWidth(0.5)
    c.line(MARGIN, PAGE_H / 2, PAGE_W - MARGIN, PAGE_H / 2)
    c.setDash(); c.setStrokeGray(0)


def _draw_pages(c, puzzles, answers):
    for i, puz in enumerate(puzzles):
        if i and i % 2 == 0:
            c.showPage()
        _, x0, y_top, w, h = slot_box(i)
        if i % 2 == 1:
            _fold_line(c)
        draw_puzzle(c, puz, x0, y_top, w, h, answers=answers)
    c.showPage()


def sheet_title(puzzles):
    return "Crossy: " + " & ".join(p["title"] for p in puzzles)


def build_pdf(puzzles, answers=False, canvas_factory=None):
    """One PDF for the puzzles, in the order given. Returns bytes.

    answers=True adds answer pages for the puzzles whose answers are published.
    canvas_factory(buffer) lets tests record what gets drawn.
    """
    if not puzzles:
        raise ValueError("no puzzles selected")
    for p in puzzles:                     # fail before drawing anything if one can't fit
        plan_puzzle(p, *slot_box(0)[3:])
    buf = io.BytesIO()
    c = canvas_factory(buf) if canvas_factory else rl_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(sheet_title(puzzles))
    c.setAuthor("Crossy")
    _draw_pages(c, puzzles, answers=False)
    if answers:
        with_answers = [p for p in puzzles if has_answers(p)]
        if with_answers:
            _draw_pages(c, with_answers, answers=True)
    c.save()
    return buf.getvalue()


def page_count(n_puzzles, n_answers=0):
    """How many pages build_pdf makes."""
    return (n_puzzles + 1) // 2 + (n_answers + 1) // 2
