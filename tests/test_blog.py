"""Fifteensquared: finding posts, reading the ways bloggers lay them out, and using them."""
import datetime as dt
import json
import tempfile
import unittest

from crossy import blog, pdf
from crossy.library import Library
from crossy.sources.base import FetchError
from crossy.store import Store
from tests.fakes import FakeSource, make_puzzle
from tests.test_pdf import record


def tiny_puzzle(solutions=False):
    """Five made-up entries with real-shaped answers (positions don't matter to the parser)."""
    def e(label, d, length, sol, seps=None):
        out = {"id": f"{label}-{d}", "label": label, "direction": d, "x": 0, "y": 0, "length": length,
               "clue": "Made-up clue (%d)" % length, "separators": seps or {}}
        if solutions:
            out["solution"] = sol
        return out
    return {
        "id": "guardian-quiptic-1409", "source": "guardian-quiptic", "number": 1409,
        "title": "Guardian Quiptic No 1,409", "setter": "Setter", "date": "2026-11-23",
        "cols": 15, "rows": 15, "solution_available": solutions,
        "entries": [e("1", "across", 7, "BUMPSUP", {",": [5]}), e("5", "across", 7, "MORTALS"),
                    e("9", "across", 5, "NADIR"), e("1", "down", 6, "BANGER"), e("2", "down", 6, "MEDUSA")],
    }


# The layout of a 2026 post: number, blue clue with the definition underlined, bold answer,
# explanation, each separated by <br />.
POST_2026 = """
<p>A fine Quiptic, which 9 people will enjoy.<!--more--></p>
<p><a href="https://example.test/grid.png"><img class="alignnone size-medium" src="https://example.test/grid-300x300.png" /></a></p>
<p><strong>Across</strong></p>
<p>1 <span style="color: #0000ff"><span style="text-decoration: underline">Raises</span> made-up words, more words</span><br />
<strong>BUMPS UP</strong><br />
A charade of BUMP and PUS reversed.</p>
<p>5 <span style="color: #0000ff">Invented clue words with <span style="text-decoration: underline">people</span></span><br />
<strong>MORTALS</strong><br />
A charade of MOR[E] and TAL[E]S.</p>
<p>9 <span style="color: #0000ff"><span style="text-decoration: underline">Lowest part</span> of something</span><br />
<strong>NADIR</strong><br />
(DRAIN)* with &#8216;broken&#8217; as the anagrind.</p>
<p><strong>Down</strong></p>
<p>1<span style="color: #0000ff"> Start of made-up <span style="text-decoration: underline">old car</span></span><br />
<strong>BANGER</strong><br />
A charade of B and ANGER.</p>
<p>2<span style="color: #0000ff"> Amused swimming <span style="text-decoration: underline">jellyfish</span></span><br />
<strong>MEDUSA</strong><br />
(AMUSED)*.</p>
<p>Many thanks to the setter.</p>
"""

# An older layout: answer in red bold at the start of the explanation, after a colon.
POST_OLD = """
<p>Intro.</p>
<p><strong>ACROSS</strong></p>
<p><span style="color: blue">1&nbsp;&nbsp;&nbsp;&nbsp;<span style="text-decoration: underline">Raises</span> words (5,2)</span><br />
<span style="color: #c00000"><strong>BUMPS UP</strong></span> : BUMP + PUS reversed</p>
<p><span style="color: blue">5&nbsp;&nbsp;&nbsp;&nbsp;Words <span style="text-decoration: underline">people</span> (7)</span><br />
<span style="color: #c00000"><strong>MORTALS</strong></span> : MOR[E] + TAL[E]S</p>
<p><span style="color: blue">9&nbsp;&nbsp;&nbsp;&nbsp;<span style="text-decoration: underline">Bottom</span> (5)</span><br />
<span style="color: #c00000"><strong>NADIR</strong></span> : (DRAIN)*</p>
<p><strong>DOWN</strong></p>
<p><span style="color: blue">1&nbsp;&nbsp;&nbsp;&nbsp;<u>Old car</u> (6)</span><br />
<span style="color: #c00000"><strong>BANGER</strong></span> : B + ANGER</p>
<p><span style="color: blue">2&nbsp;&nbsp;&nbsp;&nbsp;<u>Jellyfish</u> (6)</span><br />
<span style="color: #c00000"><strong>MEDUSA</strong></span> : (AMUSED)*</p>
"""

