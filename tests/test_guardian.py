import html
import json
import unittest

from crossy import sources
from crossy.sources import FetchError, NotFound
from crossy.sources.guardian import GuardianSource, normalise

RAW = {
    "id": "crosswords/quick/100", "number": 100, "name": "Quick crossword No 100",
    "date": 1795737600000, "webPublicationDate": 1795737647000,
    "entries": [
        {"id": "1-across", "number": 1, "humanNumber": "1", "clue": "Made-up <i>clue</i> (3,2)",
         "direction": "across", "length": 5, "group": ["1-across"], "position": {"x": 0, "y": 0},
         "separatorLocations": {",": [3]}, "solution": "ABCDE"},
        {"id": "1-down", "number": 1, "humanNumber": "1", "clue": "Another made-up clue (3)",
         "direction": "down", "length": 3, "group": ["1-down"], "position": {"x": 0, "y": 0},
         "separatorLocations": {}, "solution": "AXY"},
    ],
    "solutionAvailable": True, "dateSolutionAvailable": 1795737600000,
    "dimensions": {"cols": 5, "rows": 3}, "crosswordType": "quick",
}


def page_with(raw, old=False):
    blob = html.escape(json.dumps(raw if old else {"data": raw}), quote=True)
    attr = f'data-crossword-data="{blob}"' if old else f'props="{blob}"'
    tag = "div" if old else "gu-island"
    return f"<html><body><gu-island props=\"{{}}\"></gu-island><{tag} {attr}></{tag}></body></html>"


class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise NotFound(f"{url} doesn't exist (404)")
        return self.pages[url]


class GuardianTests(unittest.TestCase):
    def src(self, pages, slug="quick"):
        return GuardianSource(slug, "Quick crossword" if slug == "quick" else "Quick Cryptic",
                              "Daily", "quick", http_get=FakeHTTP(pages))

    def test_registry(self):
        self.assertIn("guardian-quick", sources.ALL)
        self.assertEqual(sources.ALL["guardian-quick"].name, "Quick crossword")
        for key in ("guardian-quick-cryptic", "guardian-quiptic", "guardian-cryptic", "guardian-prize",
                    "guardian-everyman", "guardian-speedy"):
            self.assertIn(key, sources.ALL)
        self.assertEqual(sources.enabled_keys(), ["guardian-quick", "guardian-quick-cryptic", "guardian-quiptic"])
        src, n = sources.split_id("guardian-quick-cryptic-138")
        self.assertEqual((src.key, n), ("guardian-quick-cryptic", 138))

    def test_normalise(self):
        p = normalise(RAW, sources.ALL["guardian-quick"])
        self.assertEqual(p["id"], "guardian-quick-100")
        self.assertEqual(p["title"], "Quick crossword No 100")
        self.assertIsNone(p["setter"])
        self.assertEqual(p["date"], "2026-11-27")
        self.assertEqual((p["cols"], p["rows"]), (5, 3))
        self.assertTrue(p["solution_available"])
        self.assertEqual(p["entries"][0]["separators"], {",": [3]})
        self.assertEqual(p["entries"][0]["solution"], "ABCDE")

    def test_prize_without_answers(self):
        raw = json.loads(json.dumps(RAW))
        raw["solutionAvailable"] = False
        raw["creator"] = {"name": "Someone"}
        for e in raw["entries"]:
            del e["solution"]
        p = normalise(raw, sources.ALL["guardian-prize"])
        self.assertFalse(p["solution_available"])
        self.assertEqual(p["setter"], "Someone")
        self.assertNotIn("solution", p["entries"][0])

    def test_fetch_current_and_old_page_layouts(self):
        for old in (False, True):
            s = self.src({"https://www.theguardian.com/crosswords/quick/100": page_with(RAW, old)})
            self.assertEqual(s.fetch(100)["number"], 100)

    def test_fetch_without_data_is_a_clear_error(self):
        s = self.src({"https://www.theguardian.com/crosswords/quick/100": "<html>changed</html>"})
        with self.assertRaisesRegex(FetchError, "no puzzle data"):
            s.fetch(100)

    def test_listing_keeps_series_apart(self):
        listing = ('<a href="/crosswords/quick/17648">x</a><img src="https://api.example/crosswords/quick/17648.svg">'
                   '<a href="/crosswords/quick/17647">x</a><a href="/crosswords/quick-cryptic/138">x</a>')
        pages = {"https://www.theguardian.com/crosswords/series/quick": listing,
                 "https://www.theguardian.com/crosswords/series/quick-cryptic": listing}
        self.assertEqual(self.src(pages).list_page(1), [17648, 17647])
        self.assertEqual(self.src(pages, "quick-cryptic").list_page(1), [138])

    def test_listing_past_the_end_is_empty(self):
        self.assertEqual(self.src({}).list_page(9), [])

    def test_empty_first_page_is_an_error(self):
        with self.assertRaises(FetchError):
            self.src({"https://www.theguardian.com/crosswords/series/quick": "<html></html>"}).list_page(1)


if __name__ == "__main__":
    unittest.main()
