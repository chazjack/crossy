#!/usr/bin/env python3
"""
A small private web page (and JSON API) for the crossword library.

Pages
  GET /                         puzzles from the last month, ticked ones go into one PDF
  GET /?source=<key>&page=<n>   further back in one series (n = listing page, 2 and up)
  GET /sheet.pdf                the default pair; or ?p=<id>&p=<id>... for any selection,
                                &answers=1 for an answers page, &view=1 to open it in the browser
  GET /settings                 weekly email settings (POST saves them, or sends a test)
  GET /health                   plain "ok", for Railway health checks
  GET /manifest.webmanifest, /icon.svg, /apple-touch-icon.png, /icon-*.png
                                home-screen app and tab icons (no password needed)

JSON API (for the website redesign)
  GET /api/puzzles              {"default": [...], "puzzles": [...], "sources": [...], "warnings": [...]}
                                ?days=31, ?source=<key>; ?source=<key>&page=<n> for older listing pages
  GET /api/default              the default selection, with summaries
  GET /api/sources              the series being followed and their status
  GET /api/puzzles/<id>         one puzzle's data (answers only with ?answers=1, once published)
  GET /api/puzzles/<id>.pdf     that puzzle on its own (saved when it was fetched)
  GET /api/puzzles/<id>/grid.svg  small grid preview

Weekly email (called by the Railway cron job, never by a browser)
  POST /api/email/run           sends the weekly email if it's due; needs header X-Crossy-Token: CRON_TOKEN

Puzzle summaries carry: id, source, source_name, number, title, setter, date, date_label,
cols, rows, clues, has_tricks, solution_available, solution_date, answers_due, url, last_downloaded,
explained (a Fifteensquared write-up is saved) and blog_url.

Environment:
  PORT             set by Railway
  SITE_PASSWORD    the password the browser asks for (any username works); the page stays locked without it
  OPEN_ACCESS      set to true to run with no password at all
  CROSSY_DATA_DIR  where puzzles are saved (Railway volumes are picked up automatically)
  CRON_TOKEN       shared secret the weekly job sends to /api/email/run
  RESEND_API_KEY, EMAIL_FROM, NOTIFY_TO, EMAIL_TO   email settings (see crossy/emailer.py)
"""
import base64, datetime as dt, hmac, html, json, os, re, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from crossy import emailer
from crossy import pdf as pdfmod
from crossy.library import Library
from crossy.preview import grid_svg
from crossy.selection import default_selection
from crossy.sources import FetchError, NotFound

PASSWORD = os.environ.get("SITE_PASSWORD", "")
OPEN = os.environ.get("OPEN_ACCESS", "").strip().lower() in ("1", "true", "yes")
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")

LIBRARY = None
EMAIL_LOCK = threading.Lock()
LAST_TEST = 0.0
LAST_NOW = 0.0


def library():
    global LIBRARY
    if LIBRARY is None:
        LIBRARY = Library()
    return LIBRARY