# A table layout with no bold: answer in capitals at the start of the second row.
POST_TABLE = """
<table><tbody>
<tr><td colspan="2"><b>Across</b></td></tr>
<tr><td>1</td><td><u>Raises</u> words (5,2)</td></tr>
<tr><td></td><td>BUMPS UP   BUMP + PUS reversed</td></tr>
<tr><td>5</td><td>Words <u>people</u> (7)</td></tr>
<tr><td></td><td>MORTALS   MOR[E] + TAL[E]S</td></tr>
<tr><td>9</td><td><u>Bottom</u> (5)</td></tr>
<tr><td></td><td>NADIR  (DRAIN)*</td></tr>
<tr><td colspan="2"><b>Down</b></td></tr>
<tr><td>1</td><td><u>Old car</u> (6)</td></tr>
<tr><td></td><td>BANGER  B + ANGER</td></tr>
<tr><td>2</td><td><u>Jellyfish</u> (6)</td></tr>
<tr><td></td><td>MEDUSA  (AMUSED)*</td></tr>
</tbody></table>
"""


class ParseTests(unittest.TestCase):
    def check(self, html):
        got = blog.parse(html, tiny_puzzle())
        self.assertEqual(sorted(got), ["1-across", "1-down", "2-down", "5-across", "9-across"])
        self.assertEqual(got["1-across"]["answer"], "BUMPS UP")
        self.assertIn("BUMP", got["1-across"]["explanation"])
        self.assertIn("PUS reversed", got["1-across"]["explanation"])
        self.assertEqual(got["2-down"]["answer"], "MEDUSA")
        self.assertIn("AMUSED", got["2-down"]["explanation"])
        self.assertIn("people", got["5-across"]["definitions"])
        self.assertNotIn("MORTALS", got["5-across"]["clue"])
        return got

    def test_2026_layout(self):
        got = self.check(POST_2026)
        self.assertEqual(got["9-across"]["explanation"], "(DRAIN)* with ‘broken’ as the anagrind.")
        self.assertEqual(got["1-across"]["clue"], "Raises made-up words, more words")
        self.assertEqual(got["1-across"]["definitions"], ["Raises"])
        self.assertEqual(got["9-across"]["definitions"], ["Lowest part"])
        self.assertEqual(got["1-down"]["clue"], "Start of made-up old car")

    def test_older_layout(self):
        got = self.check(POST_OLD)
        self.assertEqual(got["1-across"]["explanation"], "BUMP + PUS reversed")
        self.assertEqual(got["1-down"]["definitions"], ["Old car"])

    def test_table_layout(self):
        got = self.check(POST_TABLE)
        self.assertEqual(got["9-across"]["explanation"], "(DRAIN)*")

    def test_intro_numbers_are_not_clues(self):
        got = blog.parse(POST_2026, tiny_puzzle())
        self.assertNotIn("people will enjoy", got["9-across"]["clue"])

    def test_nothing_recognisable(self):
        self.assertEqual(blog.parse("<p>Just chat, no clues.</p>", tiny_puzzle()), {})

    def test_generated_full_puzzle(self):
        """Every clue of a 15x15 puzzle, written up in the 2026 layout."""
        puz = make_puzzle("guardian-cryptic", 30175, 15, 15, setter="Setter", split=True)
        parts = []
        for title, d in (("Across", "across"), ("Down", "down")):
            parts.append(f"<p><strong>{title}</strong></p>")
            for e in puz["entries"]:
                if e["direction"] == d:
                    parts.append(f'<p>{e["label"]} <span style="color: #0000ff">Some clue {e["length"]}</span><br />'
                                 f'<strong>{e["solution"]}</strong><br />Explained here.</p>')
        clean = json.loads(json.dumps(puz))
        for e in clean["entries"]:
            del e["solution"]
        got = blog.parse("".join(parts), clean)
        self.assertEqual(len(got), len(puz["entries"]))
        for e in puz["entries"]:
            self.assertEqual(got[e["id"]]["answer"], e["solution"])


