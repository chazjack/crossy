#!/usr/bin/env python3
"""
A small private web page (and JSON API) for the crossword library.

Pages
  GET /                         puzzles from the last month, ticked ones go into one PDF
  GET /?source=<key>&page=<n>   further back in one series (n = listing page, 2 and up)
  GET /sheet.pdf                the default pair; or ?p=<id>&p=<id>... for any selection,
                                &answers=1 for an answers page, &view=1 to open it in the browser
  GET /health                   plain "ok", for Railway health checks

JSON API (for the website redesign)
  GET /api/puzzles              {"default": [...], "puzzles": [...], "sources": [...], "warnings": [...]}
                                ?days=31, ?source=<key>; ?source=<key>&page=<n> for older listing pages
  GET /api/default              the default selection, with summaries
  GET /api/sources              the series being followed and their status
  GET /api/puzzles/<id>         one puzzle's data (answers only with ?answers=1, once published)
  GET /api/puzzles/<id>.pdf     that puzzle on its own (saved when it was fetched)
  GET /api/puzzles/<id>/grid.svg  small grid preview

Puzzle summaries carry: id, source, source_name, number, title, setter, date, date_label,
cols, rows, clues, has_tricks, solution_available, solution_date, answers_due, url, last_downloaded.

Environment:
  PORT             set by Railway
  SITE_PASSWORD    the password the browser asks for (any username works); the page stays locked without it
  OPEN_ACCESS      set to true to run with no password at all
  CROSSY_DATA_DIR  where puzzles are saved (Railway volumes are picked up automatically)
"""
import base64, hmac, html, json, os, re, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from crossy import pdf as pdfmod
from crossy.library import Library
from crossy.preview import grid_svg
from crossy.selection import default_selection
from crossy.sources import FetchError, NotFound

PASSWORD = os.environ.get("SITE_PASSWORD", "")
OPEN = os.environ.get("OPEN_ACCESS", "").strip().lower() in ("1", "true", "yes")

LIBRARY = None


def library():
    global LIBRARY
    if LIBRARY is None:
        LIBRARY = Library()
    return LIBRARY


STYLE = """
  :root { color-scheme: light dark; --ink:#1b1b1b; --paper:#faf8f3; --rule:#d9d4c7; --accent:#052962; --warn:#8a4b00; --warnbg:#fff3df; }
  @media (prefers-color-scheme: dark) { :root { --ink:#ece9e1; --paper:#171717; --rule:#3a3a3a; --accent:#8fb4ff; --warn:#ffcf8a; --warnbg:#3a2a12; } }
  * { box-sizing: border-box; }
  body { margin:0; font: 17px/1.5 Georgia, "Times New Roman", serif; color:var(--ink); background:var(--paper); }
  main { max-width: 34rem; margin: 0 auto; padding: 2.5rem 1.25rem 3rem; }
  h1 { font-size: 2.1rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
  h2 { font-size: 1.15rem; margin: 2rem 0 .4rem; }
  .sub { margin: 0 0 1.5rem; opacity: .8; }
  button { font: 600 1rem/1 system-ui, sans-serif; padding: .9rem 1.2rem; border-radius: .5rem; border: 2px solid var(--accent);
           background: var(--accent); color: var(--paper); cursor: pointer; }
  button.ghost { background: transparent; color: var(--accent); }
  .row { display:flex; gap:.6rem; flex-wrap: wrap; align-items:center; }
  .warn { font: .9rem/1.45 system-ui, sans-serif; color: var(--warn); background: var(--warnbg); border-radius: .5rem;
          padding: .6rem .8rem; margin: 0 0 .6rem; }
  ul { list-style: none; padding: 0; margin: 0; }
  li label { display:flex; gap:.7rem; align-items:center; padding:.55rem 0; border-bottom: 1px solid var(--rule);
             font: .98rem/1.3 system-ui, sans-serif; cursor:pointer; }
  li input { width:1.25rem; height:1.25rem; accent-color: var(--accent); flex: none; }
  .meta { opacity:.65; font-size:.85rem; }
  .tag { font: 600 .72rem/1 system-ui, sans-serif; border:1px solid var(--rule); border-radius: 1rem; padding:.2rem .45rem; opacity:.8; }
  .opts { font: .95rem system-ui, sans-serif; margin: 1rem 0; display:flex; gap:.5rem; align-items:center; }
  a { color: var(--accent); }
  .more { font: .9rem system-ui, sans-serif; display:inline-block; margin-top:.5rem; }
  #status { font: .95rem system-ui, sans-serif; min-height: 1.5em; margin-top: .8rem; }
"""

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crossy</title><style>{style}</style></head>
<body><main>
  <h1>Crossy</h1>
  <p class="sub">{ready}</p>
  {warnings}
  <form action="/sheet.pdf" method="get" onsubmit="return busy()">
    <input type="hidden" name="form" value="1">
    <div class="row">
      <button type="submit">Download PDF</button>
      <button type="submit" class="ghost" name="view" value="1" formtarget="_blank">Open in browser</button>
    </div>
    <div class="opts"><input type="checkbox" id="answers" name="answers" value="1">
      <label for="answers">Add an answers page (when published)</label></div>
    <div id="status" role="status"></div>
    {sections}
  </form>
