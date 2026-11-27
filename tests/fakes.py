"""Made-up puzzles (no real puzzle content) in the shapes the Guardian uses, and a fake source."""
import datetime as dt
import json

from crossy.sources.base import FetchError, NotFound, Source

WORDS = ("tangle quietly harbour lantern mosaic orbit velvet canopy drizzle summit pebble "
         "whistle anchor marble saddle tunnel ripple ember falcon meadow").split()


def lattice(cols, rows, split=False):
    """White cells: even rows and even columns (a simple valid grid). split=True blacks out the
    middle row and column so every row and column holds two words (more, shorter clues)."""
    white = set()
    for y in range(rows):
        for x in range(cols):
            if y % 2 == 0 or x % 2 == 0:
                white.add((x, y))
    if split:
        mx, my = cols // 2, rows // 2
        white = {(x, y) for (x, y) in white if x != mx and y != my}
    return white


def entries_for(cols, rows, white, clue_words=6, solutions=True, seed=0):
    starts = []
    for y in range(rows):
        for x in range(cols):
            if (x, y) not in white:
                continue
            a = (x - 1, y) not in white and (x + 1, y) in white
            d = (x, y - 1) not in white and (x, y + 1) in white
            if a or d:
                starts.append((x, y, a, d))
    entries, n = [], 0
    for x, y, a, d in starts:
        n += 1
        for direction, ok in (("across", a), ("down", d)):
            if not ok:
                continue
            length = 0
            while ((x + length, y) if direction == "across" else (x, y + length)) in white:
                length += 1
            words = [WORDS[(seed + n + i) % len(WORDS)] for i in range(clue_words)]
            clue = " ".join(words).capitalize() + f" ({length})"
            e = {"id": f"{n}-{direction}", "label": str(n), "direction": direction, "x": x, "y": y,
                 "length": length, "clue": clue, "separators": {",": [length // 2]} if length > 7 else {}}
            if solutions:
                e["solution"] = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2)[n % 26:n % 26 + length]
            entries.append(e)
    return entries


def make_puzzle(source="guardian-quick", number=1, cols=13, rows=13, date="2026-11-27",
                setter=None, clue_words=6, split=False, solutions=True, solution_date=None,
                instructions=None, title=None, seed=0):
    white = lattice(cols, rows, split)
    return {
        "id": f"{source}-{number}", "source": source, "number": number,
        "title": title or f"Test puzzle No {number:,}", "setter": setter, "date": date,
        "url": f"https://example.test/{source}/{number}", "cols": cols, "rows": rows,
        "entries": entries_for(cols, rows, white, clue_words, solutions, seed),
        "instructions": instructions, "solution_available": solutions,
        "solution_date": solution_date or (date if solutions else None),
        "fetched_at": "2026-11-27T00:00:00+00:00",
    }


TRICKS = ("<b>TODAY’S TRICKS</b> Clues begin or end with a definition of the answer. The rest is one of these:\n"
          "<b>Anagram</b> Letters of a word rearranged, with a hint that they are\n"
          "‘Example clue words here (5)’ gives SAMPLE\n"
          "<b>Hidden word</b> The answer hides inside the words of the clue\n"
          "‘Another made-up example (4)’ gives TEST\n"
          "<b>Insertion</b> One thing goes inside another\n"
          "‘A third invented example (3)’ gives ABC\n"
          "<b>Last letters</b> Final letters spell the answer\n"
          "‘One more invented example (3)’ gives XYZ")


def realistic_set():
    """The shapes that matter: a 13x13 quick, an 11x11 quick cryptic with tricks,
    a 15x15 cryptic with lots of long clues."""
    return [
        make_puzzle("guardian-quick", 17648, 13, 13, clue_words=3),
        make_puzzle("guardian-quick-cryptic", 138, 11, 11, setter="Setter", clue_words=7, instructions=TRICKS),
        make_puzzle("guardian-cryptic", 30175, 15, 15, setter="Setter", clue_words=11, split=True),
        make_puzzle("guardian-quiptic", 1409, 15, 15, setter="Setter", clue_words=9, split=True),
    ]


class FakeSource(Source):
    """A series held in memory. `puzzles` maps number -> puzzle (or an Exception to raise)."""

    def __init__(self, key, name, puzzles, per_page=20, kind="quick"):
        super().__init__(http_get=None)
        self.key, self.name, self.kind, self.short = key, name, kind, key.split("-", 1)[-1]
        self.publication, self.schedule = "Test", "Daily"
        self.puzzles = puzzles
        self.per_page = per_page
        self.fetches = []
        self.listings = []
        self.listing_error = None

    def puzzle_url(self, number):
        return f"https://example.test/{self.key}/{number}"

    def list_page(self, page=1):
        self.listings.append(page)
        if self.listing_error:
            raise FetchError(self.listing_error)
        nums = sorted(self.puzzles, reverse=True)
        return nums[(page - 1) * self.per_page: page * self.per_page]

    def fetch(self, number):
        self.fetches.append(number)
        p = self.puzzles.get(number)
        if p is None:
            raise NotFound(f"{number} doesn't exist (404)")
        if isinstance(p, Exception):
            raise p
        return json.loads(json.dumps(p))


def daily_series(key, name, latest, today, count, every=1, kind="quick", **kw):
    """`count` puzzles, the newest dated `today`, one every `every` days."""
    puzzles = {}
    for i in range(count):
        num = latest - i
        date = (today - dt.timedelta(days=i * every)).isoformat()
        puzzles[num] = make_puzzle(key, num, date=date, title=f"{name} No {num:,}", **kw)
    return FakeSource(key, name, puzzles, kind=kind)