class TitleTests(unittest.TestCase):
    def test_matching(self):
        q = {"source": "guardian-quiptic", "number": 1409}
        self.assertTrue(blog.title_matches("Guardian Quiptic 1,409/Harpo", q))
        self.assertTrue(blog.title_matches("Guardian Quiptic 1409 by Harpo", q))
        self.assertFalse(blog.title_matches("Guardian Quiptic 1,410/Harpo", q))
        self.assertFalse(blog.title_matches("Guardian 1,409 / Paul", q))
        c = {"source": "guardian-cryptic", "number": 30175}
        self.assertTrue(blog.title_matches("Guardian Cryptic 30,175 / Paul", c))
        self.assertTrue(blog.title_matches("Guardian 30175 &#8211; Paul", c))
        self.assertFalse(blog.title_matches("Guardian Quiptic 30,175", c))
        qc = {"source": "guardian-quick-cryptic", "number": 138}
        self.assertTrue(blog.title_matches("Guardian Quick Cryptic 138/Setter", qc))
        self.assertFalse(blog.title_matches("Guardian Quick Cryptic 1,138", qc))
        self.assertFalse(blog.supports({"source": "guardian-quick"}))


class FindTests(unittest.TestCase):
    def test_search_and_pick(self):
        seen = []

        def http(url):
            seen.append(url)
            return json.dumps([
                {"link": "https://x.test/old", "date": "2014-08-25T09:00:00",
                 "title": {"rendered": "Guardian Quiptic 771 Moley"}, "content": {"rendered": ""}},
                {"link": "https://x.test/1409", "date": "2026-11-22T10:28:09",
                 "title": {"rendered": "Guardian Quiptic 1,409/Harpo"}, "content": {"rendered": POST_2026},
                 "_embedded": {"author": [{"name": "Blogger"}]}},
            ])
        post = blog.Fifteensquared(http).find({"source": "guardian-quiptic", "number": 1409, "date": "2026-11-16"})
        self.assertEqual(post["url"], "https://x.test/1409")
        self.assertEqual(post["blogger"], "Blogger")
        self.assertEqual(post["title"], "Guardian Quiptic 1,409/Harpo")
        self.assertIn("search=Quiptic+1%2C409", seen[0])

    def test_not_blogged_yet(self):
        b = blog.Fifteensquared(lambda url: "[]")
        self.assertIsNone(b.find({"source": "guardian-quiptic", "number": 1409}))

    def test_bot_check_page(self):
        b = blog.Fifteensquared(lambda url: "<html>One moment, please...</html>")
        with self.assertRaises(FetchError):
            b.find({"source": "guardian-quiptic", "number": 1409})


class ApplyTests(unittest.TestCase):
    def test_answers_come_from_the_blog(self):
        puz = tiny_puzzle(solutions=False)
        blog.apply(puz, {"url": "u", "title": "t"}, blog.parse(POST_2026, puz))
        self.assertTrue(puz["solution_available"])
        self.assertEqual(puz["solution_source"], "Fifteensquared")
        self.assertEqual(puz["entries"][0]["solution"], "BUMPSUP")
        self.assertTrue(blog.has_explanations(puz))

    def test_blog_wins_over_the_guardian(self):
        puz = tiny_puzzle(solutions=True)
        puz["entries"][2]["solution"] = "XXXXX"
        blog.apply(puz, {"url": "u"}, blog.parse(POST_2026, puz))
        self.assertEqual(puz["entries"][2]["solution"], "NADIR")
        self.assertEqual(puz["entries"][2]["guardian_solution"], "XXXXX")

    def test_too_little_parsed_is_ignored(self):
        puz = tiny_puzzle(solutions=False)
        clues = blog.parse(POST_2026, puz)
        blog.apply(puz, {"url": "u"}, {k: clues[k] for k in ["1-across"]})
        self.assertFalse(blog.has_explanations(puz))
        self.assertFalse(puz["solution_available"])
        self.assertEqual(puz["blog"]["url"], "u")


