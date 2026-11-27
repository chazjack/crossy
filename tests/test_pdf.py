import io
import unittest

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from crossy import pdf
from tests.fakes import TRICKS, make_puzzle, realistic_set

try:
    import pypdf
except ImportError:          # optional: only used to read the text back
    pypdf = None


class RecordingCanvas(canvas.Canvas):
    """Records the bounding box of everything drawn, per page."""

    def __init__(self, buf):
        super().__init__(buf, pagesize=A4)
        self.pages = [[]]

    def _note(self, x0, y0, x1, y1, what):
        self.pages[-1].append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), what))

    def drawString(self, x, y, text, *a, **k):
        w = stringWidth(text, self._fontname, self._fontsize)
        self._note(x, y - self._fontsize * 0.25, x + w, y + self._fontsize * 0.8, text)
        return super().drawString(x, y, text, *a, **k)

    def drawRightString(self, x, y, text, *a, **k):
        w = stringWidth(text, self._fontname, self._fontsize)
        self._note(x - w, y - self._fontsize * 0.25, x, y + self._fontsize * 0.8, text)
        return super().drawRightString(x, y, text, *a, **k)

    def drawCentredString(self, x, y, text, *a, **k):
        w = stringWidth(text, self._fontname, self._fontsize)
        self._note(x - w / 2, y - self._fontsize * 0.25, x + w / 2, y + self._fontsize * 0.8, text)
        return super().drawCentredString(x, y, text, *a, **k)

    def rect(self, x, y, w, h, *a, **k):
        self._note(x, y, x + w, y + h, "rect")
        return super().rect(x, y, w, h, *a, **k)

    def line(self, x1, y1, x2, y2):
        self._note(x1, y1, x2, y2, "line")
        return super().line(x1, y1, x2, y2)

    def showPage(self):
        super().showPage()
        self.pages.append([])


def record(puzzles, answers=False):
    holder = {}

    def factory(buf):
        holder["c"] = RecordingCanvas(buf)
        return holder["c"]

    data = pdf.build_pdf(puzzles, answers=answers, canvas_factory=factory)
    pages = [p for p in holder["c"].pages if p]
    return data, pages


def pages_in(data):
    return data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page>")


class PageCountTests(unittest.TestCase):
    def test_two_puzzles_fill_one_sheet(self):
        data, pages = record(realistic_set()[:2])
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(len(pages), 1)

    def test_one_puzzle_is_one_page(self):
        _, pages = record(realistic_set()[:1])
        self.assertEqual(len(pages), 1)

    def test_three_puzzles_run_onto_a_second_page(self):
        _, pages = record(realistic_set()[:3])
        self.assertEqual(len(pages), 2)

    def test_answers_add_pages_after_the_puzzles(self):
        _, pages = record(realistic_set()[:3], answers=True)
        self.assertEqual(len(pages), pdf.page_count(3, 3))
        self.assertEqual(len(pages), 4)
        first_answer_page = " ".join(str(item[4]) for item in pages[2])
        self.assertIn("Answers:", first_answer_page)

    def test_answers_off_by_default(self):
        _, pages = record(realistic_set()[:2])
        self.assertNotIn("Answers:", " ".join(str(i[4]) for p in pages for i in p))

    def test_unpublished_answers_are_left_off(self):
        puzzles = [make_puzzle(number=1), make_puzzle(number=2, solutions=False)]
        _, pages = record(puzzles, answers=True)
        self.assertEqual(len(pages), 2)                      # 1 puzzle page + 1 answers page
        answer_titles = [i[4] for i in pages[1] if str(i[4]).startswith("Answers:")]
        self.assertEqual(answer_titles, ["Answers: Test puzzle No 1"])

    def test_no_answers_page_when_none_published(self):
        _, pages = record([make_puzzle(solutions=False)], answers=True)
        self.assertEqual(len(pages), 1)

    def test_empty_selection_is_refused(self):
        with self.assertRaises(ValueError):
            pdf.build_pdf([])

    @unittest.skipIf(pypdf is None, "pypdf not installed")
    def test_page_count_and_text_read_back(self):
        data = pdf.build_pdf(realistic_set(), answers=True)
        reader = pypdf.PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), 4)
        self.assertEqual([round(float(v)) for v in reader.pages[0].mediabox[2:]], [595, 842])
        text = reader.pages[0].extract_text()
        self.assertIn("ACROSS", text)
        self.assertIn("DOWN", text)
        self.assertIn("Test puzzle No 17,648", text)


