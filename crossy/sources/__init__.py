"""The registry of puzzle series.

DEFAULT_ENABLED is what the website lists and refreshes.
CROSSY_SOURCES, a comma-separated list of keys, overrides it. Anything in ALL can be fetched by id.
"""
from __future__ import annotations

import os

from .base import FetchError, NotFound, Source, http_get  # noqa: F401
from .guardian import GuardianSource


def _guardian():
    return [
        GuardianSource("quick", "Guardian Quick crossword", "Monday to Saturday", "quick"),
        GuardianSource("quick-cryptic", "Guardian Quick Cryptic", "Saturdays", "cryptic"),
        GuardianSource("quiptic", "Guardian Quiptic", "Mondays", "cryptic"),
        GuardianSource("cryptic", "Guardian Cryptic", "Monday to Friday", "cryptic"),
        GuardianSource("prize", "Guardian Prize", "Saturdays", "cryptic"),
        GuardianSource("everyman", "Guardian Everyman", "Sundays (until April 2025)", "cryptic"),
        GuardianSource("speedy", "Guardian Speedy", "Sundays (until April 2025)", "quick"),
    ]


ALL = {s.key: s for s in _guardian()}

# Everyman and Speedy are registered but not listed: the Guardian stopped publishing them in
# April 2025, so their newest puzzles are older than the month the page shows.
DEFAULT_ENABLED = ["guardian-quick", "guardian-quick-cryptic", "guardian-quiptic", "guardian-cryptic",
                   "guardian-prize"]


def enabled_keys():
    env = [k.strip() for k in os.environ.get("CROSSY_SOURCES", "").split(",") if k.strip()]
    keys = env or DEFAULT_ENABLED
    return [k for k in keys if k in ALL]


def get_source(key):
    try:
        return ALL[key]
    except KeyError:
        raise KeyError(f"unknown puzzle source {key!r}")


def split_id(puzzle_id):
    """'guardian-quick-cryptic-138' -> (source, 138). Raises KeyError/ValueError."""
    key, _, num = str(puzzle_id).rpartition("-")
    if not num.isdigit():
        raise ValueError(f"not a puzzle id: {puzzle_id!r}")
    return get_source(key), int(num)
