# Crossy: Guardian crosswords on paper

Crossy keeps a small library of Guardian crosswords and lays any selection out on A4, one
puzzle per half page (two per sheet, more run onto extra pages). The default sheet is the
latest **Quick crossword** (top) and the latest **Quick Cryptic** (bottom).

- **Download page** (`server.py`): the last month of puzzles, grouped by type. Tick what you
  want and get one PDF, optionally with an answers page. It also serves a JSON API.
- **Weekly job** (`crossword_sheet.py`): makes the default sheet and can email it (via Resend)
  to a printer, or print it locally.

Each puzzle is fetched from the Guardian once and saved (its data and its own PDF). The Guardian
embeds each puzzle's grid, clues and (once published) answers in the page, so nothing is
screenshotted.

## How the code is laid out

| File | What it does |
|---|---|
| `crossy/sources/` | Where puzzles come from. `guardian.py` reads Guardian pages; `__init__.py` lists the series |
| `crossy/library.py` | Fetch-once history: refresh, list the last month, look up, warnings |
| `crossy/store.py` | Saves puzzle data and PDFs on disk |
| `crossy/pdf.py` | The A4 layout, plus the answers pages |
| `crossy/blog.py` | Finds and reads each cryptic's Fifteensquared write-up (answers and explanations) |
| `crossy/selection.py` | The default pair, used by both the website and the weekly email |
| `crossy/preview.py` | Small SVG grid previews |
| `server.py` | The download page and JSON API |
| `crossword_sheet.py` | Command line and weekly job |

**Series followed:** Quick crossword (Mon to Sat), Quick Cryptic (Saturdays) and Quiptic
(Mondays). The Cryptic, Prize, Everyman and Speedy are also set up: any of their puzzles can be
fetched by id, and listing them is one line (add the key to `DEFAULT_ENABLED` in
`crossy/sources/__init__.py`, or set `CROSSY_SOURCES`).

**Adding another publication:** write a `Source` subclass (see `crossy/sources/base.py`) with
`list_page(page)` and `fetch(number)` returning the normalised puzzle shape described there,
then add it to `ALL` in `crossy/sources/__init__.py`. The library, PDFs and website need no changes.

## Answers and explanations (Fifteensquared)

[Fifteensquared](https://fifteensquared.net) blogs every Guardian cryptic (Quiptic, Quick Cryptic,
Cryptic, Prize), usually within a day or so: the solved grid and how each clue works. When you
ask for answers, Crossy looks for the puzzle's post and, if there is one:

- uses its answers (in preference to the Guardian's), so answers can arrive before the Guardian
  publishes them;
- prints a full answers page for that puzzle: the filled grid, then every clue with its answer,
  the clue with its definition underlined, and the blogger's explanation (running onto a second
  page if the write-up is long). The header says "Fifteensquared" on the right.

