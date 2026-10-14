#!/usr/bin/env python3
"""
Burrows & Badgers model tracker - scraper.

Reads every product in the Oathsworn Miniatures "Burrows & Badgers > Miniatures"
category and writes docs/data/models.json, which the GitHub Pages table reads.

Usage:
    python scraper/scrape.py --trigger scheduled   # used by the Friday cron
    python scraper/scrape.py --trigger manual      # used by "Run workflow"

Rules:
  * scheduled: only runs on a Friday at/after 18:30 UK time, once per Friday.
  * manual:    refused if the last poll (of any kind) was less than an hour ago.
  * A model is flagged "New" if it first appeared after the Friday poll before
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
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- config
BASE = "https://www.oathswornminiatures.co.uk"
CATEGORY_IDS = [4555401]  # Burrows & Badgers > Miniatures
LISTING_URL = BASE + "/Listing/Category/?categoryId={cid}&page={page}&sortItem=0&sortDirection=0"
DATA_FILE = Path(__file__).resolve().parent.parent / "docs" / "data" / "models.json"

UK = ZoneInfo("Europe/London")
FRIDAY = 4
SCHEDULE_TIME = (18, 30)          # UK local time
MANUAL_COOLDOWN = timedelta(hours=1)
REQUEST_DELAY_S = 1.0             # be polite: one page per second
MAX_PAGES = 60
LOW_STOCK = 3                     # 1..LOW_STOCK units => "Low stock"
USER_AGENT = "Mozilla/5.0 (compatible; BurrowsBadgersModelTracker/1.0; weekly stock check)"

SIZES = {"small": "Small", "medium": "Medium", "large": "Large",
         "massive": "Massive", "enormous": "Enormous"}

# Store tag (lower-case) -> faction shown in the table. Edit freely.
FACTIONS = {
    "city watch": "City Watch",
    "town watch": "Town Guard",
    "town guard": "Town Guard",
    "clan": "Hillfolk Clans",
    "hillfolk": "Hillfolk Clans",
    "hillfolk clan": "Hillfolk Clans",
    "freelances": "Freelances",
    "freelance": "Freelances",
    "old ones": "Old Ones",
    "wildling": "Wildlings",
    "wildlings": "Wildlings",
    "pirate": "Pirates",
    "pirates": "Pirates",
    "routier": "Routiers",
    "routiers": "Routiers",
    "street gang": "Street Gangs",
    "street gangs": "Street Gangs",
    "townsfolk": "Townsfolk",
    "undead": "Undead",
    "witch hunter": "Witch Hunters",
    "witch hunters": "Witch Hunters",
    "artillery": "Artillery",
    "artillerist": "Artillery",
    "royalist": "Royalists",
    "royalists": "Royalists",
    "rogues": "Rogues",
    "freebeasts": "Freebeasts",
    "kindred": "Kindred",
    "arcane conclave": "Arcane Conclave",
}
NO_FACTION = "Unaligned"

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

    size, species, factions, extra = None, [], [], []
    for tag in tags:
        low = tag.lower()
        if low in SIZES and size is None:
            size = SIZES[low]
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
        "size": size,
        "species": " / ".join(species) or None,
        "factions": factions or [NO_FACTION],
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


def scrape(session: requests.Session) -> list[dict]:
    seen: dict[str, dict] = {}
    for cid in CATEGORY_IDS:
        page, last = 1, 1
        while page <= min(last, MAX_PAGES):
            url = LISTING_URL.format(cid=cid, page=page)
            resp = None
            for attempt in range(3):
                try:
                    resp = session.get(url, timeout=30)
                    resp.raise_for_status()
                    break
                except requests.RequestException as exc:
                    print(f"  retry {attempt + 1} for page {page}: {exc}")
                    time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"Could not fetch {url}")
            items, max_page = parse_listing(resp.text)
            last = max(last, max_page)
            fresh = [i for i in items if i["id"] not in seen]
            print(f"  category {cid} page {page}/{last}: {len(items)} items ({len(fresh)} new to this run)")
            if not fresh:
                break  # past the end, or the site repeated a page
            for i in fresh:
                seen[i["id"]] = i
            page += 1
            time.sleep(REQUEST_DELAY_S)
    return list(seen.values())


# --------------------------------------------------------------------------- state
def load_state() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"models": [], "friday_polls": [], "polls": []}


def save_state(state: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def should_run(state: dict, trigger: str, now: datetime) -> tuple[bool, str]:
    last = state.get("last_poll")
    if trigger == "manual":
        if last and now - parse_iso(last) < MANUAL_COOLDOWN:
            nxt = parse_iso(last) + MANUAL_COOLDOWN
            return False, (f"Skipped: the store was last polled at "
                           f"{parse_iso(last).astimezone(UK):%H:%M} UK time. Manual polls are limited "
                           f"to one per hour - try again after {nxt.astimezone(UK):%H:%M}.")
        return True, "Manual poll."
    # scheduled
    local = now.astimezone(UK)
    if local.weekday() != FRIDAY or (local.hour, local.minute) < SCHEDULE_TIME:
        return False, f"Skipped: scheduled polls only run on Fridays from 18:30 UK time (now {local:%a %H:%M})."
    for p in state.get("polls", []):
        if p.get("trigger") == "scheduled" and parse_iso(p["time"]).astimezone(UK).date() == local.date():
            return False, "Skipped: this Friday's scheduled poll has already run."
    return True, "Scheduled Friday poll."


def merge(state: dict, scraped: list[dict], trigger: str, now: datetime) -> tuple[dict, int, int, int]:
    stamp = iso(now)
    first_run = not state.get("models") and not state.get("friday_polls")
    old = {m["id"]: m for m in state.get("models", [])}

    friday_polls = list(state.get("friday_polls", []))
    if first_run:
        state["seed_poll"] = stamp
        friday_polls.append(stamp)          # baseline: nothing is "new" on the first ever run
    elif trigger == "scheduled":
        friday_polls.append(stamp)
    friday_polls = friday_polls[-12:]
    cutoff = friday_polls[-2] if len(friday_polls) >= 2 else friday_polls[-1]
    cutoff_dt = parse_iso(cutoff)

    models, new_count, restocked = [], 0, 0
    current_ids = set()
    for item in scraped:
        current_ids.add(item["id"])
        prev = old.get(item["id"], {})
        rec = dict(item)
        rec["first_seen"] = prev.get("first_seen", stamp)
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
        "source": BASE + "/c/4555401/1/miniatures.html",
        "last_poll": stamp,
        "last_poll_trigger": trigger,
        "seed_poll": state.get("seed_poll", stamp),
        "new_since": cutoff,
        "manual_cooldown_minutes": int(MANUAL_COOLDOWN.total_seconds() // 60),
        "friday_polls": friday_polls,
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
    scraped = scrape(session)

    previous = sum(1 for m in state.get("models", []) if m.get("listed", True))
    if not scraped or (previous >= 20 and len(scraped) < previous * 0.5):
        summary(f"Aborted: only {len(scraped)} models found (previously {previous}). "
                "The store may be down or its layout changed - data left unchanged.")
        return 1

    new_state, new_count, restocked, gone = merge(state, scraped, args.trigger, now)
    save_state(new_state)
    summary(f"Found **{len(scraped)}** models - {new_count} new, {restocked} back in stock, {gone} no longer listed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
