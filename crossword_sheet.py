#!/usr/bin/env python3
"""
Guardian crossword sheet: the latest Quick Cryptic and Quiptic on one A4 page.

Usage
  python3 crossword_sheet.py                 # latest of each, saves a PDF
  python3 crossword_sheet.py --print         # ...and sends it to the default printer
  python3 crossword_sheet.py --print --printer "HP_OfficeJet"
  python3 crossword_sheet.py --quick 138 --quiptic 1409    # specific puzzles
  python3 crossword_sheet.py --from-json qc.json qp.json   # offline / testing
  python3 crossword_sheet.py --email         # email the PDF via Resend (e.g. to a printer)

Email settings come from environment variables:
  RESEND_API_KEY   your Resend API key
  EMAIL_FROM       sender on your verified Resend domain, e.g. "Crosswords <crosswords@example.com>"
  EMAIL_TO         where to send the PDF; comma-separate several (printer address, yourself, ...)
  NOTIFY_TO        optional: where to send a short note if a run fails

Needs: Python 3.8+ and reportlab  (pip3 install reportlab)
"""
import argparse, base64, datetime as dt, html, json, os, re, subprocess, sys, urllib.error, urllib.request
from html.parser import HTMLParser

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

BASE = "https://www.theguardian.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


# ---------------------------------------------------------------- fetching
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


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


def fetch_puzzle(kind, number=None):
    if number is None:
        series = get(f"{BASE}/crosswords/series/{kind}")
        nums = [int(n) for n in re.findall(rf"/crosswords/{kind}/(\d+)", series)]
        if not nums:
            sys.exit(f"Couldn't find any {kind} puzzles on the series page.")
        number = max(nums)
    p = _IslandFinder()
    p.feed(get(f"{BASE}/crosswords/{kind}/{number}"))
    if not p.data:
        sys.exit(f"Couldn't find the puzzle data on the {kind} {number} page "
                 "(the Guardian may have changed its page layout).")
    return p.data


