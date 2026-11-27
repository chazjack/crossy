"""Fifteensquared (fifteensquared.net): a blog that solves and explains each Guardian cryptic.

    blog = Fifteensquared()
    post = blog.find(puz)            # {"url", "title", "date", "blogger", "html"} or None
    clues = parse(post["html"], puz) # {entry id: {"answer", "clue", "definitions", "explanation"}}
    apply(puz, post, clues)          # saves them on the puzzle (and uses its answers)

Posts are found through the blog's WordPress search API and matched on the title, which names
the series and number (e.g. "Guardian Quiptic 1,409/Harpo"). Bloggers lay posts out in
different ways (paragraphs, tables), so the parser leans on what the puzzle already tells us:
the clue numbers in each direction and each answer's length.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from html.parser import HTMLParser

from .sources.base import FetchError, http_get, now_iso

NAME = "Fifteensquared"
SITE = "https://fifteensquared.net"
API = SITE + "/wp-json/wp/v2/posts"
MIN_PARSED = 0.6              # share of clues a post must explain before we use it

# What a post title must (and mustn't) say for each series. The blog covers the cryptics only.
_SERIES = {
    "guardian-quiptic": (["quiptic"], []),
    "guardian-quick-cryptic": (["quick cryptic"], []),
    "guardian-cryptic": (["guardian"], ["quiptic", "quick", "everyman", "speedy", "genius"]),
    "guardian-prize": (["guardian"], ["quiptic", "quick", "everyman", "speedy", "genius"]),
    "guardian-everyman": (["everyman"], []),
}
_SEARCH_WORDS = {
    "guardian-quiptic": "Quiptic", "guardian-quick-cryptic": "Quick Cryptic",
    "guardian-cryptic": "Guardian", "guardian-prize": "Guardian",
    "guardian-everyman": "Everyman",
}


def supports(puz):
    return puz.get("source") in _SERIES


def _letters(s):
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def title_matches(title, puz):
    t = html.unescape(re.sub(r"<[^>]+>", "", title or "")).lower()
    need, avoid = _SERIES.get(puz.get("source"), ([], []))
    if not all(w in t for w in need) or any(w in t for w in avoid):
        return False
    nums = {int(n.replace(",", "")) for n in re.findall(r"(?<![\d,])(\d{1,3}(?:,\d{3})+|\d+)(?![\d,])", t)}
    return int(puz["number"]) in nums


class Fifteensquared:
    name = NAME

    def __init__(self, http_get=http_get):
        self.http_get = http_get

    def _search(self, query):
        url = API + "?" + urllib.parse.urlencode({"search": query, "per_page": 10, "_embed": "author"})
        text = self.http_get(url)
        try:
            data = json.loads(text)
        except ValueError:
            raise FetchError("Fifteensquared didn't answer its search (it may be checking for bots); "
                             "will try again later")
        if not isinstance(data, list):
            raise FetchError("Fifteensquared's search answered in an unexpected way")
        return data

    def find(self, puz):
        """The post for this puzzle, or None if it hasn't been blogged (yet).
        Tries the blog's search API first; if that's refused (the site sometimes answers
        automated requests with a bot check), falls back to its search page and the post page."""
        if not supports(puz):
            return None
        n = int(puz["number"])
        word = _SEARCH_WORDS[puz["source"]]
        queries = [f"{word} {n:,}"] + ([f"{word} {n}"] if n >= 1000 else [])
        try:
            return self._find_api(puz, queries)
        except FetchError as api_error:
            try:
                return self._find_html(puz, queries)
            except FetchError:
                raise api_error

    def _find_api(self, puz, queries):
        for q in queries:
            for post in self._search(q):
                title = (post.get("title") or {}).get("rendered", "")
                if not title_matches(title, puz):
                    continue
                if puz.get("date") and (post.get("date") or "")[:10] < puz["date"]:
                    continue                  # an older puzzle with the same number in another series
                authors = (post.get("_embedded") or {}).get("author") or [{}]
                return {
                    "url": post.get("link"),
                    "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                    "date": (post.get("date") or "")[:10] or None,
                    "blogger": (authors[0] or {}).get("name") or None,
                    "html": (post.get("content") or {}).get("rendered", ""),
                }
        return None

    def _find_html(self, puz, queries):
        for q in queries:
            page = self.http_get(SITE + "/?" + urllib.parse.urlencode({"s": q}))
            if _is_bot_check(page):
                raise FetchError("Fifteensquared asked us to wait (bot check); will try again later")
            for link, title in _post_links(page):
                date = "-".join(re.search(r"/(\d{4})/(\d\d)/(\d\d)/", link).groups())
                if not title_matches(title, puz) or (puz.get("date") and date < puz["date"]):
                    continue
                body = self.http_get(link)
                if _is_bot_check(body):
                    raise FetchError("Fifteensquared asked us to wait (bot check); will try again later")
                content = post_content(body)
                if content is None:
                    raise FetchError(f"couldn't find the write-up on {link}")
                m = re.search(r'class="author-name"[^>]*>([^<]+)<', body)
                return {"url": link, "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                        "date": date, "blogger": html.unescape(m.group(1)).strip() if m else None,
                        "html": content}
        return None


def _is_bot_check(page):
    """The short "One moment, please..." page the site shows while it checks a visitor."""
    text = page or ""
    return len(text) < 3000 and ("one moment" in text.lower() or "being verified" in text.lower()
                                 or "fifteensquared" not in text.lower())


def _post_links(page):
    """(link, title) for each post linked from a search results page, in order."""
    seen, out = set(), []
    for m in re.finditer(r'<a[^>]+href="(https://fifteensquared\.net/\d{4}/\d\d/\d\d/[^"#?]+)"[^>]*>(.*?)</a>',
                         page or "", re.S):
        link, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if title and link not in seen:
            seen.add(link)
            out.append((link, title))
    return out


def post_content(page):
    """The body of a post page: the entry-content block, stopping before the comments."""
    m = re.search(r'<div[^>]+class="[^"]*\bentry-content\b[^"]*"[^>]*>', page or "")
    if not m:
        return None
    body = page[m.end():]
    ends = [i for i in (body.find(marker) for marker in
                        ('id="comments"', 'class="comments-area', '<footer', 'class="sharedaddy',
                         'class="entry-meta', '<nav')) if i >= 0]
    return body[:min(ends)] if ends else body


# ---------------------------------------------------------------- parsing
_BLOCK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tbody",
          "thead", "ul", "ol", "blockquote", "hr", "details", "summary", "section", "figure"}
_BOLD = {"strong", "b"}
_SKIP = {"script", "style", "noscript"}


class _Lines(HTMLParser):
    """HTML -> lines, each a list of runs (text, bold, underline)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.cur = [], []
        self.stack = []               # (tag, bold, underline) for open inline tags
        self.skip = 0

    def _flags(self):
        return (any(b for _, b, _ in self.stack), any(u for _, _, u in self.stack))

    def _newline(self):
        if any(t.strip() for t, _, _ in self.cur):
            self.lines.append(self.cur)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if tag in _BLOCK:
            self._newline()
            return
        if tag == "td" or tag == "th":
            self.cur.append(("  ", False, False))
            return
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        bold = tag in _BOLD or "font-weight:bold" in style or "font-weight:700" in style
        under = tag in ("u", "ins") or "text-decoration:underline" in style
        if tag in ("img", "input", "meta", "link"):
            return
        self.stack.append((tag, bold, under))

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag in _BLOCK:
            self._newline()
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip:
            return
        b, u = self._flags()
        self.cur.append((data.replace("\xa0", " "), b, u))

    def close(self):
        super().close()
        self._newline()
        return self.lines


