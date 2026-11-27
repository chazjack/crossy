"""Saves puzzle data and PDFs on disk, so each puzzle is fetched once.

Layout under the data folder:
  puzzles/<source>/<number>.json   normalised puzzle data
  pdf/<source>/<number>.pdf        that puzzle on its own (top half of an A4 page)
  index/<source>.json              what's known about the series: summaries, last check, errors
  state.json                       downloads (for "already printed" markers)

The folder is CROSSY_DATA_DIR, else Railway's volume (RAILWAY_VOLUME_MOUNT_PATH), else ~/.crossy.
If writing fails (no space, read-only disk), the store keeps working from memory and remembers
the error so the website can warn about it.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading


def default_root():
    return (os.environ.get("CROSSY_DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            or os.path.expanduser("~/.crossy"))


def is_persistent():
    """False when running on Railway without a volume (files vanish when the service sleeps)."""
    if os.environ.get("CROSSY_DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        return True
    return not os.environ.get("RAILWAY_ENVIRONMENT")


class Store:
    def __init__(self, root=None):
        self.root = root or default_root()
        self.write_error = None          # last failed write, as a message
        self._mem = {}                   # path -> bytes, for writes that failed
        self._lock = threading.Lock()

    # ------------------------------------------------------------ paths
    def _path(self, *parts):
        return os.path.join(self.root, *parts)

    def puzzle_path(self, source, number):
        return self._path("puzzles", source, f"{int(number)}.json")

    def pdf_path(self, source, number):
        return self._path("pdf", source, f"{int(number)}.pdf")

    def index_path(self, source):
        return self._path("index", f"{source}.json")

    def state_path(self):
        return self._path("state.json")

    # ------------------------------------------------------------ raw io
    def _read(self, path):
        with self._lock:
            if path in self._mem:
                return self._mem[path]
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError:
            return None                  # unreadable counts as missing

    def _write(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            with self._lock:
                self._mem[path] = data
                self.write_error = f"{e.strerror or e} ({path})"
            return False
        with self._lock:
            self._mem.pop(path, None)
        return True

    def _read_json(self, path, default=None):
        data = self._read(path)
        if data is None:
            return default
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            return default               # a damaged file counts as missing; it gets re-fetched

    def _write_json(self, path, obj):
        return self._write(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))

    # ------------------------------------------------------------ puzzles
    def read_puzzle(self, source, number):
        return self._read_json(self.puzzle_path(source, number))

    def write_puzzle(self, puzzle):
        return self._write_json(self.puzzle_path(puzzle["source"], puzzle["number"]), puzzle)

    def read_pdf(self, source, number):
        return self._read(self.pdf_path(source, number))

    def write_pdf(self, source, number, data):
        return self._write(self.pdf_path(source, number), data)

    # ------------------------------------------------------------ index and state
    def read_index(self, source):
        idx = self._read_json(self.index_path(source), {}) or {}
        idx.setdefault("puzzles", {})
        idx.setdefault("failed", {})
        return idx

    def write_index(self, source, idx):
        return self._write_json(self.index_path(source), idx)

    def read_state(self):
        st = self._read_json(self.state_path(), {}) or {}
        st.setdefault("downloads", {})
        return st

    def write_state(self, st):
        return self._write_json(self.state_path(), st)
