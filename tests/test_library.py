import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from crossy.library import Library
from crossy.selection import DEFAULT_SOURCES, default_selection
from crossy.sources import FetchError
from crossy.store import Store
from tests.fakes import daily_series, make_puzzle

TODAY = dt.date(2026, 11, 27)


def make_library(root, **kw):
    sources = {
        "guardian-quick": daily_series("guardian-quick", "Guardian Quick crossword", 17648, TODAY, 45),
        "guardian-quick-cryptic": daily_series("guardian-quick-cryptic", "Guardian Quick Cryptic", 138,
                                               TODAY - dt.timedelta(days=6), 25, every=7, kind="cryptic"),
        "guardian-quiptic": daily_series("guardian-quiptic", "Guardian Quiptic", 1409,
                                         TODAY - dt.timedelta(days=4), 25, every=7, kind="cryptic"),
    }
    lib = Library(Store(root), sources=sources, enabled=list(sources), today=TODAY, **kw)
    return lib, sources


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.env = mock.patch.dict(os.environ, {"CROSSY_DATA_DIR": self.root}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_refresh_keeps_about_a_month(self):
        lib, src = make_library(self.root)
        lib.refresh()
        recent = lib.recent()
        quick = [s for s in recent if s["source"] == "guardian-quick"]
        self.assertEqual(len(quick), 32)                  # today and the 31 days before
        self.assertEqual(quick[0]["number"], 17648)
        self.assertEqual(quick[0]["date_label"], "Fri 27 Nov")
        qc = [s for s in recent if s["source"] == "guardian-quick-cryptic"]
        self.assertEqual([s["number"] for s in qc], [138, 137, 136, 135])     # 134 is 34 days old
        # it read a second listing page for the daily series, and stopped once past a month
        self.assertEqual(src["guardian-quick"].listings, [1, 2])
        self.assertLessEqual(len(src["guardian-quick"].fetches), 32 + lib.workers)   # one batch past the month at most
        self.assertLessEqual(len(src["guardian-quick-cryptic"].fetches), 8)

    def test_each_puzzle_is_fetched_once(self):
        lib, src = make_library(self.root)
        lib.refresh()
        first = len(src["guardian-quick"].fetches)
        lib.refresh(force=True)
        self.assertEqual(len(src["guardian-quick"].fetches), first)
        # a new library on the same folder (the site waking up) doesn't fetch again either
        lib2 = Library(Store(self.root), sources=src, enabled=list(src), today=TODAY)
        lib2.refresh(force=True)
        lib2.get("guardian-quick-17648")
        self.assertEqual(len(src["guardian-quick"].fetches), first)
        self.assertEqual(len(lib2.recent()), len(lib.recent()))

    def test_saved_files(self):
        lib, _ = make_library(self.root)
        lib.refresh(["guardian-quick"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "puzzles", "guardian-quick", "17648.json")))
        pdf = os.path.join(self.root, "pdf", "guardian-quick", "17648.pdf")
        self.assertTrue(os.path.exists(pdf))
        with open(pdf, "rb") as f:
            self.assertEqual(lib.puzzle_pdf("guardian-quick-17648"), f.read())

    def test_refresh_is_skipped_when_recent(self):
        lib, src = make_library(self.root)
        lib.refresh()
        lib.refresh()
        self.assertEqual(src["guardian-quick"].listings, [1, 2])

    def test_new_puzzle_is_picked_up(self):
        lib, src = make_library(self.root)
        lib.refresh()
        src["guardian-quick"].puzzles[17649] = make_puzzle("guardian-quick", 17649, date="2026-11-28")
        lib.refresh(force=True)
        self.assertEqual(lib.latest_id("guardian-quick"), "guardian-quick-17649")
        self.assertEqual(src["guardian-quick"].fetches[-1], 17649)

    def test_default_selection_is_quick_and_quick_cryptic(self):
        self.assertEqual(DEFAULT_SOURCES, ["guardian-quick", "guardian-quick-cryptic"])
        lib, _ = make_library(self.root)
        self.assertEqual(default_selection(lib), ["guardian-quick-17648", "guardian-quick-cryptic-138"])

    def test_default_selection_without_refresh_lists_one_page(self):
        lib, src = make_library(self.root)
        self.assertEqual(default_selection(lib, refresh=False),
                         ["guardian-quick-17648", "guardian-quick-cryptic-138"])
        self.assertEqual(src["guardian-quick"].fetches, [])

    def test_sheet_for_a_selection(self):
        lib, _ = make_library(self.root)
        lib.refresh()
        data, name, notes = lib.sheet(["guardian-quick-17640", "guardian-quiptic-1409", "guardian-quick-17648"])
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(name, "crossy_quick17640_quiptic1409_quick17648.pdf")
        self.assertEqual(notes, [])

    def test_sheet_with_answers_notes_unpublished_ones(self):
        lib, src = make_library(self.root)
        src["guardian-quiptic"].puzzles[1410] = make_puzzle(
            "guardian-quiptic", 1410, date="2026-11-27", solutions=False, solution_date="2026-12-04",
            title="Guardian Quiptic No 1,410")
        _, name, notes = lib.sheet(["guardian-quick-17648", "guardian-quiptic-1410"], answers=True)
        self.assertEqual(notes, ["Answers aren't published yet for Guardian Quiptic No 1,410."])
        self.assertTrue(name.endswith("_answers.pdf"))

    def test_answers_are_fetched_again_once_due(self):
        lib, src = make_library(self.root)
        src["guardian-quiptic"].puzzles[1410] = make_puzzle(
            "guardian-quiptic", 1410, date="2026-11-20", solutions=False, solution_date="2026-11-27")
        self.assertFalse(lib.get("guardian-quiptic-1410")["solution_available"])   # fetched today, not out yet
        src["guardian-quiptic"].puzzles[1410] = make_puzzle("guardian-quiptic", 1410, date="2026-11-20")
        lib.get("guardian-quiptic-1410")
        self.assertTrue(lib.get("guardian-quiptic-1410")["solution_available"])
        self.assertEqual(src["guardian-quiptic"].fetches.count(1410), 2)

    def test_a_failed_puzzle_is_a_warning_and_retried(self):
        lib, src = make_library(self.root)
        src["guardian-quick"].puzzles[17647] = FetchError("Quick crossword No 17647: no puzzle data on the page")
        lib.refresh()
        warnings = lib.warnings()
        self.assertTrue(any(w["puzzle"] == "guardian-quick-17647" for w in warnings), warnings)
        self.assertNotIn("guardian-quick-17647", [s["id"] for s in lib.recent()])
        src["guardian-quick"].puzzles[17647] = make_puzzle("guardian-quick", 17647, date="2026-11-26")
        lib.refresh(force=True)
        self.assertFalse(any(w["puzzle"] == "guardian-quick-17647" for w in lib.warnings()))
        self.assertIn("guardian-quick-17647", [s["id"] for s in lib.recent()])

    def test_an_unreachable_series_is_a_warning_and_keeps_saved_puzzles(self):
        lib, src = make_library(self.root)
        lib.refresh()
        before = len(lib.recent())
        src["guardian-quick"].listing_error = "couldn't reach www.theguardian.com (timed out)"
        lib.refresh(force=True)
        msgs = [w["message"] for w in lib.warnings()]
        self.assertIn("Couldn't check for new Guardian Quick crossword puzzles: couldn't reach www.theguardian.com "
                      "(timed out)", msgs)
        self.assertEqual(len(lib.recent()), before)
        self.assertEqual(default_selection(lib), ["guardian-quick-17648", "guardian-quick-cryptic-138"])

    def test_unwritable_store_still_works_and_warns(self):
        lib, src = make_library(self.root)
        blocker = os.path.join(self.root, "blocked")
        open(blocker, "w").close()                # a file where a folder should be
        lib.store.root = blocker
        lib.refresh(["guardian-quick"])
        self.assertTrue(lib.recent())
        self.assertTrue(any("Couldn't save puzzles" in w["message"] for w in lib.warnings()))
        n = len(src["guardian-quick"].fetches)
        lib.get("guardian-quick-17648")           # served from memory, not fetched again
        self.assertEqual(len(src["guardian-quick"].fetches), n)

    def test_not_persistent_on_railway_without_a_volume(self):
        lib, _ = make_library(self.root)
        with mock.patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}):
            os.environ.pop("CROSSY_DATA_DIR", None)
            os.environ.pop("RAILWAY_VOLUME_MOUNT_PATH", None)
            self.assertTrue(any("Railway volume" in w["message"] for w in lib.warnings()))
        with mock.patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production", "RAILWAY_VOLUME_MOUNT_PATH": "/data"}):
            self.assertFalse(any("Railway volume" in w["message"] for w in lib.warnings()))

    def test_older_pages(self):
        lib, src = make_library(self.root)
        items = lib.older("guardian-quick", 3)
        self.assertEqual([s["number"] for s in items][:2], [17608, 17607])
        self.assertEqual(len(items), 5)           # 45 puzzles: page 3 holds the last 5

    def test_downloads_are_remembered(self):
        lib, src = make_library(self.root)
        lib.refresh()
        lib.record_download(["guardian-quick-17648"])
        lib2 = Library(Store(self.root), sources=src, enabled=list(src), today=TODAY)
        by_id = {s["id"]: s for s in lib2.recent()}
        self.assertIsNotNone(by_id["guardian-quick-17648"]["last_downloaded"])
        self.assertIsNone(by_id["guardian-quick-17647"]["last_downloaded"])

    def test_unknown_ids(self):
        lib, _ = make_library(self.root)
        with self.assertRaises(KeyError):
            lib.get("nonsense-12")
        with self.assertRaises(KeyError):
            lib.get("guardian-quick")


if __name__ == "__main__":
    unittest.main()
