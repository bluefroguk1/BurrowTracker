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
* **Manual poll:** use the **Poll the store now** button on the page (or Actions → *Poll Oathsworn
  store* → **Run workflow**). If the store was polled less than an hour ago, the run stops
  without fetching anything. Only people with write access to the repo can start a run.
* **New models:** a model gets a **New** badge and goes to the top of the table if it first
  appeared after the previous Friday poll. It keeps the badge until the following Friday's poll.
  Models restocked from zero get a **Back in stock** badge on the same basis.
* **Stock:** read from the store's listing pages. 0 = Out of stock, 1–3 = Low stock, otherwise In stock.
* **Removed models** stay in the data as "No longer listed". They're hidden by default; untick the box to show them.
* **Safety check:** if a poll finds fewer than half the models seen last time (site down or layout
  changed), the data is left unchanged and the run is marked as failed.

The scraper reads about 21 listing pages plus the two starter band pages, one request per second, so a poll takes under a minute.

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
* Faction names: edit the `FACTIONS` map in `scraper/scrape.py`. The store's tags become factions;
  models with no faction tag show as "Unaligned".
* Low-stock threshold: `LOW_STOCK` in `scraper/scrape.py`.
* Tests: `pip install -r scraper/requirements.txt && python -m unittest discover -s scraper/tests`