</main>
<script>
  function busy() {{
    const s = document.getElementById('status');
    if (!document.querySelector('input[name=p]:checked')) {{ s.textContent = 'Tick at least one puzzle.'; return false; }}
    s.textContent = 'Making the PDF…';
    setTimeout(() => {{ s.textContent = ''; }}, 6000);
    return true;
  }}
</script>
</body></html>
"""

ERROR_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crossy: something went wrong</title>
<body style="font:17px/1.5 Georgia,serif;max-width:34rem;margin:3rem auto;padding:0 1.25rem">
<h1>Couldn't make the sheet</h1><p>{msg}</p><p><a href="/">Back</a></p></body>"""

ID_RE = re.compile(r"^/api/puzzles/([a-z0-9-]+?-\d+)(\.pdf|/grid\.svg)?$")


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _puzzle_row(s, checked):
    bits = [s.get("date_label") or "", s.get("setter") or ""]
    meta = " · ".join(b for b in bits if b)
    tags = ""
    if s.get("last_downloaded"):
        tags += ' <span class="tag" title="Downloaded before">printed</span>'
    return (f'<li><label><input type="checkbox" name="p" value="{_esc(s["id"])}"{" checked" if checked else ""}>'
            f'<span>{_esc(s["title"])}<br><span class="meta">{_esc(meta)}</span>{tags}</span></label></li>')


def _warnings_html(warnings):
    return "".join(f'<p class="warn">{_esc(w["message"])}</p>' for w in warnings)


def render_page(lib, source=None, page=None):
    if source and page:
        src = lib.source(source)
        items = lib.older(source, page)
        defaults = set()
        sections = (f"<h2>{_esc(src.name)}, further back</h2><ul>"
                    + "".join(_puzzle_row(s, False) for s in items) + "</ul>"
                    + f'<a class="more" href="/?source={_esc(source)}&amp;page={page + 1}">Older</a> · '
                    + '<a class="more" href="/">Back to this month</a>')
        ready = "Tick the puzzles you want on the sheet."
    else:
        lib.refresh()
        defaults = default_selection(lib)
        recent = lib.recent()
        by_id = {s["id"]: s for s in recent}
        names = [f'{by_id[i]["source_name"]} ({by_id[i]["date_label"]})' for i in defaults if i in by_id]
        ready = ("Ready to download: " + " and ".join(names) + "." if names
                 else "Tick the puzzles you want on the sheet.")
        sections = ""
        for key in lib.enabled:
            src = lib.source(key)
            rows = [s for s in recent if s["source"] == key]
            sections += f"<h2>{_esc(src.name)}</h2>"
            if rows:
                sections += "<ul>" + "".join(_puzzle_row(s, s["id"] in defaults) for s in rows) + "</ul>"
            else:
                sections += '<p class="meta">No puzzles saved yet.</p>'
            sections += f'<a class="more" href="/?source={_esc(key)}&amp;page=2">Older {_esc(src.name)} puzzles</a>'
    return PAGE.format(style=STYLE, ready=_esc(ready), warnings=_warnings_html(lib.warnings()), sections=sections)


