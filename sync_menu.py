#!/usr/bin/env python3
"""
Triton Fuel menu sync
=====================
Reads today's UCSD HDH dining menus and every item's official nutrition facts,
then writes:

  output/menu.json          all halls, meal periods, stations, items and nutrition
  output/Triton Fuel.html   the app with today's menu built in (open it in any browser)

Run it:
    python sync_menu.py                    # every dining hall it can find
    python sync_menu.py --hall OceanView   # just one hall (name match is loose)
    python sync_menu.py --day 1            # tomorrow's menu (0 = today)
    python sync_menu.py --debug            # also save raw pages to debug/ for troubleshooting
    python sync_menu.py --publish docs     # after a good run, copy the results into docs/ (used by GitHub)

How it finds nutrition pages, in order:
  1. Reads the menu page's HTML and looks for links to each item's nutrition page.
  2. If there are none, loads the page in a real (hidden) browser, which runs the
     page's code, and looks again. Needs:  pip install playwright && playwright install chromium
  3. If there are still none, clicks each item in that browser and reads whatever
     nutrition page or pop-up opens.
Nutrition pages themselves are plain HTML and are read directly.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    sys.exit("Missing libraries. Run:  pip install requests beautifulsoup4")

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
DEBUG = HERE / "debug"
CACHE_FILE = HERE / ".nutrition_cache.json"
TEMPLATE = HERE / "app_template.html"

BASE = "https://hdh-web.ucsd.edu"
INDEX_URL = BASE + "/dining/apps/diningservices"
VENUE_URL = BASE + "/dining/apps/diningservices/Restaurants/Venue_V3?locId={locId}&subLocNum=00&locDetID={locDetID}&dayNum={day}"

# Halls we already know; anything else found on the dining index page is added.
KNOWN_HALLS = {  # subLocNum 00 = the whole hall (every restaurant inside it)
    "64 Degrees":    {"locId": "64", "locDetID": "37"},
    "Bistro":        {"locId": "27", "locDetID": "27"},
    "Canyon Vista":  {"locId": "24", "locDetID": "22"},
    "Club Med":      {"locId": "15", "locDetID": "14"},
    "Foodworx":      {"locId": "11", "locDetID": "13"},
    "OceanView":     {"locId": "05", "locDetID": "8"},
    "Pines":         {"locId": "01", "locDetID": "6"},
    "Sixth College": {"locId": "37", "locDetID": "30"},
    "Ventanas":      {"locId": "18", "locDetID": "52"},
}
SKIP_WORDS = ("market", "catering", "coffee cart", "vending")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 TritonFuelPersonal/1.0"),
    "Accept-Language": "en-US,en;q=0.9",
}
PERIOD_RE = re.compile(r"^(breakfast|brunch|lunch|dinner|late\s*night|lunch\s*/?\s*dinner)\s+menu\b", re.I)
PERIOD_NAMES = {"breakfast": "Breakfast", "brunch": "Brunch", "lunch": "Lunch",
                "dinner": "Dinner", "latenight": "Late Night", "lunchdinner": "Lunch/Dinner",
                "lunch/dinner": "Lunch/Dinner"}
NUTRI_URL_RE = re.compile(r"""([^\s"'<>()]*nutritionfacts2[^\s"'<>()]*)""", re.I)
CALS_RE = re.compile(r"(\d[\d,]*)\s*Cals?\b", re.I)
PRICE_RE = re.compile(r"^\$\d")

# Label patterns on HDH nutrition pages -> our field names
NUTRIENT_PATTERNS = [
    ("kcal",  [r"Calories(?!\s*from)"]),
    ("fat",   [r"Total\s+Fat"]),
    ("sat",   [r"Sat(?:urated|\.)?\s*Fat"]),
    ("trans", [r"Trans\s+Fat"]),
    ("chol",  [r"Cholesterol"]),
    ("na",    [r"Sodium"]),
    ("carb",  [r"Tot(?:al|\.)?\s*Carb(?:ohydrates?|s|\.)?", r"Carbohydrates?"]),
    ("fib",   [r"Dietary\s+Fiber", r"Fiber"]),
    ("sug",   [r"Total\s+Sugars?", r"(?<!Added\s)Sugars?"]),
    ("pro",   [r"Protein"]),
]
NUM = r"[\s:<~]*(\d+(?:\.\d+)?)"

session = requests.Session()
session.headers.update(HEADERS)


# ----------------------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(msg, flush=True)


def get(url: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            r = session.get(url, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as e:  # network hiccup: back off and retry
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"Could not load {url}: {last}")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def save_debug(name: str, content: str) -> None:
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / name).write_text(content, encoding="utf-8")


# ----------------------------------------------------------------------------- halls
HOURS: dict[str, list] = {}   # locId -> [{sub, name, hours}] for today, read from the index page
HOURS_RE = re.compile(r"^(.*?)\s+(\d{1,2}:\d\d [AP]M - \d{1,2}:\d\d [AP]M|Closed)$")


def read_hours(soup) -> None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "venue_v3" not in href.lower() or "daynum=0" not in href.lower():
            continue
        q = {k.lower(): v[0] for k, v in parse_qs(urlparse(urljoin(INDEX_URL, href)).query).items()}
        node, text = a, ""
        for _ in range(6):
            node = node.parent
            if node is None:
                break
            text = clean(node.get_text(" "))
            if len(text) > 25:
                break
        text = re.sub(r"\s*Today's Menu\s*$", "", text)
        m = HOURS_RE.match(text)
        if m and "locid" in q:
            HOURS.setdefault(q["locid"], []).append({"sub": q.get("sublocnum", "00"), "name": m.group(1), "hours": m.group(2)})


def discover_halls(debug: bool) -> dict[str, dict]:
    halls = {k: dict(v) for k, v in KNOWN_HALLS.items()}
    try:
        html = get(INDEX_URL)
        if debug:
            save_debug("index.html", html)
        soup = BeautifulSoup(html, "html.parser")
        try:
            read_hours(soup)
        except Exception as e:
            log(f"  (couldn't read today's hours: {e})")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "venue_v3" not in href.lower():
                continue
            q = {k.lower(): v[0] for k, v in parse_qs(urlparse(urljoin(INDEX_URL, href)).query).items()}
            if "locid" not in q or "locdetid" not in q or q.get("sublocnum", "00") != "00":
                continue
            if any(v["locId"] == q["locid"] for v in halls.values()):
                continue
            name = clean(a.get_text(" ")) or clean(a.get("title", "")) or clean(a.get("aria-label", ""))
            if not name or any(w in name.lower() for w in SKIP_WORDS):
                continue
            name = re.sub(r"\s*(menu|hours|view menu)\s*$", "", name, flags=re.I).strip()
            if name and name not in halls and len(name) < 40:
                halls[name] = {"locId": q["locid"], "locDetID": q["locdetid"]}
    except Exception as e:
        log(f"  ! Couldn't read the list of dining halls ({e}); using the halls I already know.")
    return halls


# ----------------------------------------------------------------------------- menu parsing
def find_nutrition_url(tag: Tag, page_url: str) -> str | None:
    for attr, val in tag.attrs.items():
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if isinstance(v, str) and "nutritionfacts2" in v.lower():
                m = NUTRI_URL_RE.search(v)
                if m:
                    return urljoin(page_url, m.group(1).replace("&amp;", "&"))
    return None


def item_container(tag: Tag) -> Tag:
    """Climb from the link to the element that holds the whole item (name, description, Cals)."""
    node = tag
    for _ in range(5):
        parent = node.parent
        if not isinstance(parent, Tag):
            break
        links = [t for t in parent.find_all(True) if find_nutrition_url(t, BASE)]
        if len(links) > 1 and not all(find_nutrition_url(t, BASE) == find_nutrition_url(tag, BASE) for t in links):
            break  # parent holds other items too
        node = parent
        if CALS_RE.search(node.get_text(" ")):
            break
    return node


def describe_item(container: Tag) -> dict:
    lines = [clean(x) for x in container.get_text("\n").split("\n")]
    lines = [x for x in lines if x and x not in ("+", "-")]
    name = lines[0] if lines else "Unnamed item"
    name = CALS_RE.sub("", name).strip()
    name = re.sub(r"\s*\$\d+(\.\d\d)?\s*$", "", name).strip()
    desc = ""
    for ln in lines[1:]:
        if CALS_RE.search(ln) or PRICE_RE.match(ln) or ln == name:
            continue
        desc = ln
        break
    m = CALS_RE.search(container.get_text(" "))
    kcal = int(m.group(1).replace(",", "")) if m else None
    return {"name": name, "desc": desc, "menuKcal": kcal}


def near_link(tag: Tag, page_url: str) -> bool:
    """True if the element is part of a menu item (holds or sits inside a nutrition link)."""
    node = tag
    for _ in range(4):
        if isinstance(node, Tag) and find_nutrition_url(node, page_url):
            return True
        node = node.parent
        if node is None:
            break
    return tag.find(lambda t: find_nutrition_url(t, page_url) is not None) is not None


def looks_like_heading(tag: Tag) -> bool:
    if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    cls = " ".join(tag.get("class", [])).lower()
    if "item" in cls:
        return False
    return any(w in cls for w in ("venue", "station", "category", "heading", "header", "title")) and tag.name not in ("a", "button", "li")


def parse_menu(html: str, page_url: str) -> list[dict]:
    """Walk the page top to bottom, tracking the current meal period and station."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup
    period = None
    stations: list[str] = []
    items: list[dict] = []
    seen: set[tuple] = set()
    for el in root.descendants:
        if not isinstance(el, Tag):
            continue
        text = clean(el.get_text(" "))
        if len(text) <= 40:
            m = PERIOD_RE.match(text)
            if m:
                key = re.sub(r"\s+", "", m.group(1).lower())
                period = PERIOD_NAMES.get(key, m.group(1).title())
                stations = []
                continue
        if looks_like_heading(el) and 0 < len(text) <= 50 and not CALS_RE.search(text) \
                and not PERIOD_RE.match(text) and not near_link(el, page_url):
            if "open all" in text.lower() or text.lower().startswith("our menu"):
                continue
            level = int(el.name[1]) if re.fullmatch(r"h\d", el.name or "") else 9
            stations = [s for s in stations if s[0] < level] + [(level, text)]
            continue
        url = find_nutrition_url(el, page_url)
        if not url:
            continue
        container = item_container(el)
        info = describe_item(container)
        key = (period, url)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            **info,
            "period": period or "Menu",
            "station": " · ".join(s[1] for s in stations[-2:]) or "Menu",
            "url": url,
        })
    return items


# ----------------------------------------------------------------------------- browser fallbacks
def browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def render_with_browser(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=HEADERS["User-Agent"])
        pg.goto(url, wait_until="networkidle", timeout=60000)
        # expand any collapsed sections ("Open All Venues")
        for label in ("Open All Venues", "Open All", "Expand All"):
            try:
                for btn in pg.get_by_text(label, exact=False).all():
                    btn.click(timeout=1500)
            except Exception:
                pass
        pg.wait_for_timeout(800)
        html = pg.content()
        b.close()
        return html


CLICK_JS = r"""
() => {
  const out = [];
  const all = [...document.querySelectorAll('body *')];
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
  let period = null, station = null;
  for (const el of all) {
    const t = (el.innerText || '').trim();
    if (t.length <= 40 && /^(breakfast|brunch|lunch|dinner|late\s*night)\s+menu/i.test(t)) { period = t.replace(/\s+menu.*$/i,''); continue; }
    if (/^H[2-6]$/.test(el.tagName) && t.length <= 50 && !/cals/i.test(t)) { station = t; continue; }
    if (/\d+\s*Cals?\b/i.test(own(el))) {
      const box = el.closest('li, tr, .card, [class*=item], [class*=Item]') || el.parentElement;
      if (!box || box.dataset.tfIdx) continue;
      box.dataset.tfIdx = String(out.length);
      out.push({ idx: out.length, text: box.innerText, period, station });
    }
  }
  return out;
}
"""


def click_through(url: str, debug: bool) -> list[dict]:
    """Last resort: click every item and read the nutrition page/pop-up that opens."""
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent=HEADERS["User-Agent"])
        pg = ctx.new_page()
        pg.goto(url, wait_until="networkidle", timeout=60000)
        found = pg.evaluate(CLICK_JS)
        log(f"    browser found {len(found)} items to click")
        for it in found:
            info = describe_item(BeautifulSoup(f"<div>{it['text'].replace(chr(10), '<br>')}</div>", "html.parser").div)
            rec = {**info, "period": (it["period"] or "Menu").title(), "station": it["station"] or "Menu", "url": None}
            sel = f'[data-tf-idx="{it["idx"]}"]'
            before = pg.url
            try:
                with ctx.expect_page(timeout=2500) as pop:
                    pg.click(sel, timeout=3000)
                newp = pop.value
                newp.wait_for_load_state()
                rec["url"] = newp.url
                rec["pageText"] = newp.inner_text("body")
                newp.close()
            except Exception:
                pg.wait_for_timeout(900)
                if pg.url != before:
                    rec["url"] = pg.url
                    rec["pageText"] = pg.inner_text("body")
                    pg.go_back(wait_until="networkidle")
                    pg.evaluate(CLICK_JS)  # re-tag items after navigation
                else:
                    dialog = pg.locator('[role=dialog], .modal.show, .modal[style*="block"]')
                    if dialog.count():
                        rec["pageText"] = dialog.first.inner_text()
                        pg.keyboard.press("Escape")
            results.append(rec)
        if debug:
            save_debug("clickthrough.json", json.dumps(results, indent=2))
        b.close()
    return results


# ----------------------------------------------------------------------------- nutrition pages
def parse_nutrition_text(text: str) -> dict:
    text = text.replace("\xa0", " ")
    n = {}
    for key, pats in NUTRIENT_PATTERNS:
        for pat in pats:
            m = re.search(pat + NUM, text, re.I)
            if m:
                n[key] = float(m.group(1))
                break
    out = {"n": n}
    m = re.search(r"Serving\s+Size\s*:?\s*([\d.]+\s*[A-Za-z]+(?:\s*\([^)]*\))?)", text, re.I)
    if m:
        out["serving"] = clean(m.group(1))
    m = re.search(r"Allergens(.*?)(Disclaimer|$)", text, re.I | re.S)
    if m:
        tags = re.findall(r"Contains ([A-Z][a-z]+(?: [A-Z][a-z]+)?)|\b(Vegan|Vegetarian)\b", m.group(1))
        tags = list(dict.fromkeys(a or b for a, b in tags))
        if tags:
            out["allergens"] = ", ".join(tags)
    return out


def fetch_nutrition(url: str, cache: dict, debug: bool) -> dict:
    today = dt.date.today().isoformat()
    hit = cache.get(url)
    if hit and hit.get("fetched") == today:   # re-read every page each day so HDH's changes come through
        return hit
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    for s in soup(["script", "style", "noscript"]):
        s.decompose()
    text = soup.get_text("\n")
    rec = parse_nutrition_text(text)
    h = soup.find(["h1", "h2"])
    rec["title"] = clean(h.get_text(" ")) if h else ""
    rec["fetched"] = today
    if debug and not rec["n"].get("kcal"):
        save_debug("nutrition-unparsed-" + hashlib.md5(url.encode()).hexdigest()[:8] + ".html", html)
    cache[url] = rec
    return rec


def item_id(hall: str, url: str | None, name: str) -> str:
    rec = None
    if url:
        q = parse_qs(urlparse(url).query)
        rec = (q.get("recId") or q.get("recid") or [None])[0]
        if not rec:
            m = re.search(r"nutritionfacts2/(\d+)", url, re.I)
            rec = m.group(1) if m else None
    return f"{slug(hall)[:12]}-{rec or hashlib.md5(name.encode()).hexdigest()[:8]}"


# ----------------------------------------------------------------------------- main
def sync_hall(name: str, ids: dict, day: int, cache: dict, debug: bool) -> dict:
    url = VENUE_URL.format(day=day, **ids)
    log(f"\n{name}")
    html = get(url)
    if debug:
        save_debug(f"menu-{slug(name)}.html", html)
    items = parse_menu(html, url)
    method = "page HTML"

    if not items and browser_available():
        log("  no nutrition links in the page HTML; loading it in a browser...")
        html = render_with_browser(url)
        if debug:
            save_debug(f"menu-{slug(name)}-rendered.html", html)
        items = parse_menu(html, url)
        method = "browser"
        if not items:
            log("  still no links; clicking through each item...")
            items = click_through(url, debug)
            method = "click-through"
    elif not items:
        log("  ! No nutrition links in the page HTML. Install the browser helper and run again:\n"
            "      pip install playwright && playwright install chromium")

    log(f"  {len(items)} menu entries found ({method})")
    todo = [it for it in items if it.get("url") and "pageText" not in it]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch_nutrition, it["url"], cache, debug): it for it in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            it = futs[f]
            try:
                it.update(f.result())
            except Exception as e:
                it["error"] = str(e)
            if i % 25 == 0 or i == len(todo):
                log(f"  nutrition facts: {i}/{len(todo)}")
    for it in items:
        if "pageText" in it:
            it.update(parse_nutrition_text(it.pop("pageText")))

    out_items, missing = [], 0
    for it in items:
        n = it.get("n") or {}
        if not n.get("kcal"):
            missing += 1
            if it.get("menuKcal") is None:
                continue
            n = {"kcal": it["menuKcal"]}
        for k in ("kcal", "fat", "sat", "trans", "chol", "na", "carb", "fib", "sug", "pro"):
            n.setdefault(k, None)
        out_items.append({
            "id": item_id(name, it.get("url"), it["name"]),
            "name": it["name"], "desc": it.get("desc", ""),
            "station": it["station"], "period": it["period"],
            "serving": it.get("serving"), "allergens": it.get("allergens"),
            "menuKcal": it.get("menuKcal"), "n": n,
            "complete": all(n.get(k) is not None for k in ("pro", "carb", "fat")),
            "source": it.get("url"),
        })
    if missing:
        log(f"  ! {missing} items had no readable nutrition page (calories from the menu only)")
    hours = HOURS.get(ids["locId"], [])
    whole = next((v for v in hours if v["sub"] == "00"), None)
    return {"name": name, "url": url, "hours": whole["hours"] if whole else None,
            "venues": [{"name": v["name"], "hours": v["hours"]} for v in hours if v["sub"] != "00"],
            "items": out_items}


# The template is written as page content; this wraps it into a full page that phones display correctly.
PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="Track your UCSD dining hall meals with official HDH nutrition facts.">
<meta name="theme-color" content="#F2F2F7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Triton Fuel">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<style>:root{padding-top:var(--safe-area-inset-top,env(safe-area-inset-top,0px))}body{margin:0}</style>
</head>
<body>
"""


def build_app(menu: dict) -> Path | None:
    if not TEMPLATE.exists():
        log("  (app_template.html not found next to this script, so I skipped building the app)")
        return None
    html = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(menu, separators=(",", ":")).replace("</", "<\\/")
    marker = '<script type="application/json" id="menu-data">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    html = html[:start] + payload + html[end:]
    if "<!doctype" not in html[:200].lower():
        html = PAGE_HEAD + html + "\n</body>\n</html>\n"
    path = OUT / "Triton Fuel.html"
    path.write_text(html, encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync UCSD dining menus + nutrition facts for Triton Fuel")
    ap.add_argument("--hall", action="append", help="only these halls (loose name match); repeatable")
    ap.add_argument("--day", type=int, default=0, help="0 = today, 1 = tomorrow, ... up to 6")
    ap.add_argument("--debug", action="store_true", help="save raw pages to debug/ for troubleshooting")
    ap.add_argument("--publish", metavar="FOLDER", help="after a good run, copy menu.json and the app (as index.html) here")
    ap.add_argument("--min-items", type=int, default=100,
                    help="treat the run as failed (and publish nothing) if fewer items than this were found")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    try:
        cache = json.loads(CACHE_FILE.read_text())
    except Exception:
        cache = {}

    halls = discover_halls(args.debug)
    if args.hall:
        want = [h.lower().replace(" ", "") for h in args.hall]
        halls = {k: v for k, v in halls.items() if any(w in k.lower().replace(" ", "") for w in want)}
        if not halls:
            sys.exit("No hall matched. Try without --hall to see which halls are found.")
    log(f"Syncing {len(halls)} hall(s): {', '.join(halls)}")

    menu_date = (dt.date.today() + dt.timedelta(days=args.day)).isoformat()
    result = {"generated": dt.datetime.now().isoformat(timespec="minutes"), "date": menu_date,
              "source": INDEX_URL, "halls": []}
    for name, ids in halls.items():
        try:
            hall = sync_hall(name, ids, args.day, cache, args.debug)
            if hall["items"]:
                result["halls"].append(hall)
        except Exception as e:
            log(f"  ! Skipped {name}: {e}")
        CACHE_FILE.write_text(json.dumps(cache))

    total = sum(len(h["items"]) for h in result["halls"])
    full = sum(1 for h in result["halls"] for it in h["items"] if it["complete"])
    if total < args.min_items or full < total * 0.8:
        # Something is wrong (site down, layout changed...). Keep the last good menu instead of publishing this one.
        log(f"\nFAILED: only {total} items found ({full} with full nutrition facts). Nothing was saved or published.")
        log("Run with --debug and send the debug folder to Claude so the script can be adjusted.")
        sys.exit(1)
    if args.publish:
        # Late in the day most halls have closed and HDH lists fewer items. Don't let a small late run
        # replace a fuller menu for the same day (the app itself is still rebuilt with the latest template).
        try:
            prev = json.loads((Path(args.publish) / "menu.json").read_text(encoding="utf-8"))
            prev_total = sum(len(h["items"]) for h in prev.get("halls", []))
            if prev.get("date") == result["date"] and total < prev_total * 0.7:
                log(f"\nKept the earlier menu for {prev['date']}: it has {prev_total} items, this run found only {total}.")
                result, total = prev, prev_total
                full = sum(1 for h in result["halls"] for it in h["items"] if it.get("complete", True))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    (OUT / "menu.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    app = build_app(result)
    if args.publish:
        pub = Path(args.publish)
        pub.mkdir(parents=True, exist_ok=True)
        (pub / "menu.json").write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
        if app:
            (pub / "index.html").write_text(app.read_text(encoding="utf-8"), encoding="utf-8")
        (pub / ".nojekyll").write_text("", encoding="utf-8")   # tells GitHub Pages to serve files as-is
        log(f"  Published to {pub}/")

    log("\nDone.")
    log(f"  {total} items across {len(result['halls'])} hall(s); {full} with full nutrition facts")
    log(f"  Menu data: {OUT / 'menu.json'}")
    if app:
        log(f"  App:       {app}   (double-click to open)")
    if full < total:
        log(f"\n  Note: {total - full} items had calories only (no readable nutrition page).")


if __name__ == "__main__":
    main()
