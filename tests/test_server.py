import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import server
from tests.test_library import make_library


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"CROSSY_DATA_DIR": cls.tmp.name})
        cls.env.start()
        cls.lib, cls.src = make_library(cls.tmp.name)
        server.LIBRARY = cls.lib
        cls.patches = [mock.patch.object(server, "OPEN", True), mock.patch.object(server, "PASSWORD", "")]
        for p in cls.patches:
            p.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for p in cls.patches:
            p.stop()
        cls.env.stop()
        server.LIBRARY = None
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_health(self):
        self.assertEqual(self.get("/health")[0], 200)

    def test_page_lists_puzzles_with_the_default_ticked(self):
        code, _, body = self.get("/")
        page = body.decode()
        self.assertEqual(code, 200)
        self.assertIn("Ready to download: Guardian Quick crossword (Fri 27 Nov) and Guardian Quick Cryptic "
                      "(Sat 21 Nov).", page)
        self.assertIn('value="guardian-quick-17648" checked', page)
        self.assertIn('value="guardian-quick-cryptic-138" checked', page)
        self.assertIn('value="guardian-quiptic-1409">', page)
        self.assertNotIn('name="quick"', page)              # the old number boxes are gone
        self.assertIn("Include answers", page)
        self.assertIn('title="Answers available">A<', page)
        self.assertNotIn("Open in browser", page)

    def test_failed_puzzle_shows_a_badge_in_its_list(self):
        from crossy.sources import FetchError
        self.src["guardian-quiptic"].puzzles[1410] = FetchError("no puzzle data on the page")
        self.lib.refresh(["guardian-quiptic"], force=True)
        try:
            page = self.get("/")[2].decode()
            self.assertIn("Couldn't fetch<span class=\"meta\">No 1,410</span>", page)
            self.assertIn('class="warntext"', page)
            self.assertIn("No puzzle data on the page", page)
        finally:
            del self.src["guardian-quiptic"].puzzles[1410]
            self.lib.refresh(["guardian-quiptic"], force=True)
        self.assertFalse(any(w["puzzle"] for w in self.lib.warnings()))   # gone from the listing, so no warning

    def test_older_page(self):
        code, _, body = self.get("/?source=guardian-quick&page=2")
        self.assertEqual(code, 200)
        self.assertIn("guardian-quick-17628", body.decode())

    def test_api_puzzles(self):
        code, _, body = self.get("/api/puzzles")
        data = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual([p["id"] for p in data["default"]], ["guardian-quick-17648", "guardian-quick-cryptic-138"])
        self.assertEqual(data["puzzles"][0]["id"], "guardian-quick-17648")
        for key in ("title", "setter", "date", "date_label", "source_name", "solution_available", "last_downloaded"):
            self.assertIn(key, data["puzzles"][0])
        self.assertIsInstance(data["warnings"], list)
        self.assertEqual([s["key"] for s in data["sources"]],
                         ["guardian-quick", "guardian-quick-cryptic", "guardian-quiptic"])

    def test_api_one_puzzle_hides_answers_unless_asked(self):
        _, _, body = self.get("/api/puzzles/guardian-quick-17648")
        self.assertNotIn("solution", json.loads(body)["entries"][0])
        _, _, body = self.get("/api/puzzles/guardian-quick-17648?answers=1")
        self.assertIn("solution", json.loads(body)["entries"][0])

    def test_api_puzzle_pdf_and_preview(self):
        code, headers, body = self.get("/api/puzzles/guardian-quick-17648.pdf")
        self.assertEqual((code, headers["Content-Type"]), (200, "application/pdf"))
        code, headers, body = self.get("/api/puzzles/guardian-quick-17648/grid.svg")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"<svg"))

    def test_api_unknown_puzzle(self):
        self.assertEqual(self.get("/api/puzzles/guardian-quick-99999")[0], 404)
        self.assertEqual(self.get("/api/puzzles/nope-1")[0], 404)

    def test_default_sheet(self):
        code, headers, body = self.get("/sheet.pdf")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertIn('filename="crossy_quick17648_quick-cryptic138.pdf"', headers["Content-Disposition"])
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))

    def test_selected_sheet_with_answers_inline(self):
        code, headers, body = self.get("/sheet.pdf?form=1&p=guardian-quiptic-1409&p=guardian-quick-17640"
                                       "&p=guardian-quick-17641&answers=1&view=1")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Disposition"].startswith("inline"))
        self.assertIn("_answers.pdf", headers["Content-Disposition"])
        _, _, body = self.get("/api/puzzles?source=guardian-quiptic")
        marked = {p["id"]: p["last_downloaded"] for p in json.loads(body)["puzzles"]}
        self.assertIsNotNone(marked["guardian-quiptic-1409"])

    def test_nothing_ticked(self):
        self.assertEqual(self.get("/sheet.pdf?form=1")[0], 400)

    def test_missing_puzzle_says_so(self):
        code, _, body = self.get("/sheet.pdf?p=guardian-quick-99999")
        self.assertEqual(code, 404)
        self.assertIn("doesn&#x27;t exist (yet)", body.decode())
        self.assertEqual(self.get("/sheet.pdf?p=whatever-1")[0], 404)


class LockTests(unittest.TestCase):
    def test_locked_without_password_or_open_access(self):
        with mock.patch.object(server, "OPEN", False), mock.patch.object(server, "PASSWORD", ""):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/", timeout=10)
                self.fail("should be locked")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 503)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
