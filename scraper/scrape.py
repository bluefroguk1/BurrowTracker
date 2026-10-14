#!/usr/bin/env python3
"""
Burrows & Badgers model tracker - scraper.

Reads the Burrows & Badgers categories of the Oathsworn Miniatures store and writes
docs/data/models.json, which the GitHub Pages site reads.

Usage:
    python scraper/scrape.py --trigger scheduled   # used by the daily cron
    python scraper/scrape.py --trigger manual      # used by "Run workflow"

Rules:
  * scheduled: runs once a day, at/after 18:30 UK time.
  * manual:    always runs.
  * An item is flagged "New" if it first appeared after the Friday poll before
    the most recent one (i.e. it stays "New" for a week, until the next Friday).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- config
BASE = "https://www.oathswornminiatures.co.uk"
# (category id, url slug, section, label). "models" = the main table; "extras" = the second tab.
# An item listed in several categories goes to "extras" if any of them is an extras category.
CATEGORIES = [
    (4555401, "miniatures", "models", "Miniatures"),
    (4555400, "game-rules-starter-bands", "extras", "Game Rules & Starter Bands"),
    (4562767, "supplements-accessories", "extras", "Supplements & Accessories"),
]
# The store's robots.txt disallows /Listing/, so use the public category pages, which page the same way.
LISTING_URL = BASE + "/c/{cid}/{page}/{slug}.html"
ROBOTS_URL = BASE + "/robots.txt"
CONTENTS_MAX_AGE = timedelta(days=28)  # re-read a starter band's box contents at most every 4 weeks
DATA_FILE = Path(__file__).resolve().parent.parent / "docs" / "data" / "models.json"

UK = ZoneInfo("Europe/London")
FRIDAY = 4                        # "New" badges last a week: from one Friday poll to the next
SCHEDULE_TIME = (18, 30)          # daily scheduled poll, UK local time
REQUEST_DELAY_S = 1.0             # be polite: one page per second
MAX_PAGES = 60
LOW_STOCK = 3                     # 1..LOW_STOCK units => "Low stock"
USER_AGENT = ("Mozilla/5.0 (compatible; BurrowTracker/1.1; weekly stock check; "
              "+https://github.com/bluefroguk1/BurrowTracker)")

SIZES = {"small": "Small", "medium": "Medium", "large": "Large",
         "massive": "Massive", "enormous": "Enormous"}

# The ten Allegiances in the Burrows & Badgers 2nd Edition rulebook. Only these appear as factions.
VALID_FACTIONS = ["Royalists", "Rogues", "Freebeasts", "Kindred", "Witch Hunters", "Wildlings",
                  "Arcane Conclave", "Undead", "Routiers", "Hillfolk"]

# Store tag (lower-case) -> rulebook faction.
FACTIONS = {
    "royalist": "Royalists", "royalists": "Royalists",
    "rogue": "Rogues", "rogues": "Rogues",
    "freebeast": "Freebeasts", "freebeasts": "Freebeasts",
    "kindred": "Kindred",
    "witch hunter": "Witch Hunters", "witch hunters": "Witch Hunters",
    "wildling": "Wildlings", "wildlings": "Wildlings", "old ones": "Wildlings",
    "arcane conclave": "Arcane Conclave",
    "undead": "Undead",
    "routier": "Routiers", "routiers": "Routiers",
    "hillfolk": "Hillfolk", "hillfolk clan": "Hillfolk", "hillfolk clans": "Hillfolk", "clan": "Hillfolk",
}
assert set(FACTIONS.values()) <= set(VALID_FACTIONS)

# Other store tags -> tidy label for the Tags column (anything not listed is title-cased as-is).
NON_SPECIES = {
    "ghast": "Mist Ghast",
    "stable ghast": "Stable Ghast",
    "rare beast": "Rare Beast",
    "magic user": "Magic User",
    "city watch": "City Watch",
    "town watch": "Town Guard",
    "town guard": "Town Guard",
    "freelance": "Freelances",
    "freelances": "Freelances",
    "pirate": "Pirates",
    "pirates": "Pirates",
    "street gang": "Street Gangs",
    "street gangs": "Street Gangs",
    "townsfolk": "Townsfolk",
    "artillery": "Artillery",
    "artillerist": "Artillery",
    "tunneljack": "Tunneljack",
}

# Tags that can appear as a second species (e.g. "Medium, Stoat, Weasel").
SPECIES = {
    "mouse", "dormouse", "shrew", "bird", "sparrow", "bat", "newt", "grubs",
    "hedgehog", "squirrel", "mole", "weasel", "stoat", "black rat", "rat",
    "ferret", "polecat", "cat", "rabbit", "toad", "frog", "adder", "marmot",
    "water vole", "raptor", "hound", "green lizard", "tortoise", "platypus",
    "hare", "wildcat", "brown rat", "fox", "otter", "raccoon", "armadillo",
    "fennec fox", "badger", "beaver", "bear", "cannon", "hornet",
}


# --------------------------------------------------------------------------- helpers
def now_utc() -> datetime:
    override = os.environ.get("BB_NOW")  # for tests: ISO timestamp
    if override:
        return datetime.fromisoformat(override).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def money(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*\.?\d*)", text)
    return round(float(m.group(1).replace(",", "")), 2) if m else None


def title_case(s: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def summary(msg: str) -> None:
    """Print and add to the GitHub Actions run summary."""
    print(msg)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n\n")


# --------------------------------------------------------------------------- parsing
def parse_title(title: str) -> dict:
    """'Mouse Witch Hunter (Small, Mouse, Witch Hunter)' -> name/size/species/factions/tags."""
    title = re.sub(r"\s+", " ", title).strip()
    m = re.search(r"\(([^()]*)\)\s*$", title)
    name = title[: m.start()].strip() if m else title
    tags = [t.strip() for t in m.group(1).split(",") if t.strip()] if m else []

    sizes, species, factions, extra = [], [], [], []
    for tag in tags:
        low = tag.lower()
        if low in SIZES:
            if SIZES[low] not in sizes:
                sizes.append(SIZES[low])
        elif low in NON_SPECIES:
            if NON_SPECIES[low] not in extra:
                extra.append(NON_SPECIES[low])
        elif low in FACTIONS:
            f = FACTIONS[low]
            if f not in factions:
                factions.append(f)
        elif not species:
            species.append(title_case(tag))
        elif low in SPECIES:
            species.append(title_case(tag))
        else:
            extra.append(title_case(tag))
    return {
        "name": name,
        "size": " / ".join(sorted(sizes, key=list(SIZES.values()).index)) or None,
        "species": " / ".join(species) or None,
        "factions": factions,
        "tags": extra,
    }


def stock_status(qty: int | None) -> str:
    if qty is None:
        return "Unknown"
    if qty <= 0:
        return "Out of stock"
    if qty <= LOW_STOCK:
        return "Low stock"
    return "In stock"


def parse_listing(html: str) -> tuple[list[dict], int]:
    """Return (products on this page, highest page number linked)."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for cell in soup.select(".DataViewCell"):
        link = cell.select_one('a[href*="/p/"]')
        if not link:
            continue
        href = link.get("href", "")
        m = re.search(r"/p/(\d+)/", href)
        if not m:
            continue
        pid = m.group(1)
        img = cell.select_one("img")
        short = cell.select_one(".DataViewItemCustomProperty1")
        title = (img.get("alt") if img else None) or (short.get_text(strip=True) if short else pid)
        thumb = (img.get("data-src") or img.get("src")) if img else None
        if thumb and thumb.startswith("/"):
            thumb = BASE + thumb
        full = re.sub(r"/resized/(.+?)_\d+x\d+(\.\w+)$", r"/picture/\1\2", thumb) if thumb else None

        qty_el = cell.select_one("[data-quantity-max]")
        qty = None
        if qty_el is not None:
            try:
                qty = int(qty_el.get("data-quantity-max"))
            except (TypeError, ValueError):
                qty = None
        # Fallback: a visible "Out of Stock" marker.
        oos = cell.select_one(".DataViewOutOfStock")
        if qty is None and oos is not None and "none" not in (oos.get("style") or "").replace(" ", ""):
            qty = 0

        price_ex = money(cell.select_one(".DataViewItemOurPrice").get_text()
                         if cell.select_one(".DataViewItemOurPrice") else None)
        vat = money(cell.select_one(".DataViewItemDomesticTax").get_text()
                    if cell.select_one(".DataViewItemDomesticTax") else None)
        price = round(price_ex + (vat or 0), 2) if price_ex is not None else None

        parsed = parse_title(title)
        items.append({
            "id": pid,
            "title": title,
            **parsed,
            "url": href if href.startswith("http") else BASE + href,
            "image": thumb,
            "image_full": full,
            "price": price,          # inc. VAT
            "price_ex_vat": price_ex,
            "stock_qty": qty,
            "status": stock_status(qty),
        })

    pages = [int(p) for p in re.findall(r"[?&](?:amp;)?page=(\d+)", html)]
    return items, max(pages, default=1)