class LayoutTests(unittest.TestCase):
    """Everything a puzzle draws stays inside its half of the page."""

    def assertInsideSlots(self, puzzles, answers=False):
        _, pages = record(puzzles, answers=answers)
        tol = 0.6
        for pi, items in enumerate(pages):
            slots = [pdf.slot_box(pi * 2), pdf.slot_box(pi * 2 + 1)]
            for (x0, y0, x1, y1, what) in items:
                if what == "line" and abs(y0 - A4[1] / 2) < 0.01 and abs(y1 - A4[1] / 2) < 0.01:
                    continue                                   # the fold line
                ok = any(sx - tol <= x0 and x1 <= sx + sw + tol and sy - sh - tol <= y0 and y1 <= sy + tol
                         for (_, sx, sy, sw, sh) in slots)
                self.assertTrue(ok, f"page {pi + 1}: {what!r} at ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) "
                                    "is outside both halves")

    def test_realistic_puzzles_stay_in_their_halves(self):
        self.assertInsideSlots(realistic_set())

    def test_answer_pages_stay_in_their_halves(self):
        self.assertInsideSlots(realistic_set(), answers=True)

    def test_long_tricks_text_is_clipped_not_overflowing(self):
        long = TRICKS + ("\n<b>Extra</b> " + "more words here " * 30) * 4
        self.assertInsideSlots([make_puzzle(cols=11, rows=11, instructions=long)])

    def test_heavy_cryptic_still_fits_at_a_readable_size(self):
        p = make_puzzle(cols=15, rows=15, clue_words=11, split=True)
        plan = pdf.plan_puzzle(p, *pdf.slot_box(0)[3:])
        self.assertGreaterEqual(plan.clues.size, 6)
        self.assertGreaterEqual(plan.cell, 3.5 * 2.83465)  # cells at least 3.5mm

    def test_quick_crossword_keeps_a_big_grid(self):
        p = realistic_set()[0]
        plan = pdf.plan_puzzle(p, *pdf.slot_box(0)[3:])
        self.assertGreaterEqual(plan.grid_w, 76 * 2.83465)
        self.assertGreaterEqual(plan.clues.size, 9)

    def test_clue_columns_fit_their_height(self):
        for p in realistic_set():
            _, _, _, w, h = pdf.slot_box(0)
            plan = pdf.plan_puzzle(p, w, h)
            for col in plan.clues.columns:
                self.assertLessEqual(sum(i[2] for i in col), h - pdf.HEADER_H + 0.01, p["title"])

    def test_impossible_puzzle_raises_a_clear_error(self):
        p = make_puzzle(cols=15, rows=15, clue_words=80, split=True)
        with self.assertRaises(pdf.LayoutError):
            pdf.build_pdf([p])


class TextTests(unittest.TestCase):
    def test_answer_text_uses_word_breaks(self):
        e = {"solution": "FRENCHMUSTARD", "separators": {",": [6]}}
        self.assertEqual(pdf.answer_text(e), "FRENCH MUSTARD")
        e = {"solution": "SELFMADE", "separators": {"-": [4]}}
        self.assertEqual(pdf.answer_text(e), "SELF-MADE")

    def test_date_labels(self):
        self.assertEqual(pdf.date_label({"date": "2026-11-26"}, "short"), "Thu 26 Nov")
        self.assertEqual(pdf.date_label({"date": "2026-11-26"}), "Thursday 26 November 2026")


if __name__ == "__main__":
    unittest.main()