def html_lines(content):
    p = _Lines()
    p.feed(content or "")
    return p.close()


def _text(runs):
    return re.sub(r"\s+", " ", "".join(t for t, _, _ in runs)).strip()


def _split_runs(runs, chars):
    """Drop the first `chars` characters of a run list."""
    out = []
    for t, b, u in runs:
        if chars >= len(t):
            chars -= len(t)
            continue
        out.append((t[chars:], b, u))
        chars = 0
    return out


_HEAD = re.compile(r"^(across|down)(\s+clues?)?\s*:?$", re.I)
_CLUE_NO = re.compile(r"^\s*(\d{1,2})(?:\s*[,/&]\s*\d{1,2}\s*(?:a|d|ac|dn|across|down)?\b)*\s*"
                      r"(?:a|d|ac|dn|across|down)?\b[\s.:)\-–]*")
_ANSWERISH = re.compile(r"^[A-Z][A-Z'’\-/, ]*[A-Z]$|^[A-Z]$")
_LEAD_CAPS = re.compile(r"^\s*([A-Z][A-Z'’\-]*(?:[ ,/][A-Z][A-Z'’\-]*)*)(?![a-z])")


def _expected(puz):
    """{direction: {clue number: entry}} using each entry's first number."""
    out = {"across": {}, "down": {}}
    for e in puz["entries"]:
        m = re.match(r"\d+", str(e.get("label") or ""))
        if m:
            out[e["direction"]].setdefault(int(m.group()), e)
    return out


