"""The weekly email: its settings, when it's due, and sending it through Resend.

Settings live in the data folder as email.json, edited on the website's /settings page:
  enabled     on or off
  day         0 = Monday ... 6 = Sunday
  time        "HH:MM", UK time, on the half hour (TIMES)
  sources     which series to send (the latest puzzle of each), in sheet order
  recipients  addresses to send to (yourself, a printer's Email Print address, ...)
  answers     add an answers page for puzzles whose answers are already published
  last_sent   {"at", "date", "ids", "to"} for the most recent send

The Railway cron job (crossword_sheet.py --scheduled) asks the website every half hour on
weekday mornings whether the email is due; the website sends it at most once a day.

Sending needs RESEND_API_KEY and EMAIL_FROM. NOTIFY_TO (optional) gets a note when a run fails.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:                         # no tz database: fall back to UTC (an hour out in summer)
    UK = dt.timezone.utc

from crossy.selection import DEFAULT_SOURCES

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIMES = [f"{h:02d}:{m:02d}" for h in range(6, 10) for m in (0, 30)]   # 06:00 ... 09:30
WINDOW = dt.timedelta(minutes=90)         # a late cron run still sends, a much later one doesn't
EMAIL_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")
MAX_RECIPIENTS = 10


def addresses(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def defaults():
    return {"enabled": True, "day": 4, "time": "06:30", "sources": list(DEFAULT_SOURCES),
            "recipients": addresses(os.environ.get("EMAIL_TO")), "answers": False, "last_sent": None}


def time_label(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d}{'am' if h < 12 else 'pm'}"


def summary(settings):
    if not settings["enabled"]:
        return "Off"
    return f"{DAYS[settings['day']]}s, {time_label(settings['time'])}"


# ---------------------------------------------------------------- loading and saving
def path(store):
    return os.path.join(store.root, "email.json")


def load(store, known_sources=None):
    s = defaults()
    saved = store._read_json(path(store), None)
    if isinstance(saved, dict):
        s.update({k: v for k, v in saved.items() if k in s})
    return clean(s, known_sources)


def save(store, settings):
    return store._write_json(path(store), settings)


def clean(s, known_sources=None):
    """Settings with anything odd put right, so a damaged file never stops the email."""
    out = defaults()
    out["enabled"] = bool(s.get("enabled"))
    if isinstance(s.get("day"), int) and 0 <= s["day"] <= 6:
        out["day"] = s["day"]
    if s.get("time") in TIMES:
        out["time"] = s["time"]
    srcs = [k for k in (s.get("sources") or []) if isinstance(k, str)]
    if known_sources is not None:
        srcs = [k for k in srcs if k in known_sources]
    out["sources"] = list(dict.fromkeys(srcs))
    out["recipients"] = list(dict.fromkeys(a for a in (s.get("recipients") or [])
                                           if isinstance(a, str) and EMAIL_RE.match(a)))[:MAX_RECIPIENTS]
    out["answers"] = bool(s.get("answers"))
    out["last_sent"] = s.get("last_sent") if isinstance(s.get("last_sent"), dict) else None
    return out


def from_form(current, form, source_order):
    """New settings from the settings page's form. Returns (settings, problem or None).

    form: {name: [values]} as parse_qs gives it. "remove" drops one address, "add" adds one.
    """
    one = lambda k: (form.get(k) or [""])[0].strip()
    s = dict(current)
    s["enabled"] = one("enabled") == "1"
    if one("day").isdigit() and 0 <= int(one("day")) <= 6:
        s["day"] = int(one("day"))
    if one("time") in TIMES:
        s["time"] = one("time")
    picked = set(form.get("source") or [])
    s["sources"] = [k for k in source_order if k in picked]
    s["answers"] = one("answers") == "1"
    recips = [a for a in current["recipients"] if a != one("remove")]
    problem = None
    new = one("new_address")
    if new:
        if not EMAIL_RE.match(new):
            problem = f"“{new}” doesn't look like an email address."
        elif new in recips:
            pass
        elif len(recips) >= MAX_RECIPIENTS:
            problem = f"That's the most addresses it can send to ({MAX_RECIPIENTS})."
        else:
            recips.append(new)
    s["recipients"] = recips
    return s, problem


# ---------------------------------------------------------------- when it's due
def now_uk():
    return dt.datetime.now(UK)


def next_send(settings, now=None):
    """The next time the email goes, as an aware datetime, or None when it's off."""
    if not settings["enabled"]:
        return None
    now = now or now_uk()
    h, m = (int(x) for x in settings["time"].split(":"))
    for ahead in range(8):
        day = now.date() + dt.timedelta(days=ahead)
        if day.weekday() != settings["day"]:
            continue
        when = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=now.tzinfo)
        sent_today = (settings.get("last_sent") or {}).get("date") == day.isoformat()
        if when + WINDOW > now and not sent_today:
            return when
    return None


