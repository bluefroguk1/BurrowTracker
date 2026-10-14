# BurrowTracker

A filterable list of every **Burrows & Badgers** miniature in the
[Oathsworn Miniatures store](https://www.oathswornminiatures.co.uk/c/4553237/1/burrows-badgers.html),
showing faction, species, size, image, price (inc. VAT), stock level and a link to each model.

A second tab, **Rules, Starter Bands & Accessories**, covers the *Game Rules & Starter Bands* and
*Supplements & Accessories* store categories. Each starter band lists what's in the box, read from its
store page; any item that's also sold on its own links to that model.

Live page: **https://bluefroguk1.github.io/BurrowTracker/**

## How it works

| Piece | File |
|---|---|
| Scraper (Python) | `scraper/scrape.py` |
| Schedule + manual trigger + deploy | `.github/workflows/poll.yml` |
| Web page (GitHub Pages) | `docs/index.html` |
| Data (written by the scraper) | `docs/data/models.json` |

* **Automatic poll:** every Friday at 18:30 UK time, just after the store's 6pm restock.
  GitHub's schedule runs in UTC, so the workflow fires at 17:30 and 18:30 UTC and the
  scraper ignores whichever one isn't 18:30 in the UK that week. GitHub can start scheduled
  runs a few minutes late.
* **Manual poll (repo owner only):** Actions → *Poll Oathsworn store* → **Run workflow**. If the store was
  polled less than an hour ago the run stops without fetching anything, unless you tick
  **Ignore the one-hour limit between polls**. The page has no poll button, so visitors can't start polls.
* **New models:** a model gets a **New** badge and goes to the top of the table if it first
  appeared after the previous Friday poll. It keeps the badge until the following Friday's poll.
  Models restocked from zero get a **Back in stock** badge on the same basis.
* **Stock:** read from the store's listing pages. 0 = Out of stock, 1–3 = Low stock, otherwise In stock.
* **Removed models** stay in the data as "No longer listed". They're hidden by default; untick the box to show them.
* **Safety check:** if a poll finds fewer than half the models seen last time (site down or layout
  changed), the data is left unchanged and the run is marked as failed.

### Being polite to the store

* About 22 requests per poll (robots.txt, ~21 category pages, plus starter band pages only when needed), one per second.
  That's once a week, plus at most one manual poll an hour.
* Only uses URLs the store's `robots.txt` allows. It reads robots.txt at the start of every poll and refuses
  any URL it disallows, so it follows the store's rules if they change.
* Never opens individual product pages for stock or price; the category pages carry both.
  A starter band's contents are re-read only when the band is new, its price changes, or the cached copy is 4 weeks old.
* If the store answers "too many requests" or "unavailable" (HTTP 429/503), the poll stops at once and changes nothing.
* Identifies itself in every request with a link to this repo, so the store owner can see what it is and get in touch.

## One-time setup

1. Upload these files to the repo, keeping the folder structure, including `.github/workflows/poll.yml`.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write permissions** → Save.
4. **Actions** tab → enable workflows if asked → *Poll Oathsworn store* → **Run workflow**.
   This first run takes a baseline snapshot, so nothing is marked New yet.
   The page is live a minute or two later.

## Tweaking

* Store categories scraped: `CATEGORIES` in `scraper/scrape.py` (each one goes to the "models" or "extras" tab).
  When you add a category, its existing items count as the baseline, so they aren't all marked New.
* Factions: only the ten rulebook Allegiances (`VALID_FACTIONS` in `scraper/scrape.py`); `FACTIONS` maps store tags onto them.
  Every other store tag (City Watch, Pirates, Rare Beast…) goes in the Tags column (`NON_SPECIES` tidies their names).
* Low-stock threshold: `LOW_STOCK` in `scraper/scrape.py`.
* Tests: `pip install -r scraper/requirements.txt && python -m unittest discover -s scraper/tests`