def _total_length(e, puz):
    """Letters in the whole answer: linked clues ("1,5") span several entries."""
    nums = [int(n) for n in re.findall(r"\d+", str(e.get("label") or ""))]
    if len(nums) <= 1:
        return e["length"]
    total, by = 0, {(x["direction"], re.match(r"\d+", x["label"]).group()): x
                    for x in puz["entries"] if re.match(r"\d+", str(x.get("label") or ""))}
    for i, n in enumerate(nums):
        hit = by.get((e["direction"], str(n))) if i == 0 else \
            by.get((e["direction"], str(n))) or by.get(("down" if e["direction"] == "across" else "across", str(n)))
        total += hit["length"] if hit else 0
    return total


def _clean_expl(s):
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^[\s:=\-–—]+", "", s).strip()


_ANSWER_LABEL = re.compile(r"^(answer|solution)\s*[:\-–]?\s*(.*)$", re.I)
_EXPL_LABEL = re.compile(r"^(parsing|explanation|wordplay|parse)\s*[:\-–]?\s*", re.I)


def _defs(runs):
    defs, cur = [], ""
    for t, _, u in runs:
        if u:
            cur += t
        elif cur:
            defs.append(re.sub(r"\s+", " ", cur).strip()); cur = ""
    if cur:
        defs.append(re.sub(r"\s+", " ", cur).strip())
    return [d for d in defs if d]


def _parse_labelled(lines):
    """The layout with "Answer" and "Parsing" labels (e.g. the Quick Cryptic posts, where
    the answer is hidden behind a reveal): clue, Answer, the answer, Parsing, the explanation."""
    texts = [_text(ln) for ln in lines]
    at = next((i for i, t in enumerate(texts) if _ANSWER_LABEL.match(t)
               and (len(t) < 40 or t.lower().startswith(("answer:", "solution:")))), None)
    if at is None:
        return None
    rest = _ANSWER_LABEL.match(texts[at]).group(2).strip()
    k = at + 1
    if not rest:
        while k < len(texts) and not texts[k]:
            k += 1
        if k >= len(texts) or _EXPL_LABEL.match(texts[k]):
            return None
        rest, k = texts[k], k + 1
    answer = re.sub(r"\s+", " ", rest).strip(" ,/.")
    if not _letters(answer):
        return None
    expl = " ".join(_EXPL_LABEL.sub("", t) for t in texts[k:] if t and not _EXPL_LABEL.fullmatch(t))
    clue_runs = [r for ln in lines[:at] for r in ln + [(" ", False, False)]]
    return {"answer": answer.upper() if answer.islower() else answer, "clue": _text(clue_runs),
            "definitions": _defs(clue_runs), "explanation": _clean_expl(expl)}


def _parse_clue(lines, entry, puz):
    """lines: the clue's lines with the number already removed."""
    labelled = _parse_labelled(lines)
    if labelled:
        return labelled
    runs = [(t, b, u) for i, ln in enumerate(lines) for t, b, u in (ln + ([("\n", False, False)] if i < len(lines) - 1 else []))]
    want = _total_length(entry, puz)
    answer_at = None
    # 1. a bold run that reads like an answer
    for i, (t, b, _) in enumerate(runs):
        s = t.strip()
        if b and s and _ANSWERISH.match(s):
            answer_at = i
            break
    if answer_at is not None:
        j = answer_at
        answer = runs[j][0].strip()
        while j + 1 < len(runs) and runs[j + 1][1] and _ANSWERISH.match((answer + runs[j + 1][0]).strip()):
            j += 1
            answer = (answer + runs[j][0]).strip()
        before, after = runs[:answer_at], runs[j + 1:]
    else:
        # 2. a line starting with capitals of the right length
        flat = [ln for ln in "".join(t for t, _, _ in runs).split("\n")]
        answer = None
        for k, ln in enumerate(flat):
            m = _LEAD_CAPS.match(ln)
            if m and len(_letters(m.group(1))) == want:
                answer = m.group(1).strip()
                pos = sum(len(x) + 1 for x in flat[:k]) + m.end()
                before = _cut(runs, 0, sum(len(x) + 1 for x in flat[:k]) + (m.start(1)))
                after = _cut(runs, pos, None)
                break
        if answer is None:
            return None
    clue = _text(before)
    defs = []
    cur = ""
    for t, _, u in before:
        if u:
            cur += t
        elif cur:
            defs.append(re.sub(r"\s+", " ", cur).strip()); cur = ""
    if cur:
        defs.append(re.sub(r"\s+", " ", cur).strip())
    expl = _clean_expl(" ".join(t for t, _, _ in after).replace("\n", " "))
    answer = re.sub(r"\s+", " ", answer).strip(" ,/")
    return {"answer": answer, "clue": clue, "definitions": [d for d in defs if d],
            "explanation": expl}