STYLE = """
  :root { color-scheme: light dark; --ink:#1b1b1b; --paper:#faf8f3; --rule:#d9d4c7; --accent:#052962;
          --warn:#8a4b00; --warnbg:#fff3df; --tagbg:#e8eef8; --ring:transparent; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme=light]) { color-scheme: dark; --ink:#ece9e1; --paper:#171717; --rule:#3a3a3a; --accent:#8fb4ff;
          --warn:#ffcf8a; --warnbg:#3a2a12; --tagbg:#1f2a3d; --ring:#ece9e1; } }
  :root[data-theme=light] { color-scheme: light; }
  :root[data-theme=dark] { color-scheme: dark; --ink:#ece9e1; --paper:#171717; --rule:#3a3a3a; --accent:#8fb4ff;
          --warn:#ffcf8a; --warnbg:#3a2a12; --tagbg:#1f2a3d; --ring:#ece9e1; }
  * { box-sizing: border-box; }
  body { margin:0; font: 16px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color:var(--ink);
         background:var(--paper); -webkit-text-size-adjust: 100%; }
  main { position: relative; max-width: 34rem; margin: 0 auto; padding: 2.25rem 1.25rem 3rem; }
  h1 { font-size: 2rem; font-weight: 750; margin: 0; letter-spacing: -.02em; display:flex; align-items:center; gap:.5rem; flex:1; min-width:0; }
  .top { display:flex; align-items:center; gap:.5rem; margin: 0 0 1.75rem; }
  h1 .logo { width: 2.1rem; height: 2.1rem; flex: none; margin-right: .2rem; box-shadow: 0 0 0 1.5px var(--ring); }   /* keeps the dark frame visible on a dark page */
  h2 { font-size: 1.05rem; font-weight: 700; margin: 2rem 0 .3rem; display:flex; align-items:center; gap:.5rem; }
  .sub { margin: 0 0 1.4rem; opacity: .8; }
  button { font-family: inherit; font-weight: 600; font-size: 1rem; line-height: 1; padding: .95rem 1.4rem; border-radius: .55rem;
           border: 2px solid var(--accent); background: var(--accent); color: var(--paper); cursor: pointer; }
  .row { display:flex; gap:1rem; flex-wrap: wrap; align-items:center; }
  .opts.answers { margin: 0 0 1rem; }
  .buttons { flex-wrap: nowrap; gap:.75rem; }
  .buttons button { flex: 1 1 0; min-width: 0; padding-left: .75rem; padding-right: .75rem; }
  button.alt { background: var(--tagbg); color: var(--accent); border-color: var(--tagbg); }
  button.alt:disabled { opacity: .6; cursor: default; }
  .opts { display:flex; gap:.5rem; align-items:center; font-size: .95rem; cursor:pointer; }
  .opts input, li input { width:1.25rem; height:1.25rem; accent-color: var(--accent); flex: none; margin:0; }
  ul { list-style: none; padding: 0; margin: 0; }
  li { border-bottom: 1px solid var(--rule); }
  li > label, li > .failed { display:flex; gap:.75rem; align-items:center; padding:.6rem 0; cursor:pointer; }
  .what { flex: 1; min-width: 0; }
  .meta { opacity:.65; font-size:.85rem; display:block; }
  .tag { font-weight: 700; font-size: .72rem; line-height: 1; border-radius: 1rem; padding:.28rem .5rem; flex:none;
         background: var(--tagbg); color: var(--accent); }
  .tag.a { width:1.5rem; height:1.5rem; padding:0; display:inline-flex; align-items:center; justify-content:center; }
  details.warn { display:inline-block; }
  details.warn summary { list-style:none; cursor:pointer; width:1.5rem; height:1.5rem; border-radius:50%;
         background: var(--warnbg); color: var(--warn); font-weight: 800; font-size: .9rem; line-height: 1.5rem; text-align:center; }
  details.warn summary::-webkit-details-marker { display:none; }
  details.warn[open] summary { outline: 2px solid var(--warn); }
  .warntext { font-size:.88rem; font-weight: 400; letter-spacing: 0; color: var(--warn); background: var(--warnbg);
         border-radius:.5rem; padding:.55rem .75rem; margin:.35rem 0 .5rem; }
  li .failed { cursor:default; opacity:.85; }
  li .failed .box { width:1.25rem; height:1.25rem; flex:none; }
  a { color: var(--accent); }
  .more { font-size:.9rem; display:inline-block; margin-top:.6rem; }
  #status { font-size: .95rem; min-height: 1.4em; margin-top: .7rem; }
  .readyhead { margin: 0 0 .3rem; font-weight: 650; }
  #ready { list-style: disc; padding-left: 1.25rem; margin: 0 0 1.3rem; }
  #ready li { border: 0; padding: .12rem 0; }
  #ready .meta { display: block; }
  .listhead { margin: 2rem 0 .3rem; position: relative; display:flex; align-items:center; gap:.5rem; }
  .picker { position: relative; }
  #type { display:inline-flex; align-items:center; margin-left:-.6rem; padding:.35rem .6rem; cursor:pointer;
         border:0; border-radius:.55rem; background:transparent; color:var(--ink); font-size:1.05rem; font-weight:700; line-height:1.3; }
  #type:hover, #type[aria-expanded=true] { background: var(--tagbg); color: var(--accent); }
  #type:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .menu { position:absolute; z-index:5; top:calc(100% + .3rem); left:-.6rem; min-width:15rem; max-width:calc(100vw - 2.5rem);
         padding:.3rem; border:1px solid var(--rule); border-radius:.8rem; background:var(--paper);
         box-shadow:0 10px 28px rgba(0,0,0,.14), 0 2px 6px rgba(0,0,0,.06); }
  .menu[hidden] { display:none; }
  .menu button { display:flex; align-items:center; justify-content:space-between; gap:1rem; width:100%; padding:.7rem .8rem;
         border:0; border-radius:.55rem; background:transparent; color:var(--ink); font-size:.97rem; font-weight:500;
         line-height:1.2; text-align:left; }
  .menu button:hover, .menu button:focus-visible { background:var(--tagbg); outline:none; }
  .menu button[aria-selected=true] { color:var(--accent); font-weight:700; }
  .menu .count { opacity:.6; font-size:.85rem; font-weight:500; }
  .showmore { display:block; width:100%; margin-top:.9rem; height:2.75rem; padding:0 1rem; border-radius:.55rem; border:0;
         background:var(--tagbg); color:var(--accent); font-weight:600; }
  .showmore:hover { filter: brightness(.97); }
  .showmore[hidden] { display:none; }
  li[hidden] { display: none; }
  .emailstrip { display:flex; align-items:center; gap:.75rem; margin-top:1.4rem; padding:.75rem .9rem; border-radius:.55rem;
         background: var(--tagbg); color: var(--accent); text-decoration:none; font-size:.95rem; }
  .emailstrip span { flex:1; }
  .emailstrip svg { flex:none; }
  /* settings */
  .back { display:inline-flex; align-items:center; gap:.25rem; min-height:2.75rem; margin-left:-.35rem; font-weight:600;
         text-decoration:none; }
  .setrow { display:flex; align-items:center; gap:.75rem; min-height:3.5rem; padding:.5rem 0; border-bottom:1px solid var(--rule); }
  .setrow > .what { flex:1; }
  .switch { position:relative; flex:none; width:3.25rem; height:2rem; }
  .switch input { position:absolute; inset:0; width:100%; height:100%; margin:0; opacity:0; cursor:pointer; z-index:1; }
  .switch span { position:absolute; inset:0; border-radius:1rem; background:var(--rule); transition:background .15s; }
  .switch span::after { content:""; position:absolute; top:.1875rem; left:.1875rem; width:1.625rem; height:1.625rem;
         border-radius:50%; background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.25); transition:transform .15s; }
  .switch input:checked + span { background:var(--accent); }
  .switch input:checked + span::after { transform:translateX(1.25rem); }
  .switch input:focus-visible + span { outline:2px solid var(--accent); outline-offset:2px; }
  .days { display:grid; grid-template-columns:repeat(7, minmax(0, 1fr)); gap:.375rem; border:0; padding:0; margin:0; }
  .days label { position:relative; }
  .days input { position:absolute; opacity:0; width:1px; height:1px; }
  .days span { display:flex; align-items:center; justify-content:center; height:2.75rem; border-radius:.55rem; font-weight:600;
         border:1px solid var(--rule); background:var(--paper); cursor:pointer; }
  .days input:checked + span { background:var(--accent); border-color:var(--accent); color:var(--paper); }
  .days input:focus-visible + span { outline:2px solid var(--accent); outline-offset:2px; }
  .selectwrap { position:relative; display:inline-flex; }
  .selectwrap select { appearance:none; -webkit-appearance:none; height:2.75rem; padding:0 2.75rem 0 .9rem; border-radius:.55rem;
         border:1px solid var(--rule); background:var(--paper); color:var(--ink); font:inherit; font-weight:600; cursor:pointer; }
  .selectwrap.block { display:flex; margin-top:.5rem; }
  .selectwrap.block select { width:100%; }
  .selectwrap svg { position:absolute; right:.875rem; top:.875rem; pointer-events:none; }
  .body { transition: opacity .15s; }
  .body.off { opacity: .4; }
  .recips li:last-child { border-bottom: 0; }
  button:disabled { opacity: .45; cursor: default; }
  .addrow { display:flex; gap:.5rem; padding:.9rem 0 .25rem; }
  .addrow input { flex:1; min-width:0; height:2.75rem; padding:0 .75rem; border-radius:.55rem; border:1px solid var(--rule);
         background:var(--paper); color:var(--ink); font:inherit; }
  button.soft { background:var(--tagbg); color:var(--accent); border-color:var(--tagbg); padding:0 1rem; height:2.75rem; }
  button.ghost { background:transparent; color:var(--accent); }
  button.x { background:none; border:0; color:var(--ink); opacity:.6; width:2.75rem; height:2.75rem; padding:0;
         display:flex; align-items:center; justify-content:center; }
  .notice { border-radius:.5rem; padding:.6rem .8rem; margin:0 0 1rem; background:var(--tagbg); color:var(--accent); }
  .notice.bad { background:var(--warnbg); color:var(--warn); }
  /* settings slides in from the right, and back out again (browsers without view transitions just switch) */
  @view-transition { navigation: auto; }
  @keyframes from-right { from { transform: translateX(100%); } }
  @keyframes to-left { to { transform: translateX(-30%); } }
  @keyframes from-left { from { transform: translateX(-30%); } }
  @keyframes to-right { to { transform: translateX(100%); } }
  html:active-view-transition-type(forward)::view-transition-new(root) { animation: .3s cubic-bezier(.2,.7,.2,1) both from-right; }
  html:active-view-transition-type(forward)::view-transition-old(root) { animation: .3s cubic-bezier(.2,.7,.2,1) both to-left; }
  html:active-view-transition-type(back)::view-transition-new(root) { animation: .3s cubic-bezier(.2,.7,.2,1) both from-left; }
  html:active-view-transition-type(back)::view-transition-old(root) { animation: .3s cubic-bezier(.2,.7,.2,1) both to-right;
         z-index: 1; }
  @media (prefers-reduced-motion: reduce) { ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; } }
  #theme { flex:none; width:2.5rem; height:2.5rem; margin-right:-.6rem; padding:0; font-size:1.15rem; line-height:1;
         display:flex; align-items:center; justify-content:center; background:transparent; color:var(--ink); border:0; opacity:.6; }
  #theme:hover, #theme:focus-visible { opacity:1; }
"""