class FakeBlog:
    def __init__(self, post=None, error=None):
        self.post, self.error, self.calls = post, error, 0

    def find(self, puz):
        self.calls += 1
        if self.error:
            raise FetchError(self.error)
        return dict(self.post) if self.post else None


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        puz = tiny_puzzle(solutions=False)
        puz.update(url="https://example.test/q/1409", solution_date="2026-11-24",
                   fetched_at="2026-11-23T00:00:00+00:00", instructions=None)
        self.src = FakeSource("guardian-quiptic", "Guardian Quiptic", {1409: puz}, kind="cryptic")

    def lib(self, fake):
        return Library(Store(self.tmp.name), sources={"guardian-quiptic": self.src},
                       enabled=["guardian-quiptic"], today=dt.date(2026, 11, 23), blog=fake)

    def test_answers_sheet_uses_the_blog(self):
        fake = FakeBlog({"url": "https://x.test/1409", "title": "Guardian Quiptic 1,409/Harpo",
                         "blogger": "Blogger", "html": POST_2026})
        lib = self.lib(fake)
        data, name, notes = lib.sheet(["guardian-quiptic-1409"], answers=True)
        self.assertEqual(notes, [])
        self.assertTrue(name.endswith("_answers.pdf"))
        self.assertTrue(lib.get("guardian-quiptic-1409")["blog"]["clues"])
        self.assertTrue(lib.recent()[0]["explained"])
        lib.sheet(["guardian-quiptic-1409"], answers=True)
        self.assertEqual(fake.calls, 1)                   # found once, then kept

    def test_without_answers_the_blog_is_not_asked(self):
        fake = FakeBlog(None)
        self.lib(fake).sheet(["guardian-quiptic-1409"], answers=False)
        self.assertEqual(fake.calls, 0)

    def test_not_blogged_yet_is_rechecked_later(self):
        fake = FakeBlog(None)
        lib = self.lib(fake)
        _, _, notes = lib.sheet(["guardian-quiptic-1409"], answers=True)
        self.assertEqual(notes, ["Answers aren't published yet for Guardian Quiptic No 1,409."])
        lib.sheet(["guardian-quiptic-1409"], answers=True)
        self.assertEqual(fake.calls, 1)                   # not again within a few hours

    def test_blog_errors_dont_break_the_sheet(self):
        lib = self.lib(FakeBlog(error="bot check"))
        data, _, _ = lib.sheet(["guardian-quiptic-1409"], answers=True)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(lib.get("guardian-quiptic-1409")["blog_error"], "bot check")


class PdfTests(unittest.TestCase):
    def explained_puzzle(self, cols=15, clue_words=9, expl_words=14):
        puz = make_puzzle("guardian-quiptic", 1409, cols, cols, setter="Setter", split=True,
                          clue_words=clue_words)
        clues = {}
        for e in puz["entries"]:
            clue = " ".join(["words"] * clue_words) + " definition here"
            clues[e["id"]] = {"answer": e["solution"], "clue": clue, "definitions": ["definition here"],
                              "explanation": " ".join(["explained"] * expl_words)}
        puz["blog"] = {"name": "Fifteensquared", "url": "https://fifteensquared.net/x/", "blogger": "Blogger",
                       "clues": clues}
        return puz

    def test_explained_page_stays_on_the_page(self):
        puz = self.explained_puzzle()
        _, pages = record([puz], answers=True)
        self.assertEqual(len(pages), 2)                            # puzzle, then explained answers
        for x0, y0, x1, y1, what in pages[1]:
            self.assertGreaterEqual(x0, pdf.MARGIN - 1, what)
            self.assertLessEqual(x1, pdf.PAGE_W - pdf.MARGIN + 1, what)
            self.assertGreaterEqual(y0, pdf.MARGIN - 2, what)
        texts = [w for *_, w in pages[1]]
        self.assertTrue(any("Fifteensquared" in t for t in texts))
        self.assertTrue(any(t.startswith("explained") for t in texts))

    def test_long_write_up_runs_onto_another_page(self):
        puz = self.explained_puzzle(expl_words=80)
        _, pages = record([puz], answers=True)
        self.assertGreaterEqual(len(pages), 3)
        for page in pages[1:]:
            for x0, y0, x1, y1, what in page:
                self.assertGreaterEqual(y0, pdf.MARGIN - 2, what)

    def test_mixed_sheet(self):
        plain = make_puzzle("guardian-quick", 17648, 13, 13, clue_words=3)
        _, pages = record([plain, self.explained_puzzle()], answers=True)
        # puzzles page, half-page answers for the quick, full page for the explained one
        self.assertEqual(len(pages), 3)



class ServerTests(unittest.TestCase):
    def test_puzzle_without_answers_hides_the_write_up(self):
        import server
        puz = tiny_puzzle(solutions=True)
        blog.apply(puz, {"url": "u"}, blog.parse(POST_2026, puz))
        out = server._strip_answers(puz)
        self.assertNotIn("clues", out["blog"])
        self.assertEqual(out["blog"]["url"], "u")
        self.assertTrue(all("solution" not in e for e in out["entries"]))
        self.assertIn("clues", puz["blog"])


if __name__ == "__main__":
    unittest.main()