def _cut(runs, start, end):
    """Runs covering characters [start, end) of the joined text."""
    out, pos = [], 0
    for t, b, u in runs:
        a, z = pos, pos + len(t)
        pos = z
        lo, hi = max(a, start), min(z, end if end is not None else z)
        if lo < hi:
            out.append((t[lo - a:hi - a], b, u))
    return out


def parse(content, puz):
    """{entry id: {answer, clue, definitions, explanation}} for the clues the post explains."""
    lines = html_lines(content)
    expected = _expected(puz)
    direction, last = None, 0
    blocks = []                       # [direction, number, [lines]]
    for ln in lines:
        text = _text(ln)
        h = _HEAD.match(text)
        if h:
            direction, last = h.group(1).lower(), 0
            continue
        if not direction:
            continue
        m = _CLUE_NO.match(text)
        if m:
            n = int(m.group(1))
            if n in expected[direction] and n > last:
                last = n
                # drop the number from the line's runs
                raw = "".join(t for t, _, _ in ln)
                lead = len(raw) - len(raw.lstrip())
                rm = _CLUE_NO.match(raw[lead:])
                blocks.append([direction, n, [_split_runs(ln, lead + rm.end())]])
                continue
        if blocks and blocks[-1][0] == direction:
            blocks[-1][2].append(ln)
    out = {}
    for d, n, ls in blocks:
        entry = expected[d][n]
        try:
            got = _parse_clue(ls, entry, puz)
        except Exception:                 # one odd clue shouldn't lose the rest
            got = None
        if got:
            out[entry["id"]] = got
    return out


# ---------------------------------------------------------------- applying to a puzzle
def apply(puz, post, clues):
    """Save the post's explanations on the puzzle and use its answers. Returns puz."""
    usable = len(clues) >= MIN_PARSED * max(1, len(_expected_entries(puz)))
    puz["blog"] = {
        "name": NAME, "url": post.get("url"), "title": post.get("title"), "date": post.get("date"),
        "blogger": post.get("blogger"), "fetched_at": now_iso(),
        "clues": clues if usable else {},
    }
    if not usable:
        return puz
    for e in puz["entries"]:
        c = clues.get(e["id"])
        if not c:
            continue
        letters = _letters(c["answer"])
        if len(letters) == e["length"]:
            if e.get("solution") and e["solution"] != letters:
                e["guardian_solution"] = e["solution"]
            e["solution"] = letters
        elif len(letters) == _total_length(e, puz) and len(letters) > e["length"]:
            _fill_linked(puz, e, letters)
    if all(e.get("solution") for e in puz["entries"]):
        if not puz.get("solution_available"):
            puz["solution_source"] = NAME
        puz["solution_available"] = True
    return puz


def _expected_entries(puz):
    """Entries that carry their own clue (not "See 1")."""
    return [e for e in puz["entries"] if not re.match(r"^\s*see\s+\d", e.get("clue") or "", re.I)]


def _fill_linked(puz, e, letters):
    nums = [n for n in re.findall(r"\d+", str(e.get("label") or ""))]
    by = {}
    for x in puz["entries"]:
        m = re.match(r"\d+", str(x.get("label") or ""))
        if m:
            by.setdefault((x["direction"], m.group()), x)
    pos = 0
    other = "down" if e["direction"] == "across" else "across"
    for i, n in enumerate(nums):
        x = e if i == 0 else (by.get((e["direction"], n)) or by.get((other, n)))
        if not x:
            return
        part = letters[pos:pos + x["length"]]
        pos += x["length"]
        if len(part) == x["length"]:
            x["solution"] = part


def has_explanations(puz):
    return bool((puz.get("blog") or {}).get("clues"))
