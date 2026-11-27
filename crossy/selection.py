"""The default selection, in one place.

The website's "Ready to download" pair and the weekly email both use this, so they always match:
the latest Quick crossword (top half) and the latest Quick Cryptic (bottom half).
"""
from __future__ import annotations

DEFAULT_SOURCES = ["guardian-quick", "guardian-quick-cryptic"]


def default_selection(library, refresh=True):
    """Puzzle ids for the default sheet, in page order.

    refresh=True (the website) checks the series listings as usual first; refresh=False
    (the weekly job) just asks each series for its newest puzzle, which is quicker.
    Series that can't be reached are left out; library.warnings() says why.
    """
    if refresh:
        library.refresh(DEFAULT_SOURCES)
    ids = []
    for key in DEFAULT_SOURCES:
        pid = library.latest_id(key, use_index=refresh)
        if pid:
            ids.append(pid)
    return ids