class StoreBusy(RuntimeError):
    """The store asked us to slow down (429/503) - stop the whole poll rather than retry."""


_robots: RobotFileParser | None = None


def load_robots(session: requests.Session) -> None:
    """Read robots.txt once per poll; every later request is checked against it."""
    global _robots
    _robots = RobotFileParser()
    try:
        resp = session.get(ROBOTS_URL, timeout=30)
        _robots.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except requests.RequestException as exc:
        print(f"  could not read robots.txt ({exc}); continuing with the default category pages only")
        _robots.parse([])


def fetch(session: requests.Session, url: str) -> str:
    if _robots is not None and not _robots.can_fetch(USER_AGENT, url):
        raise RuntimeError(f"robots.txt disallows {url} - not fetching it")
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code in (429, 503):
                wait = resp.headers.get("Retry-After", "")
                raise StoreBusy(f"store answered {resp.status_code} (Retry-After: {wait or 'not given'}) for {url}")
            resp.raise_for_status()
            return resp.text
        except StoreBusy:
            raise
        except requests.RequestException as exc:
            print(f"  retry {attempt + 1} for {url}: {exc}")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}")


CODE_RE = re.compile(r"^(?:[A-Z][A-Za-z]{1,3}\d+[A-Za-z]?(?:-[a-z0-9]+)?)\b")
STOP_RE = re.compile(r"supplied unpainted|please note|price is exclusive", re.I)