# ---------------------------------------------------------------- text helpers
def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def wrap(text, font, size, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- drawing
def draw_grid(c, puz, x0, y_top, cell):
    cols, rows = puz["dimensions"]["cols"], puz["dimensions"]["rows"]
    white, numbers = set(), {}
    for e in puz["entries"]:
        x, y = e["position"]["x"], e["position"]["y"]
        numbers[(x, y)] = e["humanNumber"]
        for i in range(e["length"]):
            white.add((x + i, y) if e["direction"] == "across" else (x, y + i))

    # black background then white squares
    c.setFillGray(0)
    c.rect(x0, y_top - rows * cell, cols * cell, rows * cell, stroke=0, fill=1)
    c.setLineWidth(0.4)
    for (x, y) in white:
        c.setFillGray(1)
        c.rect(x0 + x * cell, y_top - (y + 1) * cell, cell, cell, stroke=1, fill=1)
    c.setFillGray(0)
    c.setFont("Helvetica", cell * 0.30)
    for (x, y), n in numbers.items():
        c.drawString(x0 + x * cell + cell * 0.07, y_top - y * cell - cell * 0.30, n)

    # word separators: "," = thick bar, "-" = short dash
    for e in puz["entries"]:
        x, y = e["position"]["x"], e["position"]["y"]
        across = e["direction"] == "across"
        for sep, locs in (e.get("separatorLocations") or {}).items():
            for k in locs:
                if across:
                    bx, by1, by2 = x0 + (x + k) * cell, y_top - y * cell, y_top - (y + 1) * cell
                    if sep == ",":
                        c.setLineWidth(2.8); c.line(bx, by1, bx, by2)
                    else:
                        c.setLineWidth(1.2); c.line(bx - cell * .18, (by1 + by2) / 2, bx + cell * .18, (by1 + by2) / 2)
                else:
                    by, bx1, bx2 = y_top - (y + k) * cell, x0 + x * cell, x0 + (x + 1) * cell
                    if sep == ",":
                        c.setLineWidth(2.8); c.line(bx1, by, bx2, by)
                    else:
                        c.setLineWidth(1.2); c.line((bx1 + bx2) / 2, by - cell * .18, (bx1 + bx2) / 2, by + cell * .18)
    c.setLineWidth(1.2)
    c.rect(x0, y_top - rows * cell, cols * cell, rows * cell, stroke=1, fill=0)


def layout_clues(puz, width, height):
    """Choose the largest font (and 1 or 2 columns) that fits the clue box."""
    groups = [("Across", [e for e in puz["entries"] if e["direction"] == "across"]),
              ("Down",   [e for e in puz["entries"] if e["direction"] == "down"])]
    for size in [10, 9.5, 9, 8.5, 8, 7.5, 7, 6.5, 6]:
        for ncols in (1, 2):
            colw = (width - (ncols - 1) * 4 * mm) / ncols
            lead, numw = size * 1.22, stringWidth("00", "Helvetica-Bold", size) + 3
            items = []                      # (kind, payload, height)
            for title, es in groups:
                items.append(("head", title, lead * 1.35))
                for e in es:
                    lines = wrap(clean(e["clue"]), "Helvetica", size, colw - numw)
                    items.append(("clue", (e["humanNumber"], lines), lead * len(lines) + size * 0.25))
            if ncols == 1:
                cols_out = [items]
            else:                           # Across in one column, Down in the other
                split = next(i for i, it in enumerate(items) if it[0] == "head" and it[1] == "Down")
                cols_out = [items[:split], items[split:]]
            if len(cols_out) <= ncols and all(sum(i[2] for i in col) <= height for col in cols_out):
                return size, colw, numw, lead, cols_out
    raise RuntimeError("Clues don't fit even at 6pt")


def draw_clues(c, puz, x0, y_top, width, height):
    size, colw, numw, lead, cols = layout_clues(puz, width, height)
    for ci, col in enumerate(cols):
        x, y = x0 + ci * (colw + 4 * mm), y_top
        for kind, payload, h in col:
            if kind == "head":
                c.setFont("Helvetica-Bold", size + 1)
                c.drawString(x, y - size - 1, payload.upper())
            else:
                num, lines = payload
                c.setFont("Helvetica-Bold", size)
                c.drawString(x, y - size, num)
                c.setFont("Helvetica", size)
                for i, ln in enumerate(lines):
                    c.drawString(x + numw, y - size - i * lead, ln)
            y -= h


def draw_tricks(c, text, x0, y_top, width, height):
    """The Quick Cryptic's 'Today's tricks' box (bold labels kept)."""
    rows = [r.strip() for r in (text or "").split("\n") if r.strip()]
    for size in [8.5, 8, 7.5, 7, 6.5, 6, 5.5]:
        lead, out = size * 1.25, []
        for r in rows:
            m = re.match(r"<b>(.*?)</b>\s*(.*)", r)
            bold, rest = (clean(m.group(1)), clean(m.group(2))) if m else ("", clean(r))
            font = "Helvetica-Oblique" if rest.startswith(("‘", "'")) and not bold else "Helvetica"
            # wrap "BOLD rest" as one paragraph; the bold label sits at the start of line 1
            words = (bold + " " + rest).strip()
            lines = wrap(words, font, size, width) or [""]
            out.append((bold, font, lines))
        if sum(len(l) for _, _, l in out) * lead <= height:
            break
    y = y_top - size
    for bold, font, lines in out:
        for i, ln in enumerate(lines):
            if i == 0 and bold and ln.startswith(bold):
                c.setFont("Helvetica-Bold", size); c.drawString(x0, y, bold)
                bw = stringWidth(bold, "Helvetica-Bold", size)
                c.setFont(font, size); c.drawString(x0 + bw, y, ln[len(bold):])
            else:
                c.setFont(font, size); c.drawString(x0, y, ln)
            y -= lead
        y -= size * 0.15


def draw_puzzle(c, puz, label, x0, y_top, w, h):
    when = dt.datetime.fromtimestamp(puz["date"] / 1000, dt.timezone.utc).strftime("%A %-d %B %Y")
    setter = (puz.get("creator") or {}).get("name", "")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x0, y_top - 13, f"{label} No {puz['number']:,}")
    c.setFont("Helvetica", 9)
    c.drawRightString(x0 + w, y_top - 12, f"{setter}  ·  {when}" if setter else when)
    c.setLineWidth(0.6)
    c.line(x0, y_top - 17, x0 + w, y_top - 17)

    top = y_top - 22
    avail_h = h - 22
    cols, rows = puz["dimensions"]["cols"], puz["dimensions"]["rows"]
    tricks = puz.get("instructions")
    grid_w = 84 * mm if not tricks else 76 * mm
    cell = min(grid_w / cols, (avail_h if not tricks else avail_h - 40 * mm) / rows)
    draw_grid(c, puz, x0, top, cell)
    gw = cell * cols
    if tricks:
        draw_tricks(c, tricks, x0, top - cell * rows - 4 * mm, gw, avail_h - cell * rows - 4 * mm)
    gap = 6 * mm
    draw_clues(c, puz, x0 + gw + gap, top, w - gw - gap, avail_h)


