# Guardian crosswords, printed every Saturday

Every Saturday morning this grabs the latest Guardian **Quick Cryptic** and **Quiptic**,
lays them out one above the other on a single A4 page, and emails the PDF to your
printer, which prints it.

It runs as a Railway cron job and sends through Resend.

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

## Printer setup (Brother Email Print)

- Open the printer's web settings page, go to **Online Functions**, and enable **Email Print**.
  It prints a sheet showing the printer's email address. Put that address in `EMAIL_TO`.
- Set it to print **attachments only**. Otherwise you also get a page with the email text.
- If the printer has an allowed-senders list, add your `EMAIL_FROM` address.

## Running it on your own computer

```sh
pip3 install reportlab
python3 crossword_sheet.py            # saves ~/Crosswords/crosswords_QC…_Q….pdf
python3 crossword_sheet.py --print    # …and prints it on the default printer
python3 crossword_sheet.py --quick 138 --quiptic 1409   # specific puzzles
```

## If it breaks

The script reads the puzzle data the Guardian embeds in each crossword page. If the Guardian
changes how its pages are built, or blocks requests from Railway, the run fails. If
`NOTIFY_TO` is set, you get an email with the error, and Railway's cron logs show the details.
