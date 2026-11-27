"""The Guardian's crosswords.

Each puzzle page embeds the full puzzle (grid, clues and, once published, the answers) as JSON,
so nothing is screenshotted. Series pages (/crosswords/series/<slug>?page=N) list 20 puzzles each.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from html.parser import HTMLParser

from .base import FetchError, Source, http_get, now_iso

BASE = "https://www.theguardian.com"


class _IslandFinder(HTMLParser):
    """Finds the crossword JSON the Guardian embeds in the page."""

    def __init__(self):
        super().__init__()
        self.data = None

    def handle_starttag(self, tag, attrs):
        if self.data:
            return
        a = dict(attrs)
        if tag == "gu-island" and '"entries"' in (a.get("props") or ""):
            self.data = json.loads(a["props"]).get("data")
        elif "data-crossword-data" in a:          # older page layout
            self.data = json.loads(a["data-crossword-data"])


def find_puzzle_json(page_html):
    p = _IslandFinder()
    p.feed(page_html)
    return p.data


def _day(ms):
    if not ms:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()


def normalise(raw, source):
    """Guardian puzzle JSON -> the normalised shape in crossy.sources.base."""
    number = int(raw["number"])
    entries = []
    for e in raw["entries"]:
        item = {
            "id": e["id"],
            "label": str(e.get("humanNumber") or e.get("number")),
            "direction": e["direction"],
            "x": e["position"]["x"],
            "y": e["position"]["y"],
            "length": e["length"],
            "clue": e.get("clue", ""),
            "separators": e.get("separatorLocations") or {},
        }
        if e.get("solution"):
            item["solution"] = e["solution"]
        entries.append(item)
    has_answers = bool(raw.get("solutionAvailable")) and all(e.get("solution") for e in entries)
    return {
        "id": source.puzzle_id(number),
        "source": source.key,
        "number": number,
        "title": f"{source.name} No {number:,}",
        "setter": (raw.get("creator") or {}).get("name") or None,
        "date": _day(raw.get("date")),
        "url": source.puzzle_url(number),
        "cols": raw["dimensions"]["cols"],
        "rows": raw["dimensions"]["rows"],
        "entries": entries,
        "instructions": raw.get("instructions") or None,
        "solution_available": has_answers,
        "solution_date": _day(raw.get("dateSolutionAvailable")),
        "fetched_at": now_iso(),
    }


class GuardianSource(Source):
    publication = "The Guardian"

    def __init__(self, slug, name, schedule, kind, http_get=http_get):
        super().__init__(http_get)
        self.slug = slug
        self.key = f"guardian-{slug}"
        self.name = name
        self.schedule = schedule
        self.kind = kind
        self.short = slug

    def puzzle_url(self, number):
        return f"{BASE}/crosswords/{self.slug}/{int(number)}"

    def list_page(self, page=1):
        url = f"{BASE}/crosswords/series/{self.slug}" + (f"?page={page}" if page > 1 else "")
        try:
            text = self.http_get(url)
        except FetchError as e:
            if page > 1 and "404" in str(e):
                return []
            raise
        # "/crosswords/quick/123" doesn't match "/crosswords/quick-cryptic/123", so series stay apart
        nums = {int(n) for n in re.findall(rf"/crosswords/{re.escape(self.slug)}/(\d+)\b", text)}
        if page == 1 and not nums:
            raise FetchError(f"no {self.name} puzzles on the Guardian's series page "
                             "(the page layout may have changed)")
        return sorted(nums, reverse=True)

    def fetch(self, number):
        text = self.http_get(self.puzzle_url(number))
        try:
            raw = find_puzzle_json(text)
        except (ValueError, KeyError) as e:
            raise FetchError(f"{self.name} No {number}: couldn't read the puzzle data ({e})")
        if not raw or not raw.get("entries"):
            raise FetchError(f"{self.name} No {number}: no puzzle data on the page "
                             "(the Guardian may have changed its page layout)")
        try:
            return normalise(raw, self)
        except (KeyError, TypeError, ValueError) as e:
            raise FetchError(f"{self.name} No {number}: unexpected puzzle data ({e!r})")


def clean(s):
    """Strip tags and entities from clue text."""
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