def build_pdf(quick, quiptic, path):
    W, H = A4
    m = 11 * mm
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(f"Guardian Quick Cryptic {quick['number']} & Quiptic {quiptic['number']}")
    half = (H - 2 * m) / 2
    draw_puzzle(c, quick, "Quick Cryptic", m, H - m, W - 2 * m, half - 5 * mm)
    # faint divider
    c.setStrokeGray(0.7); c.setDash(2, 3); c.setLineWidth(0.5)
    c.line(m, H / 2, W - m, H / 2)
    c.setDash(); c.setStrokeGray(0)
    draw_puzzle(c, quiptic, "Quiptic", m, H / 2 - 5 * mm, W - 2 * m, half - 5 * mm)
    c.showPage()
    c.save()


# ---------------------------------------------------------------- email (Resend)
def _addresses(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def send_email(to, subject, text, attachment_path=None):
    key, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("EMAIL_FROM")
    if not (key and sender and to):
        raise RuntimeError("Set RESEND_API_KEY, EMAIL_FROM and EMAIL_TO to send email.")
    body = {"from": sender, "to": to, "subject": subject, "text": text}
    if attachment_path:
        with open(attachment_path, "rb") as f:
            body["attachments"] = [{"filename": os.path.basename(attachment_path),
                                    "content": base64.b64encode(f.read()).decode("ascii")}]
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "guardian-crosswords/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend refused the email ({e.code}): {e.read().decode(errors='replace')}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", type=int, help="Quick Cryptic number (default: latest)")
    ap.add_argument("--quiptic", type=int, help="Quiptic number (default: latest)")
    ap.add_argument("--from-json", nargs=2, metavar=("QUICK.json", "QUIPTIC.json"), help="use saved puzzle data")
    ap.add_argument("--out", default=os.path.expanduser("~/Crosswords"), help="folder for the PDFs")
    ap.add_argument("--print", action="store_true", help="send the PDF to the printer")
    ap.add_argument("--printer", help="printer name (see `lpstat -p`); default printer if omitted")
    ap.add_argument("--email", action="store_true", help="email the PDF to EMAIL_TO via Resend")
    a = ap.parse_args()
    try:
        run(a)
    except (Exception, SystemExit) as e:
        if isinstance(e, SystemExit) and not e.code:
            raise
        msg = str(e.code if isinstance(e, SystemExit) else e) or type(e).__name__
        notify = _addresses(os.environ.get("NOTIFY_TO"))
        if a.email and notify:
            try:
                send_email(notify, "Crossword sheet failed this week",
                           f"The weekly crossword job didn't produce a sheet.\n\nError: {msg}\n")
            except Exception as e2:
                print(f"(Also couldn't send the failure note: {e2})", file=sys.stderr)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)


def run(a):

    if a.from_json:
        quick, quiptic = (json.load(open(f, encoding="utf-8")) for f in a.from_json)
    else:
        quick, quiptic = fetch_puzzle("quick-cryptic", a.quick), fetch_puzzle("quiptic", a.quiptic)

    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"crosswords_QC{quick['number']}_Q{quiptic['number']}.pdf")
    build_pdf(quick, quiptic, path)
    print(f"Saved {path}")

    if a.print:
        cmd = ["lp", "-o", "media=A4", "-o", "fit-to-page"] + (["-d", a.printer] if a.printer else []) + [path]
        subprocess.run(cmd, check=True)
        print("Sent to printer.")

    if a.email:
        to = _addresses(os.environ.get("EMAIL_TO"))
        subject = f"Crosswords: Quick Cryptic {quick['number']} & Quiptic {quiptic['number']:,}"
        res = send_email(to, subject, "This week's crosswords are attached. Happy solving!", path)
        print(f"Emailed to {', '.join(to)} (id {res.get('id', '?')}).")


if __name__ == "__main__":
    main()