def _strip_answers(puz):
    out = dict(puz)
    out["entries"] = [{k: v for k, v in e.items() if k != "solution"} for e in puz["entries"]]
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Crossy/2.0"

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None, cache="no-store"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _authed(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            _, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
        except Exception:
            return False
        return hmac.compare_digest(pw.encode(), PASSWORD.encode())

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if not PASSWORD and not OPEN:       # closed until a password is set (or OPEN_ACCESS=true)
            return self._send(503, "Locked: set SITE_PASSWORD (or OPEN_ACCESS=true) on this service in Railway.", "text/plain")
        if PASSWORD and not self._authed():
            return self._send(401, "Password needed.", "text/plain",
                              {"WWW-Authenticate": 'Basic realm="Crossy", charset="UTF-8"'})
        try:
            return self._route(url.path, q)
        except BrokenPipeError:
            pass
        except Exception as e:              # last resort: say what went wrong rather than hang up
            print(f"Error on {url.path}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            if url.path.startswith("/api/"):
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return self._send(500, ERROR_PAGE.format(msg=_esc(f"{type(e).__name__}: {e}")))

    def _route(self, path, q):
        lib = library()
        one = lambda k: (q.get(k) or [""])[0].strip()
        if path == "/":
            page = one("page")
            source = one("source")
            if source and page.isdigit() and source in lib.sources:
                return self._send(200, render_page(lib, source, max(2, int(page))))
            return self._send(200, render_page(lib))
        if path == "/sheet.pdf":
            return self._sheet(lib, q)
        if path == "/api/sources":
            lib.refresh()
            return self._json({"sources": lib.status(), "warnings": lib.warnings()})
        if path == "/api/default":
            ids = default_selection(lib)
            by_id = {s["id"]: s for s in lib.recent(days=3650)}
            return self._json({"ids": ids, "puzzles": [by_id[i] for i in ids if i in by_id],
                               "warnings": lib.warnings()})
        if path == "/api/puzzles":
            source, page = one("source"), one("page")
            if source and source not in lib.sources:
                return self._json({"error": f"unknown source {source!r}"}, 404)
            if source and page.isdigit() and int(page) > 1:
                try:
                    items = lib.older(source, int(page))
                except FetchError as e:
                    return self._json({"error": str(e), "puzzles": [], "warnings": lib.warnings()}, 502)
                return self._json({"puzzles": items, "page": int(page), "warnings": lib.warnings()})
            days = int(one("days")) if one("days").isdigit() else None
            lib.refresh([source] if source else None)
            ids = default_selection(lib)
            keys = [source] if source else None
            recent = lib.recent(days=days, keys=keys)
            by_id = {s["id"]: s for s in lib.recent(days=3650)}
            return self._json({"default": [by_id[i] for i in ids if i in by_id], "puzzles": recent,
                               "sources": lib.status(), "warnings": lib.warnings()})
        m = ID_RE.match(path)
        if m:
            return self._puzzle(lib, m.group(1), m.group(2), one("answers"))
        return self._send(404, "Not found", "text/plain")

    def _puzzle(self, lib, pid, suffix, answers):
        try:
            if suffix == ".pdf":
                data = lib.puzzle_pdf(pid)
                return self._send(200, data, "application/pdf",
                                  {"Content-Disposition": f'inline; filename="{pid}.pdf"'})
            puz = lib.get(pid)
        except (KeyError, NotFound) as e:
            return self._json({"error": f"no such puzzle: {pid} ({e})"}, 404)
        except FetchError as e:
            return self._json({"error": str(e)}, 502)
        if suffix == "/grid.svg":
            return self._send(200, grid_svg(puz), "image/svg+xml", cache="public, max-age=86400")
        return self._json(puz if answers and pdfmod.has_answers(puz) else _strip_answers(puz))

    def _sheet(self, lib, q):
        ids = [i for v in q.get("p", []) for i in v.split(",") if i.strip()]
        from_form = bool(q.get("form"))
        if not ids and from_form:
            return self._send(400, ERROR_PAGE.format(msg="Tick at least one puzzle."))
        answers = bool((q.get("answers") or [""])[0])
        try:
            if not ids:
                ids = default_selection(lib)
                if not ids:
                    raise FetchError("couldn't find this week's puzzles. "
                                     + " ".join(w["message"] for w in lib.warnings()))
            pdf, name, notes = lib.sheet(ids, answers=answers)
        except KeyError as e:
            return self._send(404, ERROR_PAGE.format(msg=_esc(f"That isn't a puzzle Crossy knows: {e}")))
        except NotFound as e:
            msg = f"One of those puzzles doesn't exist (yet). ({e})"
            return self._send(404, ERROR_PAGE.format(msg=_esc(msg)))
        except (FetchError, pdfmod.LayoutError, ValueError) as e:
            print(f"Error building sheet: {e}", file=sys.stderr, flush=True)
            return self._send(502, ERROR_PAGE.format(msg=_esc(e)))
        lib.record_download(ids)
        disposition = "inline" if (q.get("view") or [""])[0] else "attachment"
        print(f"Served {name}" + (f" ({' '.join(notes)})" if notes else ""), flush=True)
        headers = {"Content-Disposition": f'{disposition}; filename="{name}"'}
        if notes:
            headers["X-Crossy-Notes"] = " ".join(notes).encode("ascii", "replace").decode()
        self._send(200, pdf, "application/pdf", headers)

    def log_message(self, fmt, *args):      # keep Railway logs quiet (and free of auth headers)
        pass


def main():
    port = int(os.environ.get("PORT", "8000"))
    if PASSWORD:
        print("Password protection on.", flush=True)
    elif OPEN:
        print("OPEN_ACCESS is on: no password, anyone with the link can use the page.", flush=True)
    else:
        print("SITE_PASSWORD isn't set, so the page stays locked until you add it.", flush=True)
    print(f"Puzzles are saved in {library().store.root}", flush=True)
    print(f"Crossy listening on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
