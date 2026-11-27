"""Crossy: fetch crosswords, keep a small library of them, and print them on A4.

Modules
  crossy.sources    where puzzles come from (one class per publication, a registry of series)
  crossy.store      saves each puzzle's data and PDF on disk
  crossy.library    fetch-once history: refresh, list, look up, warnings
  crossy.pdf        lays out any selection, two puzzles per A4 page, plus an optional answers page
  crossy.selection  the default pair (one place, shared by the website and the weekly email)
  crossy.preview    small SVG grid previews
"""