# Picks the slide direction: into /settings slides forward, out of it slides back.
SLIDE_JS = """<script>
  addEventListener('pagereveal', e => {
    if (!e.viewTransition || !window.navigation || !navigation.activation || !navigation.activation.from) return;
    const from = new URL(navigation.activation.from.url).pathname, to = location.pathname;
    e.viewTransition.types.add(to === '/settings' && from !== '/settings' ? 'forward'
                               : from === '/settings' && to !== '/settings' ? 'back' : 'none');
  });
</script>"""

MAIL_ICON = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2">'
             '</rect><path d="M3 7l9 6 9-6"></path></svg>')
CHEVRON = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
           'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{d}"></path></svg>')

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Crossy</title>
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="icon" href="/icon-192.png" type="image/png" sizes="192x192">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#faf8f3" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#171717" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Crossy">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<script>try {{ const t = localStorage.theme; if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>
<style>{style}</style>{slide}</head>
<body><main>
  <div class="top">
    <h1><img class="logo" src="/logo-mark.svg" width="34" height="34" alt="">Crossy {general}</h1>
    <button id="theme" type="button" aria-label="Switch light or dark"></button>
  </div>
  {general_text}
  <form action="/sheet.pdf" method="get" onsubmit="return busy()">
    <input type="hidden" name="form" value="1">
    <p class="readyhead">Ready to download</p>
    <ul id="ready">{ready}</ul>
    <label class="opts answers"><input type="checkbox" name="answers" value="1"> Include answers</label>
    <div class="row buttons">
      <button type="submit">Download PDF</button>
      <button type="button" id="emailnow" class="alt">Email now</button>
    </div>
    <div id="status" role="status"></div>
    <a class="emailstrip" href="/settings">{mail_icon}<span><strong>Weekly email:</strong> {email_summary}</span>{chevron}</a>
    {listing}
  </form>