def parse_contents(html: str) -> list[dict]:
    """Starter band product page -> [{code, name}] from the 'Contains:' list."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one(".long-description") or soup.select_one(".listing-description")
    if not box:
        return []
    for tag in box.find_all(["div", "p", "li", "br", "tr", "h1", "h2", "h3", "h4"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in box.get_text().split("\n")]
    lines = [ln for ln in lines if ln]
    # Find the "Contains" marker; it may be on its own line or inline ("... Contains: BB214 ...").
    marker = re.compile(r"\bcontains\b\s*:?\s*", re.I)
    idx = next((i for i, ln in enumerate(lines) if marker.search(ln)), None)
    if idx is None:
        return []
    rest = lines[idx][marker.search(lines[idx]).end():]
    tail = ([rest] if rest else []) + lines[idx + 1:]
    if len(tail) == 1:  # everything on one line: split before each product code
        tail = [t.strip() for t in re.split(r"\s(?=[A-Z][A-Za-z]{1,3}\d+[A-Za-z]?(?:-[a-z0-9]+)?\s)", tail[0]) if t.strip()]
    out = []
    for ln in tail:
        if STOP_RE.search(ln):
            ln = STOP_RE.split(ln)[0].strip(" .")
            if ln:
                out.append(ln)
            break
        out.append(ln)
    items = []
    for ln in out:
        m = CODE_RE.match(ln)
        code = m.group(0) if m else None
        name = ln[m.end():].strip(" :-") if m else ln
        if name:
            items.append({"code": code, "name": name})
    return items


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def link_contents(contents: list[dict], models: list[dict]) -> None:
    """Point each starter-band item at the matching model in the Models table, if any."""
    by_name = {}
    for m in models:
        by_name.setdefault(norm(m["name"]), m)
    for c in contents:
        n = norm(c["name"])
        hit = by_name.get(n) or by_name.get(re.sub(r" 1$", "", n))
        c["model_id"] = hit["id"] if hit else None


def scrape(session: requests.Session, previous: dict[str, dict] | None = None,
           now: datetime | None = None) -> list[dict]:
    previous = previous or {}
    now = now or now_utc()
    load_robots(session)
    seen: dict[str, dict] = {}
    requests_made = 1  # robots.txt
    for cid, slug, section, label in CATEGORIES:
        page, last, cat_ids = 1, 1, set()
        while page <= min(last, MAX_PAGES):
            items, max_page = parse_listing(fetch(session, LISTING_URL.format(cid=cid, page=page, slug=slug)))
            requests_made += 1
            last = max(last, max_page)
            fresh = [i for i in items if i["id"] not in cat_ids]
            print(f"  {label} page {page}/{last}: {len(items)} items")
            if not fresh:
                break  # past the end, or the site repeated a page
            for i in fresh:
                cat_ids.add(i["id"])
                rec = seen.setdefault(i["id"], {**i, "section": section, "categories": []})
                rec["categories"].append(label)
                if section == "extras":
                    rec["section"] = "extras"
            page += 1
            time.sleep(REQUEST_DELAY_S)

    items = list(seen.values())
    models = [i for i in items if i["section"] == "models"]
    for i in items:
        if "starter band" not in i["title"].lower():
            continue
        prev = previous.get(i["id"], {})
        checked = prev.get("contents_checked")
        fresh_enough = (prev.get("contents") and checked and prev.get("price") == i["price"]
                        and now - parse_iso(checked) < CONTENTS_MAX_AGE)
        if fresh_enough:
            i["contents"], i["contents_checked"] = prev["contents"], checked
            print(f"  {i['title']}: contents from cache (checked {checked[:10]})")
        else:
            try:
                i["contents"] = parse_contents(fetch(session, i["url"]))
                i["contents_checked"] = iso(now)
                requests_made += 1
            except StoreBusy:
                raise
            except RuntimeError as exc:
                print(f"  could not read contents of {i['title']}: {exc}")
                i["contents"] = prev.get("contents") or []
                i["contents_checked"] = checked
            print(f"  {i['title']}: {len(i['contents'])} items in the box")
            time.sleep(REQUEST_DELAY_S)
        link_contents(i["contents"], models)
    print(f"  {requests_made} requests to the store in total")
    return items


# --------------------------------------------------------------------------- state
def load_state() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"models": [], "friday_polls": [], "polls": []}


def save_state(state: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def should_run(state: dict, trigger: str, now: datetime) -> tuple[bool, str]:
    if trigger == "manual":
        return True, "Manual poll."
    # scheduled: once a day, from 18:30 UK time (the workflow fires at two UTC times to cover BST/GMT)
    local = now.astimezone(UK)
    if (local.hour, local.minute) < SCHEDULE_TIME:
        return False, f"Skipped: scheduled polls run from 18:30 UK time (now {local:%a %H:%M})."
    for p in state.get("polls", []):
        if p.get("trigger") == "scheduled" and parse_iso(p["time"]).astimezone(UK).date() == local.date():
            return False, "Skipped: today's scheduled poll has already run."
    return True, "Scheduled daily poll."


def merge(state: dict, scraped: list[dict], trigger: str, now: datetime) -> tuple[dict, int, int, int]:
    stamp = iso(now)
    first_run = not state.get("models") and not state.get("friday_polls")
    old = {m["id"]: m for m in state.get("models", [])}

    friday_polls = list(state.get("friday_polls", []))
    if first_run:
        state["seed_poll"] = stamp
        friday_polls.append(stamp)          # baseline: nothing is "new" on the first ever run
    elif trigger == "scheduled" and now.astimezone(UK).weekday() == FRIDAY:
        friday_polls.append(stamp)
    friday_polls = friday_polls[-12:]
    cutoff = friday_polls[-2] if len(friday_polls) >= 2 else friday_polls[-1]
    cutoff_dt = parse_iso(cutoff)

    # Categories added to the scraper since the last poll: their items are a baseline, not "New".
    tracked_before = set(state.get("tracked_categories") or (["Miniatures"] if old else []))
    added_cats = {c[-1] for c in CATEGORIES} - tracked_before

    models, new_count, restocked = [], 0, 0
    current_ids = set()
    for item in scraped:
        current_ids.add(item["id"])
        prev = old.get(item["id"], {})
        rec = dict(item)
        baseline = not prev and old and set(item.get("categories", [])) <= added_cats
        rec["first_seen"] = prev.get("first_seen") or (cutoff if baseline else stamp)
        rec["last_seen"] = stamp
        rec["listed"] = True
        rec["restocked_at"] = prev.get("restocked_at")
        if prev and (prev.get("stock_qty") == 0 or not prev.get("listed", True)) and (item["stock_qty"] or 0) > 0:
            rec["restocked_at"] = stamp
            restocked += 1
        rec["is_new"] = parse_iso(rec["first_seen"]) > cutoff_dt
        rec["is_restocked"] = bool(rec["restocked_at"]) and parse_iso(rec["restocked_at"]) > cutoff_dt and not rec["is_new"]
        new_count += rec["is_new"] and not prev
        models.append(rec)

    gone = 0
    for pid, prev in old.items():
        if pid in current_ids:
            continue
        rec = dict(prev)
        if rec.get("listed", True):
            gone += 1
        rec.update(listed=False, status="No longer listed", is_new=False, is_restocked=False)
        models.append(rec)

    models.sort(key=lambda m: (not m["is_new"], not m["listed"], m.get("name", "").lower()))
    polls = (state.get("polls", []) + [{
        "time": stamp, "trigger": trigger, "initial": first_run,
        "count": len(scraped), "new": new_count, "restocked": restocked, "delisted": gone,
    }])[-50:]

    return {
        "source": BASE + "/c/4553237/1/burrows-badgers.html",
        "last_poll": stamp,
        "last_poll_trigger": trigger,
        "seed_poll": state.get("seed_poll", stamp),
        "new_since": cutoff,
        "friday_polls": friday_polls,
        "tracked_categories": [c[-1] for c in CATEGORIES],
        "polls": polls,
        "models": models,
    }, new_count, restocked, gone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trigger", choices=["scheduled", "manual"], default="manual")
    args = ap.parse_args()

    now = now_utc()
    state = load_state()
    ok, reason = should_run(state, args.trigger, now)
    if not ok:
        summary(reason)
        return 0
    summary(f"**{reason}** Started {now.astimezone(UK):%a %d %b %Y %H:%M} UK time.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"})
    try:
        scraped = scrape(session, {m["id"]: m for m in state.get("models", [])}, now)
    except StoreBusy as exc:
        summary(f"Stopped early: {exc}. Nothing was changed; the next poll will try again.")
        return 1

    previous = sum(1 for m in state.get("models", []) if m.get("listed", True))
    if not scraped or (previous >= 20 and len(scraped) < previous * 0.5):
        summary(f"Aborted: only {len(scraped)} models found (previously {previous}). "
                "The store may be down or its layout changed - data left unchanged.")
        return 1

    new_state, new_count, restocked, gone = merge(state, scraped, args.trigger, now)
    save_state(new_state)
    n_models = sum(1 for i in scraped if i["section"] == "models")
    bands = [i for i in scraped if i.get("contents") is not None]
    summary(f"Found **{n_models}** models and **{len(scraped) - n_models}** rules/bands/accessories - "
            f"{new_count} new, {restocked} back in stock, {gone} no longer listed.")
    for b in bands:
        summary(f"* {b['name']}: " + (", ".join(c['name'] for c in b['contents']) or "contents not found"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
