"""What every puzzle source provides.

A source is one crossword series (the Guardian Quick crossword, say). To add a publication,
write a Source subclass that can list its recent puzzle numbers and fetch one puzzle into the
normalised shape below, then register its series in crossy/sources/__init__.py.

Normalised puzzle (a plain dict, saved as JSON):

  id                 "<source key>-<number>", e.g. "guardian-quick-17648"
  source             source key, e.g. "guardian-quick"
  number             int
  title              e.g. "Quick crossword No 17,648"
  setter             str or None (the Guardian Quick crossword has no named setter)
  date               publication date, "YYYY-MM-DD"
  url                the puzzle's page
  cols, rows         grid size
  entries            list of {id, label, direction ("across"/"down"), x, y, length, clue,
                     separators ({",": [6], "-": [3]} word/hyphen breaks), solution (str, optional)}
  instructions       optional text with <b>bold</b> labels (the Quick Cryptic's "Today's tricks")
  solution_available bool: answers are published and included
  solution_date      "YYYY-MM-DD" or None: when the answers are (or were) published
  fetched_at         ISO timestamp
"""
from __future__ import annotations

import datetime as dt
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


class FetchError(Exception):
    """A puzzle or listing couldn't be fetched or understood. The message is shown to people."""


class NotFound(FetchError):
    pass


def _retry_after(e, default):
    try:
        return min(30.0, max(1.0, float(e.headers.get("Retry-After"))))
    except (TypeError, ValueError, AttributeError):
        return default


def http_get(url, timeout=20, retries=3, sleep=time.sleep):
    """Fetch a page as text. Retries server errors and timeouts, and waits when the site says
    "too many requests" (429), as the Guardian does if a month of puzzles is fetched at once."""
    last = None
    for attempt in range(retries + 1):
        wait = 1.5 * (attempt + 1)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(f"{url} doesn't exist (404)")
            if e.code == 429:
                last = FetchError(f"the Guardian asked us to slow down (429) at {url}")
                wait = _retry_after(e, 5.0 * (attempt + 1))
            else:
                last = FetchError(f"{url} answered {e.code}")
                if e.code < 500:
                    break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = FetchError(f"couldn't reach {url.split('/')[2]} ({getattr(e, 'reason', e)})")
        if attempt < retries:
            sleep(wait)
    raise last


class Source:
    key = ""            # unique id used in puzzle ids and URLs, e.g. "guardian-quick"
    name = ""           # shown to people, e.g. "Quick crossword"
    publication = ""    # e.g. "The Guardian"
    schedule = ""       # e.g. "Monday to Saturday"
    kind = ""           # "quick" (plain definitions) or "cryptic"
    short = ""          # short tag for file names, e.g. "quick"

    def __init__(self, http_get=http_get):
        self.http_get = http_get

    def list_page(self, page=1):
        """Puzzle numbers on one page of the series listing, newest first. Page 1 is the newest.
        Returns [] past the end."""
        raise NotImplementedError

    def fetch(self, number):
        """One puzzle, normalised (see the module docstring). Raises FetchError."""
        raise NotImplementedError

    def puzzle_url(self, number):
        raise NotImplementedError

    def puzzle_id(self, number):
        return f"{self.key}-{int(number)}"

    def info(self):
        return {"key": self.key, "name": self.name, "publication": self.publication,
                "schedule": self.schedule, "kind": self.kind}

    def __repr__(self):
        return f"<{type(self).__name__} {self.key}>"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