</main>
<script>
  // light/dark follows the system until the button is used, then remembers the choice
  const root = document.documentElement, themeBtn = document.getElementById('theme');
  const dark = () => root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const SUN = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>';
  const MOON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>';
  const showTheme = () => {{ themeBtn.innerHTML = dark() ? SUN : MOON; }};
  themeBtn.addEventListener('click', () => {{
    root.dataset.theme = dark() ? 'light' : 'dark';
    try {{ localStorage.theme = root.dataset.theme; }} catch (e) {{}}
    showTheme();
  }});
  showTheme();
  const esc = s => s.replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
  const emailNow = document.getElementById('emailnow');
  emailNow.addEventListener('click', async () => {{
    const s = document.getElementById('status');
    const ticked = [...document.querySelectorAll('input[name=p]:checked')];
    if (!ticked.length) {{ s.textContent = 'Tick at least one puzzle.'; return; }}
    const body = new URLSearchParams();
    ticked.forEach(i => body.append('p', i.value));
    if (document.querySelector('input[name=answers]').checked) body.append('answers', '1');
    emailNow.disabled = true; s.textContent = 'Emailing…';
    try {{
      const r = await fetch('/email/now', {{ method: 'POST', body }});
      const j = await r.json().catch(() => ({{}}));
      s.textContent = j.message || (r.ok ? 'Sent.' : 'Could not send that.');
    }} catch (e) {{ s.textContent = 'Could not reach the server.'; }}
    emailNow.disabled = false;
  }});
  function busy() {{
    const s = document.getElementById('status');
    if (!document.querySelector('input[name=p]:checked')) {{ s.textContent = 'Tick at least one puzzle.'; return false; }}
    s.textContent = 'Making the PDF…';
    setTimeout(() => {{ s.textContent = ''; }}, 6000);
    return true;
  }}
  // "Ready to download" lists whatever is ticked, in sheet order
  function ready() {{
    const ticked = [...document.querySelectorAll('input[name=p]:checked')];
    document.getElementById('ready').innerHTML = ticked.length
      ? ticked.map(i => `<li>${{esc(i.dataset.name)}}<span class="meta">${{esc(i.dataset.sub)}}</span></li>`).join('')
      : '<li class="meta">Nothing ticked yet</li>';
  }}
  document.querySelectorAll('input[name=p]').forEach(i => i.addEventListener('change', ready));
  // the heading picks which crosswords the list shows, 10 at a time
  const PAGE_SIZE = 10;
  const type = document.getElementById('type'), menu = document.getElementById('menu');
  const showMore = document.getElementById('showmore');
  const rows = [...document.querySelectorAll('#puzzles > li')];
  let filter = 'all', shown = PAGE_SIZE;
  function paint() {{
    let n = 0;
    rows.forEach(li => {{
      const match = filter === 'all' || li.dataset.source === filter;
      if (match) n++;
      li.hidden = !match || n > shown;
    }});
    showMore.hidden = n <= shown;
  }}
  if (type && showMore) {{
    const opts = [...menu.querySelectorAll('[role=option]')];
    const close = (focus) => {{ menu.hidden = true; type.setAttribute('aria-expanded', 'false'); if (focus) type.focus(); }};
    const open = () => {{ menu.hidden = false; type.setAttribute('aria-expanded', 'true');
                         (opts.find(o => o.getAttribute('aria-selected') === 'true') || opts[0]).focus(); }};
    type.addEventListener('click', () => menu.hidden ? open() : close());
    opts.forEach(o => o.addEventListener('click', () => {{
      filter = o.dataset.value; shown = PAGE_SIZE;
      opts.forEach(x => x.setAttribute('aria-selected', String(x === o)));
      document.getElementById('typelabel').textContent = o.dataset.label;
      close(true); paint();
    }}));
    menu.addEventListener('keydown', e => {{
      const i = opts.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') {{ e.preventDefault(); opts[(i + 1) % opts.length].focus(); }}
      else if (e.key === 'ArrowUp') {{ e.preventDefault(); opts[(i - 1 + opts.length) % opts.length].focus(); }}
      else if (e.key === 'Escape') {{ e.preventDefault(); close(true); }}
      else if (e.key === 'Tab') close(false);
    }});
    document.addEventListener('click', e => {{ if (!menu.hidden && !e.target.closest('.picker')) close(false); }});
    showMore.addEventListener('click', () => {{ shown += PAGE_SIZE; paint(); }});
    paint();
  }}
  // a "!" opens its explanation underneath
  document.querySelectorAll('details.warn').forEach(d => d.addEventListener('toggle', () => {{
    const t = document.getElementById(d.dataset.for);
    if (t) t.hidden = !d.open;
  }}));
