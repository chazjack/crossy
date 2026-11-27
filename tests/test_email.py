import datetime as dt
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import server
from crossy import emailer
from tests.test_library import make_library

UK = emailer.UK


def at(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UK)


class ScheduleTests(unittest.TestCase):
    def settings(self, **kw):
        s = emailer.clean({**emailer.defaults(), "recipients": ["me@example.com"], **kw})
        return s

    def test_due_on_the_day_within_the_window(self):
        s = self.settings(day=4, time="06:30")                     # Fridays 6:30
        self.assertEqual(emailer.due(s, at(2026, 12, 4, 6, 30)), (True, ""))
        self.assertTrue(emailer.due(s, at(2026, 12, 4, 7, 45))[0])  # a late cron run still sends
        self.assertFalse(emailer.due(s, at(2026, 12, 4, 6, 0))[0])  # too early
        self.assertFalse(emailer.due(s, at(2026, 12, 4, 8, 0))[0])  # too late
        self.assertFalse(emailer.due(s, at(2026, 12, 3, 6, 30))[0])  # Thursday

    def test_not_twice_a_day_and_not_when_off_or_empty(self):
        s = self.settings(last_sent={"date": "2026-12-04"})
        self.assertEqual(emailer.due(s, at(2026, 12, 4, 6, 30)), (False, "it has already gone today"))
        self.assertFalse(emailer.due(self.settings(enabled=False), at(2026, 12, 4, 6, 30))[0])
        self.assertFalse(emailer.due(self.settings(sources=[]), at(2026, 12, 4, 6, 30))[0])
        self.assertFalse(emailer.due(self.settings(recipients=[]), at(2026, 12, 4, 6, 30))[0])

    def test_next_send(self):
        s = self.settings(day=4, time="06:30")
        self.assertEqual(emailer.next_send(s, at(2026, 11, 27, 12, 0)), at(2026, 12, 4, 6, 30))
        self.assertEqual(emailer.next_send(s, at(2026, 12, 4, 6, 0)), at(2026, 12, 4, 6, 30))
        s["last_sent"] = {"date": "2026-12-04"}
        self.assertEqual(emailer.next_send(s, at(2026, 12, 4, 6, 45)), at(2026, 12, 11, 6, 30))
        self.assertIsNone(emailer.next_send(self.settings(enabled=False)))

    def test_summary_and_subject(self):
        self.assertEqual(emailer.summary(self.settings(day=4, time="06:30")), "Fridays, 6:30am")
        self.assertEqual(emailer.summary(self.settings(enabled=False)), "Off")
        self.assertEqual(emailer.subject([{"title": "Guardian Quick crossword No 17,648"},
                                          {"title": "Guardian Quick Cryptic No 138"}]),
                         "Quick crossword 17,648 & Quick Cryptic 138")

    def test_clean_puts_damage_right(self):
        s = emailer.clean({"enabled": 1, "day": 9, "time": "03:00", "sources": ["guardian-quick", "nope", 5],
                           "recipients": ["ok@example.com", "not an email", "ok@example.com"], "last_sent": "x"},
                          known_sources={"guardian-quick"})
        self.assertEqual((s["enabled"], s["day"], s["time"], s["sources"], s["recipients"], s["last_sent"]),
                         (True, 4, "06:30", ["guardian-quick"], ["ok@example.com"], None))

    def test_form(self):
        cur = self.settings(recipients=["a@example.com", "b@example.com"])
        form = {"enabled": ["1"], "day": ["5"], "time": ["08:00"], "source": ["guardian-quiptic", "guardian-quick"],
                "answers": ["1"], "remove": ["a@example.com"], "new_address": ["c@example.com"]}
        new, problem = emailer.from_form(cur, form, ["guardian-quick", "guardian-quick-cryptic", "guardian-quiptic"])
        self.assertIsNone(problem)
        self.assertEqual((new["enabled"], new["day"], new["time"], new["sources"], new["answers"], new["recipients"]),
                         (True, 5, "08:00", ["guardian-quick", "guardian-quiptic"], True,
                          ["b@example.com", "c@example.com"]))
        new, problem = emailer.from_form(cur, {"new_address": ["nope"]}, [])
        self.assertIn("doesn't look like an email address", problem)
        self.assertFalse(new["enabled"])            # an unticked switch sends nothing


class SettingsServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"CROSSY_DATA_DIR": cls.tmp.name, "EMAIL_TO": "me@example.com"})
        cls.env.start()
        cls.lib, cls.src = make_library(cls.tmp.name)
        server.LIBRARY = cls.lib
        cls.patches = [mock.patch.object(server, "OPEN", True), mock.patch.object(server, "PASSWORD", ""),
                       mock.patch.object(server, "CRON_TOKEN", "sekrit")]
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

    def setUp(self):
        try:
            os.remove(emailer.path(self.lib.store))
        except FileNotFoundError:
            pass
        server.LAST_TEST = 0.0

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def req(self, path, data=None, headers=None):
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers=headers or {},
                                   method="POST" if data is not None else "GET")
        opener = urllib.request.build_opener(self.NoRedirect)
        try:
            with opener.open(r, timeout=20) as resp:
                return resp.status, resp.headers, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def form(self, **kw):
        base = {"enabled": "1", "day": "4", "time": "06:30", "source": ["guardian-quick", "guardian-quick-cryptic"],
                "action": "save"}
        base.update(kw)
        return base

    def test_home_links_to_settings(self):
        page = self.req("/")[2]
        self.assertIn('<a class="emailstrip" href="/settings">', page)
        self.assertIn("<strong>Weekly email:</strong> Fridays, 6:30am", page)
        self.assertIn("@view-transition", page)

    def test_settings_page_shows_defaults(self):
        code, _, page = self.req("/settings")
        self.assertEqual(code, 200)
        self.assertIn('name="enabled" value="1" aria-label="Weekly Crossy email" checked', page)
        self.assertIn('<input type="radio" name="day" value="4" checked>', page)
        self.assertIn('<option value="06:30" selected>6:30am</option>', page)
        self.assertIn('value="guardian-quick" checked', page)
        self.assertIn('value="guardian-quick-cryptic" checked', page)
        self.assertNotIn('value="guardian-quiptic" checked', page)
        self.assertIn("me@example.com", page)               # EMAIL_TO seeds the list
        self.assertIn('placeholder="Add an email address"', page)

    def test_save_add_and_remove(self):
        code, headers, _ = self.req("/settings", self.form(day="5", time="08:00", answers="1",
                                                           new_address="printer@example.com", action="add"))
        self.assertEqual((code, headers["Location"]), (303, "/settings"))
        st = emailer.load(self.lib.store)
        self.assertEqual((st["day"], st["time"], st["answers"], st["recipients"]),
                         (5, "08:00", True, ["me@example.com", "printer@example.com"]))
        self.assertIn("Saturdays, 8:00am", self.req("/")[2])
        self.req("/settings", self.form(remove="me@example.com"))
        self.assertEqual(emailer.load(self.lib.store)["recipients"], ["printer@example.com"])
        code, _, page = self.req("/settings", self.form(new_address="bad", action="add"))
        self.assertEqual(code, 400)
        self.assertIn("doesn&#x27;t look like an email address", page)
        # Enter in the address box adds it (the form's first submit button), never removes one
        self.assertLess(page.index('value="add" tabindex="-1"'), page.index('name="remove"'))

    def test_send_a_test(self):
        with mock.patch.object(emailer, "send", return_value={"id": "x"}) as send:
            code, headers, _ = self.req("/settings", self.form(action="test"))
            self.assertEqual((code, headers["Location"]), (303, "/settings?done=sent"))
            to, subject, text, (name, pdf) = send.call_args[0]
            self.assertEqual((to, subject), (["me@example.com"], "Quick crossword 17,648 & Quick Cryptic 138"))
            self.assertTrue(pdf.startswith(b"%PDF"))
            self.assertIsNone(emailer.load(self.lib.store)["last_sent"])   # a test doesn't use up the day
            self.assertEqual(self.req("/settings", self.form(action="test"))[0], 429)   # one a minute

    def test_run_needs_the_token_and_sends_once(self):
        self.assertEqual(self.req("/api/email/run", {})[0], 403)
        self.assertEqual(self.req("/api/email/run", {}, {"X-Crossy-Token": "wrong"})[0], 403)
        friday = at(2026, 12, 4, 6, 30)
        with mock.patch.object(emailer, "send", return_value={"id": "x"}) as send, \
                mock.patch.object(emailer, "now_uk", return_value=friday):
            code, _, body = self.req("/api/email/run", {}, {"X-Crossy-Token": "sekrit"})
            res = json.loads(body)
            self.assertEqual((code, res["sent"], res["to"]), (200, True, ["me@example.com"]))
            self.assertEqual(send.call_count, 1)
            res = json.loads(self.req("/api/email/run", {}, {"X-Crossy-Token": "sekrit"})[2])
            self.assertEqual((res["sent"], res["reason"]), (False, "it has already gone today"))
            self.assertEqual(send.call_count, 1)
        self.assertIn("Last sent Fri 4 Dec, 6:30am", self.req("/settings")[2])

    def test_run_off_day_does_nothing(self):
        with mock.patch.object(emailer, "send") as send, \
                mock.patch.object(emailer, "now_uk", return_value=at(2026, 12, 2, 6, 30)):
            res = json.loads(self.req("/api/email/run", {}, {"X-Crossy-Token": "sekrit"})[2])
        self.assertEqual((res["sent"], res["reason"]), (False, "it goes on Fridays"))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
