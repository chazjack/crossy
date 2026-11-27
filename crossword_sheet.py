#!/usr/bin/env python3
"""
Crossword sheet: puzzles on A4, one per half page (two per sheet).

By default it's the default pair from crossy/selection.py: the latest Guardian Quick crossword
and the latest Quick Cryptic, the same pair the website offers first.

Usage
  python3 crossword_sheet.py                   # default pair, saves a PDF
  python3 crossword_sheet.py --answers         # ...with an answers page (when published)
  python3 crossword_sheet.py --list            # puzzles from the last month, with their ids
  python3 crossword_sheet.py --puzzle guardian-quiptic-1409 --puzzle guardian-quick-17648
  python3 crossword_sheet.py --print           # ...and send it to the default printer
  python3 crossword_sheet.py --print --printer "HP_OfficeJet"
  python3 crossword_sheet.py --from-json a.json b.json   # saved Guardian puzzle JSON (offline / testing)
  python3 crossword_sheet.py --email           # email the PDF via Resend (e.g. to a printer)

Email settings come from environment variables:
  RESEND_API_KEY   your Resend API key
  EMAIL_FROM       sender on your verified Resend domain, e.g. "Crosswords <crosswords@example.com>"
  EMAIL_TO         where to send the PDF; comma-separate several (printer address, yourself, ...)
  NOTIFY_TO        optional: where to send a short note if a run fails

Puzzles are saved in CROSSY_DATA_DIR (default ~/.crossy), so each is fetched once.

Needs: Python 3.9+ and reportlab  (pip3 install reportlab)
"""
import argparse, base64, json, os, subprocess, sys, urllib.error, urllib.request

from crossy import pdf as pdfmod
from crossy import sources as srcmod
from crossy.library import Library
from crossy.selection import default_selection
from crossy.sources.guardian import clean, normalise
from crossy.store import Store


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


# ---------------------------------------------------------------- log output
def print_summary(puz):
    """Write what was fetched to the log, so a run can be checked without the PDF."""
    by = f" by {puz['setter']}" if puz.get("setter") else ""
    print(f"\n=== {puz['title']}{by}, {pdfmod.date_label(puz, 'short')} ({puz['cols']}x{puz['rows']}, "
          f"{len(puz['entries'])} clues, answers {'published' if pdfmod.has_answers(puz) else 'not yet published'}) ===")
    for direction in ("across", "down"):
        print(f"  {direction.upper()}")
        for e in puz["entries"]:
            if e["direction"] == direction:
                print(f"    {e['label']:>3}  {clean(e['clue'])}")
    print(flush=True)


def _from_json(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if "cols" in raw:                     # already normalised
        return raw
    key = f"guardian-{raw.get('crosswordType', 'quick')}"
    return normalise(raw, srcmod.ALL.get(key) or srcmod.ALL["guardian-quick"])


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--puzzle", action="append", metavar="ID",
                    help="puzzle id such as guardian-quick-17648 (repeat for more; default: the default pair)")
    ap.add_argument("--answers", action="store_true", help="add an answers page, for puzzles with published answers")
    ap.add_argument("--list", action="store_true", help="list the last month of puzzles and exit")
    ap.add_argument("--from-json", nargs="+", metavar="FILE", help="use saved puzzle data")
    ap.add_argument("--out", default=os.path.expanduser("~/Crosswords"), help="folder for the PDFs")
    ap.add_argument("--data", help="folder for saved puzzles (default: CROSSY_DATA_DIR or ~/.crossy)")
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
    lib = Library(Store(a.data) if a.data else None)

    if a.list:
        lib.refresh()
        for w in lib.warnings():
            print(f"! {w['message']}")
        for s in lib.recent():
            by = f", {s['setter']}" if s.get("setter") else ""
            print(f"{s['id']:<34} {s['source_name']}, {s['date_label']}{by}")
        return

    if a.from_json:
        puzzles = [_from_json(f) for f in a.from_json]
    else:
        ids = a.puzzle or default_selection(lib, refresh=False)
        if not ids:
            sys.exit("Couldn't find any puzzles: " + "; ".join(w["message"] for w in lib.warnings()))
        puzzles = [lib.get(pid) for pid in ids]

    for puz in puzzles:
        print_summary(puz)

    data = pdfmod.build_pdf(puzzles, answers=a.answers)
    os.makedirs(a.out, exist_ok=True)
    name = "crossy_" + "_".join(f"{p['source'].replace('guardian-', '')}{p['number']}" for p in puzzles)
    path = os.path.join(a.out, name + ("_answers" if a.answers else "") + ".pdf")
    with open(path, "wb") as f:
        f.write(data)
    print(f"Saved {path}")
    if not a.from_json:
        lib.record_download([p["id"] for p in puzzles])

    if a.print:
        cmd = ["lp", "-o", "media=A4", "-o", "fit-to-page"] + (["-d", a.printer] if a.printer else []) + [path]
        subprocess.run(cmd, check=True)
        print("Sent to printer.")

    if a.email:
        to = _addresses(os.environ.get("EMAIL_TO"))
        subject = "Crosswords: " + " & ".join(p["title"].replace(" No ", " ") for p in puzzles)
        res = send_email(to, subject, "This week's crosswords are attached. Happy solving!", path)
        print(f"Emailed to {', '.join(to)} (id {res.get('id', '?')}).")


if __name__ == "__main__":
    main()