</script>
</body></html>
"""

SETTINGS_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Crossy: email settings</title>
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#faf8f3" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#171717" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<script>try {{ const t = localStorage.theme; if (t) document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>
<style>{style}</style>{slide}</head>
<body><main>
  <a class="back" href="/">{back_icon}Crossy</a>
  <h1>Email settings</h1>
  {notice}
  <form method="post" action="/settings">
    <button type="submit" name="action" value="add" tabindex="-1" aria-hidden="true"
            style="position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden">Save</button>
    <div class="setrow">
      <span class="what"><strong>Weekly email</strong><span class="meta">{next_line}</span></span>
      <label class="switch"><input type="checkbox" id="enabled" name="enabled" value="1" aria-label="Weekly email"{enabled}><span></span></label>
    </div>

    <div id="body" class="body{off}"{inert}>
      <h2>Send to</h2>
      <ul class="recips">{recipients}</ul>
      <div class="addrow">
        <input type="email" name="new_address" aria-label="Add an email address" placeholder="Add an email address" autocomplete="email">
        <button type="submit" name="action" value="add" class="soft">Add</button>
      </div>

      <h2>Delivery</h2>
      <fieldset class="days"><legend class="meta" style="position:absolute;left:-9999px">Day</legend>{days}</fieldset>
      <span class="selectwrap block"><select name="time" aria-label="Time of day (UK time)">{times}</select>{down_icon}</span>

      <h2>Puzzles</h2>
      <ul>{sources}</ul>
      <div class="setrow">
        <span class="what">Include answers</span>
        <label class="switch"><input type="checkbox" name="answers" value="1" aria-label="Include answers"{answers}><span></span></label>
      </div>
    </div>

    <div class="row" style="margin-top:1.75rem">
      <button type="submit" name="action" value="save">Save</button>
      <button type="submit" name="action" value="test" id="testbtn" class="ghost"{test_disabled}>Send a test now</button>
    </div>
    <p class="meta" style="margin-top:.9rem">{last_line}</p>
  </form>
</main>
<script>
  // switching the weekly email off greys out and locks everything under it (they still save as they were)
  const enabled = document.getElementById('enabled'), body = document.getElementById('body'), testBtn = document.getElementById('testbtn');
  enabled.addEventListener('change', () => {{
    body.classList.toggle('off', !enabled.checked);
    body.inert = !enabled.checked;
    testBtn.disabled = !enabled.checked;
  }});
</script>
</body></html>
"""

ERROR_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crossy: something went wrong</title>
<body style="font:16px/1.5 system-ui,-apple-system,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1.25rem">
<h1>Couldn't make the sheet</h1><p>{msg}</p><p><a href="/">Back</a></p></body>"""

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC = {                       # served without a password, so phones can fetch the icons
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/logo-mark.svg": ("logo-mark.svg", "image/svg+xml"),
    "/favicon.ico": ("icon-192.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
}
MANIFEST = {
    "name": "Crossy", "short_name": "Crossy",
    "description": "Guardian crosswords, ready to print",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#faf8f3", "theme_color": "#faf8f3",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"},
    ],
}

