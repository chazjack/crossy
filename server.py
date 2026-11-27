#!/usr/bin/env python3
"""
A small private web page for downloading the crossword sheet on demand.

  GET /              the page (password protected if SITE_PASSWORD is set)
  GET /sheet.pdf     builds and returns the PDF; ?quick=138&quiptic=1409 for specific puzzles,
                     &view=1 to open it in the browser instead of downloading
  GET /health        plain "ok", for Railway health checks

Environment:
  PORT             set by Railway
  SITE_PASSWORD    the password the browser asks for (any username works); the page stays locked without it
"""
import base64, hmac, html, os, sys, tempfile, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import crossword_sheet as cs

PASSWORD = os.environ.get("SITE_PASSWORD", "")

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crossy</title>
<style>
  :root { color-scheme: light dark; --ink:#1b1b1b; --paper:#faf8f3; --rule:#d9d4c7; --accent:#052962; }
  @media (prefers-color-scheme: dark) { :root { --ink:#ece9e1; --paper:#171717; --rule:#3a3a3a; --accent:#8fb4ff; } }
  * { box-sizing: border-box; }
  body { margin:0; font: 17px/1.5 Georgia, "Times New Roman", serif; color:var(--ink); background:var(--paper); }
  main { max-width: 34rem; margin: 0 auto; padding: 3rem 1.25rem; }
  h1 { font-size: 2.1rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
  .sub { margin: 0 0 2rem; opacity: .75; }
  .grid { display:inline-grid; grid-template-columns: repeat(5, 12px); gap:2px; margin-bottom:1.25rem; }
  .grid i { width:12px; height:12px; border:1.5px solid var(--ink); }
  .grid i.b { background: var(--ink); }
  button { font: 600 1rem/1 system-ui, sans-serif; padding: .9rem 1.2rem; border-radius: .5rem; border: 2px solid var(--accent);
           background: var(--accent); color: var(--paper); cursor: pointer; }
  button.ghost { background: transparent; color: var(--accent); }
  .row { display:flex; gap:.6rem; flex-wrap: wrap; }
  details { margin-top: 2.25rem; border-top: 1px solid var(--rule); padding-top: 1.25rem; }
  summary { cursor: pointer; font-weight: bold; }
  label { display:block; font: .9rem system-ui, sans-serif; margin: 1rem 0 .3rem; }
  input { font: 1rem system-ui, sans-serif; padding: .6rem .7rem; width: 9rem; border: 1px solid var(--rule); border-radius: .4rem;
          background: transparent; color: var(--ink); }
  .hint { font: .85rem/1.45 system-ui, sans-serif; opacity: .7; margin-top: .9rem; }
  #status { font: .95rem system-ui, sans-serif; min-height: 1.5em; margin-top: 1rem; }
</style></head>
<body><main>
  <div class="grid" aria-hidden="true">
    <i></i><i class="b"></i><i></i><i></i><i></i>
    <i></i><i class="b"></i><i></i><i class="b"></i><i></i>
    <i></i><i></i><i></i><i></i><i></i>
    <i></i><i class="b"></i><i></i><i class="b"></i><i></i>
    <i></i><i></i><i></i><i class="b"></i><i></i>
  </div>
  <h1>Crossy</h1>
  <p class="sub">This week's Guardian Quick Cryptic and Quiptic on one A4 sheet.</p>

  <form action="/sheet.pdf" method="get" onsubmit="busy()">
    <div class="row">
      <button type="submit">Download PDF</button>
      <button type="submit" class="ghost" name="view" value="1" formtarget="_blank">Open in browser</button>
    </div>
    <div id="status" role="status"></div>

    <details>
      <summary>Choose older puzzles</summary>
      <label for="quick">Quick Cryptic number</label>
      <input id="quick" name="quick" inputmode="numeric" pattern="[0-9]*" placeholder="latest">
      <label for="quiptic">Quiptic number</label>
      <input id="quiptic" name="quiptic" inputmode="numeric" pattern="[0-9]*" placeholder="latest">
      <p class="hint">Leave a box empty for the latest one. Both series go up by one each week.
        The number is in the puzzle's title on the Guardian site.</p>
    </details>
  </form>
</main>
<script>
  function busy() {
    const s = document.getElementById('status');
    s.textContent = 'Fetching the puzzles… this takes a few seconds.';
    setTimeout(() => { s.textContent = ''; }, 8000);
  }
</script>
</body></html>
"""

ERROR_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crossy: something went wrong</title>
<body style="font:17px/1.5 Georgia,serif;max-width:34rem;margin:3rem auto;padding:0 1.25rem">
<h1>Couldn't make the sheet</h1><p>{msg}</p><p><a href="/">Back</a></p></body>"""


def _num(q, key):
    v = (q.get(key) or [""])[0].strip()
    return int(v) if v.isdigit() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "Crossy/1.0"

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

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
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if not PASSWORD:                    # closed until a password is set
            return self._send(503, "Locked: set SITE_PASSWORD on this service in Railway.", "text/plain")
        if not self._authed():
            return self._send(401, "Password needed.", "text/plain",
                              {"WWW-Authenticate": 'Basic realm="Crossy", charset="UTF-8"'})
        if url.path == "/":
            return self._send(200, PAGE)
        if url.path == "/sheet.pdf":
            return self._sheet(urllib.parse.parse_qs(url.query))
        return self._send(404, "Not found", "text/plain")

    def _sheet(self, q):
        try:
            quick = cs.fetch_puzzle("quick-cryptic", _num(q, "quick"))
            quiptic = cs.fetch_puzzle("quiptic", _num(q, "quiptic"))
            with tempfile.TemporaryDirectory() as d:
                name = f"crosswords_QC{quick['number']}_Q{quiptic['number']}.pdf"
                path = os.path.join(d, name)
                cs.build_pdf(quick, quiptic, path)
                with open(path, "rb") as f:
                    pdf = f.read()
        except (Exception, SystemExit) as e:
            msg = str(e.code if isinstance(e, SystemExit) else e) or type(e).__name__
            if "404" in msg:
                msg = "One of those puzzle numbers doesn't exist (yet). Check the numbers and try again."
            print(f"Error building sheet: {msg}", file=sys.stderr, flush=True)
            return self._send(502, ERROR_PAGE.format(msg=html.escape(msg)))
        disposition = "inline" if (q.get("view") or [""])[0] else "attachment"
        print(f"Served {name}", flush=True)
        self._send(200, pdf, "application/pdf", {"Content-Disposition": f'{disposition}; filename="{name}"'})

    def log_message(self, fmt, *args):      # keep Railway logs quiet (and free of auth headers)
        pass


def main():
    port = int(os.environ.get("PORT", "8000"))
    if not PASSWORD:
        print("SITE_PASSWORD isn't set, so the page stays locked until you add it.", flush=True)
    print(f"Crossy listening on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