def due(settings, now=None):
    """(True, "") when a scheduled run should send now, else (False, why not)."""
    now = now or now_uk()
    if not settings["enabled"]:
        return False, "the weekly email is off"
    if not settings["sources"]:
        return False, "no puzzles are ticked"
    if not settings["recipients"]:
        return False, "there's nobody to send it to"
    if now.weekday() != settings["day"]:
        return False, f"it goes on {DAYS[settings['day']]}s"
    h, m = (int(x) for x in settings["time"].split(":"))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if (settings.get("last_sent") or {}).get("date") == now.date().isoformat():
        return False, "it has already gone today"
    if now < slot:
        return False, f"it goes at {time_label(settings['time'])}"
    if now >= slot + WINDOW:
        return False, f"{time_label(settings['time'])} was too long ago"
    return True, ""


def subject(puzzles):
    return " & ".join(p["title"].replace(" No ", " ").replace("Guardian ", "") for p in puzzles)


# ---------------------------------------------------------------- sending (Resend)
def send(to, subject, text, attachment=None):
    """attachment: (filename, bytes). Returns Resend's reply. Raises RuntimeError with the reason."""
    key, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("EMAIL_FROM")
    if not (key and sender and to):
        missing = [n for n, v in (("RESEND_API_KEY", key), ("EMAIL_FROM", sender), ("a recipient", to)) if not v]
        raise RuntimeError("Can't send email: missing " + ", ".join(missing) + ".")
    body = {"from": sender, "to": to, "subject": subject, "text": text}
    if attachment:
        name, data = attachment
        body["attachments"] = [{"filename": name, "content": base64.b64encode(data).decode("ascii")}]
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "crossy/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Resend refused the email ({e.code}): {e.read().decode(errors='replace')}")


def send_sheet(lib, settings, scheduled=True, now=None):
    """Build the sheet from the settings and email it. Returns (ids, recipients, notes).
    A scheduled send is recorded in settings["last_sent"], so it goes at most once a day;
    a test send isn't, so it never stops that day's email."""
    from crossy.selection import latest_ids
    ids = latest_ids(lib, settings["sources"])
    if not ids:
        raise RuntimeError("couldn't find any puzzles: " + "; ".join(w["message"] for w in lib.warnings()))
    pdf, name, notes = lib.sheet(ids, answers=settings["answers"])
    puzzles = [lib.get(i) for i in ids]
    to = settings["recipients"]
    text = "This week's crosswords are attached. Happy solving!"
    if notes:
        text += "\n\n" + " ".join(notes)
    res = send(to, subject(puzzles), text, (name, pdf))
    now = now or now_uk()
    if not scheduled:
        return ids, to, notes
    settings["last_sent"] = {"at": now.isoformat(timespec="minutes"), "date": now.date().isoformat(),
                             "ids": ids, "to": to, "id": res.get("id")}
    return ids, to, notes