ID_RE = re.compile(r"^/api/puzzles/([a-z0-9-]+?-\d+)(\.pdf|/grid\.svg)?$")


def _esc(s):
    return html.escape(str(s or ""), quote=True)


class _Warn:
    """A "!" that expands to the message(s). The text sits in its own element so it can go
    under a heading or a row rather than inside it."""
    n = 0

    def __init__(self, messages):
        _Warn.n += 1
        self.id = f"w{_Warn.n}"
        self.messages = [m for m in messages if m]

    def badge(self):
        if not self.messages:
            return ""
        return (f'<details class="warn" data-for="{self.id}"><summary aria-label="Problem: show details">!</summary>'
                f'</details>')

    def text(self):
        if not self.messages:
            return ""
        return f'<div class="warntext" id="{self.id}" hidden>' + "<br>".join(_esc(m) for m in self.messages) + "</div>"


def _sub_parts(s):
    """The second line of a row: date, number, setter (setter only when there is one)."""
    return [x for x in (s.get("date_label"), f"No {s['number']:,}" if s.get("number") else "", s.get("setter")) if x]


def _puzzle_row(s, checked):
    parts = _sub_parts(s)
    meta = " - ".join(_esc(x) for x in parts)
    tags = ""
    if s.get("last_downloaded"):
        tags += '<span class="tag" title="Downloaded before">printed</span>'
    if s.get("explained"):
        tags += '<span class="tag a" title="Answers and explanations from Fifteensquared">A</span>'
    elif s.get("solution_available"):
        tags += '<span class="tag a" title="Answers available">A</span>'
    return (f'<li data-source="{_esc(s["source"])}"><label>'
            f'<input type="checkbox" name="p" value="{_esc(s["id"])}" data-name="{_esc(s.get("source_name") or s["title"])}" '
            f'data-sub="{_esc(" - ".join(parts))}"{" checked" if checked else ""}>'
            f'<span class="what">{_esc(s.get("source_name") or s["title"])}<span class="meta">{meta}</span></span>'
            f'{tags}</label></li>')


def _failed_row(src, number, message):
    w = _Warn([message[:1].upper() + message[1:]])
    return (f'<li data-source="{_esc(src.key)}"><div class="failed"><span class="box"></span>'
            f'<span class="what">{_esc(src.name)}<span class="meta">No {int(number):,} - Couldn\'t fetch</span></span>'
            f'{w.badge()}</div>{w.text()}</li>')


def _ready_items(summaries):
    if not summaries:
        return '<li class="meta">Nothing ticked yet</li>'
    return "".join(f'<li>{_esc(s.get("source_name") or s["title"])}'
                   f'<span class="meta">{_esc(" - ".join(_sub_parts(s)))}</span></li>' for s in summaries)


def _listing(lib, items, failed, defaults, order, single=None, page=None):
    """One list, newest first. failed: [(source, number, message)], placed next to their neighbours."""
    rows = [((s.get("date") or ""), -order.get(s["source"], 99), s["number"], _puzzle_row(s, s["id"] in defaults))
            for s in items]
    for src, n, msg in failed:
        newer = [s for s in items if s["source"] == src.key and s["number"] > n]
        date = min(newer, key=lambda s: s["number"])["date"] if newer else "9999"
        rows.append((date, -order.get(src.key, 99), n, _failed_row(src, n, msg)))
    rows.sort(key=lambda r: r[:3], reverse=True)
    body = "".join(r[3] for r in rows) or '<li class="meta">No puzzles saved yet.</li>'
    if single:
        src = lib.source(single)
        head = f'<h2 class="listhead">{_esc(src.name)}, further back</h2>'
        more = (f'<a class="more" href="/?source={_esc(single)}&amp;page={page + 1}">Older</a> · '
                '<a class="more" href="/">Back to this month</a>')
    else:
        counts = {}
        for s in items:
            counts[s["source"]] = counts.get(s["source"], 0) + 1
        def option(value, label, count=None, selected=False):
            n = f'<span class="count">{count}</span>' if count is not None else ""
            return (f'<button type="button" role="option" data-value="{_esc(value)}" data-label="{_esc(label)}" '
                    f'aria-selected="{"true" if selected else "false"}"><span>{_esc(label)}</span>{n}</button>')
        opts = option("all", "All crosswords", selected=True) + "".join(
            option(k, lib.source(k).name, counts.get(k, 0)) for k in lib.enabled)
        head = ('<div class="listhead"><div class="picker">'
                '<button type="button" id="type" aria-haspopup="listbox" aria-expanded="false" '
                'aria-label="Which crosswords to show"><span id="typelabel">All crosswords</span></button>'
                f'<div class="menu" id="menu" role="listbox" hidden>{opts}</div></div></div>')
        more = '<button type="button" id="showmore" class="showmore" hidden>Show more</button>'
    return f'{head}<ul id="puzzles">{body}</ul>{more}'