Answers come after the puzzles, in the same order. Half-page answers (the Guardian's) pair up
two to a page, and skip over a full Fifteensquared page to share a sheet, to save paper.

If there's no post yet, the Guardian's answers are used as before, on the half-page layout, and
the sheet's notes say so. The blog is checked again after three hours, and given up on two months
after the puzzle came out. A post is found through the blog's search API (by series and number), or its search page and
the post itself when the API answers with a bot check, and read from its text, so it works with the different ways bloggers lay posts out. If a post can't be
read well enough (fewer than 60% of clues), it's ignored. The Quick crossword isn't blogged.

Puzzle ids look like `guardian-quick-17648` (series key, then the puzzle number).

## Saved puzzles

Puzzles are saved in `CROSSY_DATA_DIR`, else the Railway volume if one is attached
(`RAILWAY_VOLUME_MOUNT_PATH`), else `~/.crossy`. On Railway, **attach a volume to `crossy-web`**
(any mount path, e.g. `/data`): without one, saved puzzles vanish whenever the site sleeps and
the first visit after waking fetches the month again (around 20 seconds). The page says so
while there's no volume.

If something can't be fetched or saved, the page shows a warning naming the series or puzzle,
and the API returns it under `warnings`. Failed puzzles are retried on the next check.

## JSON API

| Request | Returns |
|---|---|
| `GET /api/puzzles` | `default` (the default pair), `puzzles` (last 31 days, newest first), `sources`, `warnings`. Takes `?days=` and `?source=` |
| `GET /api/puzzles?source=<key>&page=<n>` | further back: listing page *n* (2 and up) of one series |
| `GET /api/default` | the default pair |
| `GET /api/sources` | the series followed and when each was last checked |
| `GET /api/puzzles/<id>` | one puzzle's data; answers only with `?answers=1` and once published |
| `GET /api/puzzles/<id>.pdf` | that puzzle on its own (saved when fetched) |
| `GET /api/puzzles/<id>/grid.svg` | a small grid preview |
| `GET /sheet.pdf?p=<id>&p=<id>` | one PDF of the selection, in that order. No `p` means the default pair. `&answers=1` adds answers pages, `&view=1` opens it in the browser |

Each puzzle summary has `id`, `source`, `source_name`, `number`, `title`, `setter` (none for the
Quick crossword), `date`, `date_label` (e.g. "Thu 26 Nov"), `cols`, `rows`, `clues`,
`has_tricks`, `solution_available`, `solution_date`, `answers_due`, `url`, `last_downloaded`
(when it last went into a downloaded PDF, for "already printed" markers), `explained` (a
Fifteensquared write-up is saved) and `blog_url`.

`GET /api/puzzles/<id>?answers=1` also looks for the Fifteensquared post and includes it under
`blog` (`url`, `blogger`, and `clues`: per entry id, `answer`, `clue`, `definitions`, `explanation`).

## Railway setup (one time)

1. **New service:** in your Railway project, click *New → GitHub Repo* and pick this repo.
2. **Settings → Deploy:**
   - **Start Command:** `python crossword_sheet.py --email --out /tmp`
   - **Cron Schedule:** `30 6 * * 6` (Saturdays 06:30 UTC, which is 06:30 in winter and 07:30 in summer UK time)
   - **Restart Policy:** Never
3. **Variables:**

   | Name | Value |
   |---|---|
   | `RESEND_API_KEY` | a Resend API key with *Sending access* (make a new one just for this) |
   | `EMAIL_FROM` | e.g. `Crosswords <crosswords@yourdomain.com>` (must be on a domain verified in Resend) |
   | `EMAIL_TO` | the printer's email address. Add your own, comma-separated, if you want a copy |
   | `NOTIFY_TO` | *(optional)* your own address, to be told if a run fails |

4. Deploy. Railway builds the image (it spots `requirements.txt` and installs reportlab).
   To test without waiting for Saturday, temporarily set the cron to a few minutes from
   now (cron times are UTC), check the logs, then set it back.

## Download page (optional second service)

`server.py` is the download page: the last month of puzzles to pick from, one PDF of whatever
you tick, and the JSON API above.

1. In the same Railway project, add **another** service from this repo: *New → GitHub Repo*.
2. **Settings → Deploy:**
   - **Start Command:** `python server.py`
   - **Cron Schedule:** leave empty
   - **Serverless:** on (it sleeps when idle, so it costs next to nothing)
3. **Settings → Networking → Generate Domain** to get its web address.
4. **Variables:** `SITE_PASSWORD`, which is the password your browser asks for (any username works),
   or `OPEN_ACCESS=true` for no password.
5. **Volume:** right-click the service → *Attach volume*, mount path `/data`. Saved puzzles live there.

## Printer setup (Brother Email Print)

- Open the printer's web settings page, go to **Online Functions**, and enable **Email Print**.
  It prints a sheet showing the printer's email address. Put that address in `EMAIL_TO`.
- Set it to print **attachments only**. Otherwise you also get a page with the email text.
- If the printer has an allowed-senders list, add your `EMAIL_FROM` address.

## Running it on your own computer

```sh
pip3 install reportlab
python3 crossword_sheet.py            # the default pair, saved in ~/Crosswords
python3 crossword_sheet.py --answers  # …with an answers page
python3 crossword_sheet.py --print    # …and prints it on the default printer
python3 crossword_sheet.py --list     # the last month of puzzles, with their ids
python3 crossword_sheet.py --puzzle guardian-quiptic-1409 --puzzle guardian-quick-17648
python3 server.py                     # the download page on http://localhost:8000 (set OPEN_ACCESS=true)
```

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

They use made-up puzzles and never touch the network. The PDF tests check page counts and that
everything a puzzle draws stays inside its half of the page, for a 13x13 quick, an 11x11 Quick
Cryptic with its tricks box, and 15x15 cryptics with 32 long clues.

## If it breaks

The script reads the puzzle data the Guardian embeds in each crossword page. If the Guardian
changes how its pages are built, or blocks requests from Railway, the run fails. If
`NOTIFY_TO` is set, you get an email with the error, and Railway's cron logs show the details.
