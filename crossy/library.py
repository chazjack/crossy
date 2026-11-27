"""The puzzle library: fetch each puzzle once, keep about a month of history, warn on failures.

    lib = Library()                      # data folder from crossy.store.default_root()
    lib.refresh()                        # check enabled series for new puzzles (skips if checked recently)
    lib.recent()                         # summaries, newest first, last 31 days
    lib.older("guardian-quick", page=3)  # further back, one listing page at a time
    lib.get("guardian-quick-17648")      # full puzzle data (fetched and saved if new)
    lib.sheet(["guardian-quick-17648", "guardian-quick-cryptic-138"], answers=False)
    lib.warnings()                       # anything the website should warn about
"""
from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor

from . import pdf as pdfmod
from . import sources as srcmod
from .sources import FetchError, NotFound
from .sources.base import now_iso
from .store import Store, is_persistent

HISTORY_DAYS = 31
MAX_PAGES = 6                 # listing pages read per refresh, at most
STALE_AFTER = 30 * 60         # seconds before a series is checked again
RETRY_AFTER_ERROR = 5 * 60


def _parse_time(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


class Library:
    def __init__(self, store=None, sources=None, enabled=None, history_days=HISTORY_DAYS,
                 stale_after=STALE_AFTER, workers=6, today=None):
        self.store = store or Store()
        self.sources = sources or srcmod.ALL
        self.enabled = enabled or [k for k in srcmod.enabled_keys() if k in self.sources]
        self.history_days = history_days
        self.stale_after = stale_after
        self.workers = workers
        self._today = today
        self._locks = {k: threading.RLock() for k in self.sources}
        self._state_lock = threading.Lock()
        self._indexes = {}

    # ------------------------------------------------------------ helpers
    def today(self):
        return self._today() if callable(self._today) else (self._today or dt.date.today())

    def source(self, key):
        try:
            return self.sources[key]
        except KeyError:
            raise KeyError(f"unknown puzzle source {key!r}")

    def split_id(self, pid):
        key, _, num = str(pid).rpartition("-")
        if not num.isdigit() or key not in self.sources:
            raise KeyError(f"not a puzzle id: {pid!r}")
        return self.sources[key], int(num)

    def _index(self, key):
        if key not in self._indexes:
            self._indexes[key] = self.store.read_index(key)
        return self._indexes[key]

    def _save_index(self, key):
        self.store.write_index(key, self._indexes[key])

    @staticmethod
    def _summary_fields(p):
        return {
            "title": p["title"], "setter": p.get("setter"), "date": p.get("date"),
            "cols": p["cols"], "rows": p["rows"], "clues": len(p["entries"]),
            "has_tricks": bool(p.get("instructions")),
            "solution_available": bool(p.get("solution_available")),
            "solution_date": p.get("solution_date"), "url": p.get("url"),
        }

    # ------------------------------------------------------------ fetching
    def _fetch_and_save(self, src, number):
        """Fetch one puzzle, save its data and its own PDF, record it in the index."""
        puz = src.fetch(number)
        self.store.write_puzzle(puz)
        try:
            self.store.write_pdf(src.key, number, pdfmod.build_pdf([puz]))
        except pdfmod.LayoutError:
            pass                                  # still listed; sheet() reports the problem
        idx = self._index(src.key)
        idx["puzzles"][str(number)] = self._summary_fields(puz)
        idx["failed"].pop(str(number), None)
        return puz

    def _ensure(self, src, numbers, stop_before=None):
        """Fetch the numbers not saved yet, newest first, in small parallel batches.
        Stops after a batch that reaches a puzzle older than stop_before (a date)."""
        idx = self._index(src.key)
        todo = [n for n in sorted(numbers, reverse=True) if str(n) not in idx["puzzles"]]
        if stop_before:
            # numbers rise with dates, so anything below a puzzle already known to be too old is too old
            old = [int(n) for n, info in idx["puzzles"].items()
                   if info.get("date") and info["date"] < stop_before.isoformat()]
            if old:
                todo = [n for n in todo if n > max(old)]
        for i in range(0, len(todo), self.workers):
            batch = todo[i:i + self.workers]
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                results = list(ex.map(lambda n: self._try_fetch(src, n), batch))
            if stop_before and any(p and p.get("date") and p["date"] < stop_before.isoformat()
                                   for p in results):
                break

    def _try_fetch(self, src, number):
        try:
            return self._fetch_and_save(src, number)
        except FetchError as e:
            self._index(src.key)["failed"][str(number)] = {"error": str(e), "at": now_iso()}
        except Exception as e:                    # keep one bad puzzle from breaking the listing
            self._index(src.key)["failed"][str(number)] = {"error": f"{type(e).__name__}: {e}", "at": now_iso()}
        return None

    # ------------------------------------------------------------ refresh
    def _is_fresh(self, idx):
        checked = _parse_time(idx.get("checked_at"))
        if not checked:
            return False
        age = (dt.datetime.now(dt.timezone.utc) - checked).total_seconds()
        return age < (RETRY_AFTER_ERROR if idx.get("error") else self.stale_after)

    def refresh(self, keys=None, force=False):
        """Check series for new puzzles and fetch the ones not saved yet (about a month back)."""
        for key in keys or self.enabled:
            src = self.source(key)
            with self._locks[key]:
                idx = self._index(key)
                if not force and self._is_fresh(idx):
                    continue
                cutoff = self.today() - dt.timedelta(days=self.history_days)
                seen = set()
                try:
                    for page in range(1, MAX_PAGES + 1):
                        nums = src.list_page(page)
                        if not nums:
                            break
                        seen.update(nums)
                        self._ensure(src, nums, stop_before=cutoff)
                        dates = [idx["puzzles"][str(n)]["date"] for n in nums
                                 if str(n) in idx["puzzles"] and idx["puzzles"][str(n)].get("date")]
                        if dates and min(dates) < cutoff.isoformat():
                            break
                        if not dates:             # nothing on this page could be fetched
                            break
                    if seen:                      # forget failures the listing no longer shows
                        for n in list(idx["failed"]):
                            if int(n) not in seen and int(n) >= min(seen):
                                idx["failed"].pop(n)
                    idx["error"] = None
                    idx["refreshed_at"] = now_iso()
                except FetchError as e:
                    idx["error"] = str(e)
                except Exception as e:
                    idx["error"] = f"{type(e).__name__}: {e}"
                idx["checked_at"] = now_iso()
                self._save_index(key)

    def older(self, key, page):
        """Fetch (once) and list the puzzles on one page of a series' listing, for going further back."""
        src = self.source(key)
        with self._locks[key]:
            nums = src.list_page(page)
            self._ensure(src, nums)
            self._save_index(key)
        return [s for s in (self._summary(key, n) for n in nums) if s]

    # ------------------------------------------------------------ lookups
    def _summary(self, key, number):
        src = self.source(key)
        info = self._index(key)["puzzles"].get(str(number))
        if not info:
            return None
        pid = src.puzzle_id(number)
        out = {"id": pid, "source": key, "source_name": src.name, "publication": src.publication,
               "kind": src.kind, "number": int(number)}
        out.update(info)
        if info.get("date"):
            out["date_label"] = pdfmod.date_label(info, "short")
        out["answers_due"] = bool(not info.get("solution_available") and info.get("solution_date")
                                  and info["solution_date"] <= self.today().isoformat())
        out["last_downloaded"] = self._downloads().get(pid)
        return out

    def recent(self, days=None, keys=None):
        """Summaries of saved puzzles from the last `days` days, newest first."""
        days = self.history_days if days is None else days
        since = (self.today() - dt.timedelta(days=days)).isoformat()
        out = []
        order = {k: i for i, k in enumerate(self.enabled)}
        for key in keys or self.enabled:
            for num in self._index(key)["puzzles"]:
                s = self._summary(key, num)
                if s and (s.get("date") or "") >= since:
                    out.append(s)
        out.sort(key=lambda s: (s.get("date") or "", -order.get(s["source"], 99), s["number"]), reverse=True)
        return out

    def get(self, pid):
        """Full puzzle data. Fetched and saved if new; fetched again once its answers are due."""
        src, number = self.split_id(pid)
        with self._locks[src.key]:
            puz = self.store.read_puzzle(src.key, number)
            if puz and not puz.get("solution_available") and puz.get("solution_date") \
                    and puz["solution_date"] <= self.today().isoformat():
                try:
                    puz = self._fetch_and_save(src, number)
                    self._save_index(src.key)
                except FetchError:
                    pass                          # keep the copy we have
            if puz:
                return puz
            try:
                puz = self._fetch_and_save(src, number)
            except NotFound:
                raise
            except FetchError as e:
                self._index(src.key)["failed"][str(number)] = {"error": str(e), "at": now_iso()}
                self._save_index(src.key)
                raise
            self._save_index(src.key)
            return puz

    def puzzle_pdf(self, pid):
        """The single-puzzle PDF saved when the puzzle was fetched (rebuilt if missing)."""
        src, number = self.split_id(pid)
        data = self.store.read_pdf(src.key, number)
        if data is None:
            data = pdfmod.build_pdf([self.get(pid)])
            self.store.write_pdf(src.key, number, data)
        return data

    def latest_id(self, key, use_index=True):
        """The newest puzzle in a series, or None if it can't be found."""
        src = self.source(key)
        idx = self._index(key)
        if use_index and idx["puzzles"]:
            best = max(idx["puzzles"].items(), key=lambda kv: (kv[1].get("date") or "", int(kv[0])))
            return src.puzzle_id(best[0])
        try:
            nums = src.list_page(1)
        except FetchError as e:
            with self._locks[key]:
                idx["error"] = str(e)
                idx["checked_at"] = now_iso()
                self._save_index(key)
            return None
        return src.puzzle_id(max(nums)) if nums else None

    # ------------------------------------------------------------ sheets
    def sheet(self, ids, answers=False):
        """(pdf_bytes, filename, notes) for the puzzles, in the order given.
        Raises FetchError/KeyError naming the puzzle that couldn't be fetched."""
        puzzles = [self.get(pid) for pid in ids]
        notes = []
        if answers:
            missing = [p["title"] for p in puzzles if not pdfmod.has_answers(p)]
            if missing:
                notes.append("Answers aren't published yet for " + ", ".join(missing) + ".")
        data = pdfmod.build_pdf(puzzles, answers=answers)
        name = "crossy_" + "_".join(f"{self.sources[p['source']].short}{p['number']}" for p in puzzles)
        if answers and any(pdfmod.has_answers(p) for p in puzzles):
            name += "_answers"
        return data, name + ".pdf", notes

    # ------------------------------------------------------------ downloads ("already printed")
    def _downloads(self):
        if not hasattr(self, "_state"):
            self._state = self.store.read_state()
        return self._state["downloads"]

    def record_download(self, ids):
        with self._state_lock:
            d = self._downloads()
            stamp = now_iso()
            for pid in ids:
                d[pid] = stamp
            self.store.write_state(self._state)

    # ------------------------------------------------------------ warnings
    def warnings(self, keys=None):
        """Problems worth showing on the website: [{"level", "source", "puzzle", "message"}]."""
        out = []
        if not is_persistent():
            out.append({"level": "info", "source": None, "puzzle": None,
                        "message": "Puzzles are only kept until the site next goes to sleep. "
                                   "Add a Railway volume to keep them."})
        if self.store.write_error:
            out.append({"level": "warning", "source": None, "puzzle": None,
                        "message": "Couldn't save puzzles to disk, so they'll be fetched again next "
                                   f"time: {self.store.write_error}"})
        for key in keys or self.enabled:
            src, idx = self.source(key), self._index(key)
            if idx.get("error"):
                out.append({"level": "warning", "source": key, "puzzle": None,
                            "message": f"Couldn't check for new {src.name} puzzles: {idx['error']}"})
            for num, f in sorted(idx["failed"].items(), key=lambda kv: -int(kv[0])):
                out.append({"level": "warning", "source": key, "puzzle": src.puzzle_id(num),
                            "message": f"Couldn't fetch {src.name} No {int(num):,}: {f.get('error')}"})
        return out

    def status(self):
        """Per-series state, for the API."""
        out = []
        for key in self.enabled:
            src, idx = self.source(key), self._index(key)
            info = src.info()
            info.update({"checked_at": idx.get("checked_at"), "refreshed_at": idx.get("refreshed_at"),
                         "error": idx.get("error"), "saved": len(idx["puzzles"]),
                         "failed": len(idx["failed"])})
            out.append(info)
        return out