def render_page(lib, source=None, page=None):
    if not (source and page):
        lib.refresh()
    warnings = lib.warnings()
    general = _Warn([w["message"] for w in warnings if not w["source"] or not w["puzzle"]])
    order = {k: i for i, k in enumerate(lib.enabled)}
    failed = [(lib.source(w["source"]), int(w["puzzle"].rsplit("-", 1)[1]), w["message"].split(": ", 1)[-1])
              for w in warnings if w["puzzle"]]
    if source and page:
        items = lib.older(source, page)
        nums = [s["number"] for s in items]
        failed = [f for f in failed if f[0].key == source and nums and min(nums) <= f[1] <= max(nums)]
        defaults, ready = set(), []
        listing = _listing(lib, items, failed, defaults, order, single=source, page=page)
    else:
        defaults = default_selection(lib)
        items = lib.recent()
        by_id = {s["id"]: s for s in items}
        oldest = {}
        for s in items:
            oldest[s["source"]] = min(oldest.get(s["source"], s["number"]), s["number"])
        failed = [f for f in failed if f[1] >= oldest.get(f[0].key, 0)]
        ready = [by_id[i] for i in defaults if i in by_id]
        listing = _listing(lib, items, failed, set(defaults), order)
    email = emailer.load(lib.store, lib.sources)
    return PAGE.format(style=STYLE, slide=SLIDE_JS, ready=_ready_items(ready), general=general.badge(),
                       general_text=general.text(), listing=listing, mail_icon=MAIL_ICON,
                       chevron=CHEVRON.format(d="M9 18l6-6-6-6"), email_summary=_esc(emailer.summary(email)))


def render_settings(lib, notice="", bad=False):
    st = emailer.load(lib.store, lib.sources)
    nxt = emailer.next_send(st)
    next_line = ("Paused. Nothing will be sent." if not st["enabled"]
                 else f"Next: {nxt:%a} {nxt.day} {nxt:%b}, {emailer.time_label(st['time'])}" if nxt else "")
    days = "".join(f'<label><input type="radio" name="day" value="{i}"{" checked" if st["day"] == i else ""}>'
                   f'<span aria-label="{d}">{d[0]}</span></label>' for i, d in enumerate(emailer.DAYS))
    times = "".join(f'<option value="{t}"{" selected" if st["time"] == t else ""}>{emailer.time_label(t)}</option>'
                    for t in emailer.TIMES)
    sources = "".join(
        f'<li><label><input type="checkbox" name="source" value="{_esc(k)}"{" checked" if k in st["sources"] else ""}>'
        f'<span class="what">{_esc(lib.source(k).name)}</span></label></li>' for k in lib.enabled)
    x_icon = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>')
    recipients = "".join(
        f'<li class="setrow"><span class="what">{_esc(a)}</span>'
        f'<button type="submit" class="x" name="remove" value="{_esc(a)}" aria-label="Remove {_esc(a)}">{x_icon}</button></li>'
        for a in st["recipients"]) or '<li class="setrow meta">No addresses yet</li>'
    last = st.get("last_sent") or {}
    last_line = ""
    if last.get("at"):
        try:
            when = dt.datetime.fromisoformat(last["at"])
            last_line = f"Last sent {when:%a} {when.day} {when:%b}, {emailer.time_label(when.strftime('%H:%M'))}"
        except ValueError:
            pass
    if notice:
        notice = f'<p class="notice{" bad" if bad else ""}" role="status">{_esc(notice)}</p>'
    return SETTINGS_PAGE.format(
        style=STYLE, slide=SLIDE_JS, notice=notice, next_line=_esc(next_line), enabled=" checked" if st["enabled"] else "",
        off="" if st["enabled"] else " off", inert="" if st["enabled"] else " inert",
        test_disabled="" if st["enabled"] else " disabled",
        days=days, times=times, sources=sources, recipients=recipients, answers=" checked" if st["answers"] else "",
        last_line=_esc(last_line), back_icon=CHEVRON.format(d="M15 18l-6-6 6-6"),
        down_icon=CHEVRON.format(d="M6 9l6 6 6-6").replace('width="18" height="18"', 'width="16" height="16"'))


def _strip_answers(puz):
    out = dict(puz)
    out["entries"] = [{k: v for k, v in e.items() if k not in ("solution", "guardian_solution")}
                      for e in puz["entries"]]
    if out.get("blog"):                      # the write-up gives the answers away too
        out["blog"] = {k: v for k, v in out["blog"].items() if k != "clues"}
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
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                return self._send(200, f.read(), ctype, cache="public, max-age=604800")
        if url.path == "/manifest.webmanifest":
            return self._send(200, json.dumps(MANIFEST), "application/manifest+json", cache="public, max-age=3600")
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

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 65536)
            form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
            if url.path == "/api/email/run":
                token = self.headers.get("X-Crossy-Token", "")
                if not CRON_TOKEN or not hmac.compare_digest(token.encode(), CRON_TOKEN.encode()):
                    return self._json({"error": "bad or missing X-Crossy-Token"}, 403)
                return self._email_run(library(), bool(urllib.parse.parse_qs(url.query).get("force")))
            if not PASSWORD and not OPEN:
                return self._send(503, "Locked.", "text/plain")
            if PASSWORD and not self._authed():
                return self._send(401, "Password needed.", "text/plain",
                                  {"WWW-Authenticate": 'Basic realm="Crossy", charset="UTF-8"'})
            if url.path == "/settings":
                return self._save_settings(library(), form)
            if url.path == "/email/now":
                return self._email_now(library(), form)
            return self._send(404, "Not found", "text/plain")
        except BrokenPipeError:
            pass
        except Exception as e:
            print(f"Error on POST {url.path}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            if url.path.startswith("/api/"):
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return self._send(500, ERROR_PAGE.format(msg=_esc(f"{type(e).__name__}: {e}")))

    def _save_settings(self, lib, form):
        action = (form.get("action") or [""])[0]
        with EMAIL_LOCK:
            current = emailer.load(lib.store, lib.sources)
            new, problem = emailer.from_form(current, form, lib.enabled)
            new["last_sent"] = current["last_sent"]
            if problem:
                return self._send(400, render_settings(lib, problem, bad=True))
            if not emailer.save(lib.store, new):
                return self._send(500, render_settings(lib, "Couldn't save: " + str(lib.store.write_error), bad=True))
        if action == "test":
            if not new["sources"] or not new["recipients"]:
                return self._send(400, render_settings(lib, "Tick a puzzle and add an address first.", bad=True))
            global LAST_TEST
            now = dt.datetime.now().timestamp()
            if now - LAST_TEST < 60:
                return self._send(429, render_settings(lib, "A test went less than a minute ago. Give it a moment.", bad=True))
            LAST_TEST = now
            try:
                emailer.send_sheet(lib, new, scheduled=False)
            except Exception as e:
                print(f"Test email failed: {e}", file=sys.stderr, flush=True)
                return self._send(502, render_settings(lib, f"The test didn't send: {e}", bad=True))
            return self._send(303, "", headers={"Location": "/settings?done=sent"})
        if action in ("add",) or (form.get("remove") or [""])[0]:
            return self._send(303, "", headers={"Location": "/settings"})
        return self._send(303, "", headers={"Location": "/settings?done=saved"})

    def _email_now(self, lib, form):
        """"Email now" on the home page: the ticked puzzles, as one PDF, to the saved recipients."""
        global LAST_NOW
        ids = [i for v in form.get("p", []) for i in v.split(",") if i.strip()]
        answers = bool((form.get("answers") or [""])[0])
        st = emailer.load(lib.store, lib.sources)
        if not ids:
            return self._json({"message": "Tick at least one puzzle."}, 400)
        if not st["recipients"]:
            return self._json({"message": "Add an email address in the weekly email settings first."}, 400)
        with EMAIL_LOCK:
            now = dt.datetime.now().timestamp()
            if now - LAST_NOW < 10:
                return self._json({"message": "That only just went. Give it a few seconds."}, 429)
            LAST_NOW = now
        try:
            _, notes = emailer.send_puzzles(lib, ids, answers, st["recipients"])
        except (KeyError, NotFound, FetchError, pdfmod.LayoutError, ValueError) as e:
            return self._json({"message": f"Couldn't make the sheet: {e}"}, 502)
        except Exception as e:
            print(f"Email now failed: {e}", file=sys.stderr, flush=True)
            return self._json({"message": f"Couldn't send: {e}"}, 502)
        lib.record_download(ids)
        n = len(st["recipients"])
        return self._json({"message": f"Emailed to {st['recipients'][0]}" + (f" and {n - 1} more" if n > 1 else "") + "."
                                      + ((" " + " ".join(notes)) if notes else "")})

    def _email_run(self, lib, force=False):
        with EMAIL_LOCK:
            st = emailer.load(lib.store, lib.sources)
            ok, why = (True, "") if force else emailer.due(st)
            if not ok:
                return self._json({"sent": False, "reason": why})
            ids, to, notes = emailer.send_sheet(lib, st, scheduled=True)
            if not emailer.save(lib.store, st):
                print(f"Sent, but couldn't record it: {lib.store.write_error}", file=sys.stderr, flush=True)
        print(f"Weekly email sent to {', '.join(to)}: {' '.join(ids)}", flush=True)
        return self._json({"sent": True, "ids": ids, "to": to, "notes": notes})

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
        if path == "/settings":
            msgs = {"saved": "Saved.", "sent": "Test sent. Check your inbox (and junk)."}
            return self._send(200, render_settings(lib, msgs.get(one("done"), "")))
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
            puz = lib.get(pid, explain=bool(answers) and not suffix)
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
