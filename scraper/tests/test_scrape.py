"""Offline tests: python -m unittest discover -s scraper/tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scrape  # noqa: E402

# Markup copied from the live store (Ashop), Oct 2026.
CELL = '''<div class="DataViewCell">    <form method="post" action="/Basket/AddToBasket">
<input type="hidden" id="hiddenProductId-{pid}" value="{pid}">
<div class="DataViewItemCustomProperty1">{short}</div>
<div class="DataViewItemAddToCart"><div id="divNotStock-{pid}" class="DataViewOutOfStock" style="display:none">Out of Stock</div><input type="text" id="txtQuantity-{pid}" name="Quantity" size="1" class="wTextField default-value" value="" maxlength="6" data-quantity-max="{qty}"> <input id="btnAddToBasket-{pid}" class="wButton add2cart" onclick="return AddListingItem(this);" value="" type="submit"></div>
<div class="DataViewItemOurPrice">&pound;{price}</div>+<div class="DataViewItemDomesticTax">VAT: &pound;{vat} Exc.</div>
<div class="DataViewItemThumbnailImage"><div style="text-align: center;height: 250px;width: 250px;"><a href="https://www.oathswornminiatures.co.uk/p/{pid}/{slug}.html"><img style="margin-top: expression(( 250 - this.height ) / 2);" data-src="https://static.shop033.com/resources/E9/167913/resized/1C/{img}_250x250.jpg" alt="{alt}" width="212" height="250"></a></div></div>
</form></div>'''


def page(items, last):
    pager = "".join(
        f'<a href="/Listing/Category/?categoryId=4555401&amp;page={n}&amp;sortItem=0&amp;sortDirection=0">{n}</a>'
        for n in range(1, last + 1))
    cells = "".join(CELL.format(**i) for i in items)
    return f"<html><body><div class='DataViewCellOut'>{cells}</div><div class='pager'>{pager}</div></body></html>"


def item(pid, alt, qty=6, price="3.12", vat="0.62"):
    return dict(pid=pid, alt=alt, short=alt.split(" (")[0], qty=qty, price=price, vat=vat,
                slug="x", img=str(80000000 + int(pid) % 1000000))


class FakeResp:
    def __init__(self, text): self.text, self.status_code, self.headers = text, 200, {}
    def raise_for_status(self): pass


EMPTY = "<html><body><div class='DataViewCellOut'></div></body></html>"

# Starter band product page markup copied from the live store (Oct 2026).
BAND_PAGE = """<div class="long-description"><div>Five metal miniatures, three metal Objective markers, and a set of
polyhedral dice, ideal to use as a first Band for the Burrows and Badgers Skirmish Miniatures Game.</div>
<div>Contains:</div><div>BB214<span> </span>Otter Knight 1</div><div>BB215<span> </span>Yorkshire Terrier Knight</div>
<div>BB216<span> </span>Hedgehog Magic User</div><div>Acc15-a<span> </span>Objectives</div><div>Polyhedral dice</div>
<div>SUPPLIED UNPAINTED.<br></div>&nbsp;Please note: Price is exclusive of VAT.<br></div>"""


class FakeSession:
    """pages: {page_no: html} for Miniatures, or {category_id: {page_no: html}}."""
    def __init__(self, pages, products=None):
        self.pages, self.products, self.calls, self.headers = pages, products or {}, [], {}

    def get(self, url, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp("User-agent: *\nDisallow: /Listing/\nDisallow: /Basket/\n")
        if "/p/" in url:
            pid = url.split("/p/")[1].split("/")[0]
            return FakeResp(self.products.get(pid, "<html></html>"))
        assert "/Listing/" not in url, "scraper must not use robots-disallowed URLs"
        cid, n = (int(x) for x in url.split("/c/")[1].split("/")[:2])
        self.calls.append((cid, n))
        nested = isinstance(next(iter(self.pages.values())), dict)
        if nested:
            cat = self.pages.get(cid, {1: EMPTY})
        else:
            cat = self.pages if cid == 4555401 else {1: EMPTY}
        return FakeResp(cat.get(n, cat[max(cat)]))  # site repeats last page


class ParseTitle(unittest.TestCase):
    def test_basic(self):
        r = scrape.parse_title("Mouse Witch Hunter (Small, Mouse, Witch Hunter)")
        self.assertEqual((r["name"], r["size"], r["species"], r["factions"]),
                         ("Mouse Witch Hunter", "Small", "Mouse", ["Witch Hunters"]))

    def test_order_independent_and_unaligned(self):
        r = scrape.parse_title("Cocker Spaniel (Witch Hunter, Large, Hound)")
        self.assertEqual((r["size"], r["species"], r["factions"]), ("Large", "Hound", ["Witch Hunters"]))
        r = scrape.parse_title("Hedgehog Warrioress (Medium, Hedgehog)")
        self.assertEqual(r["factions"], [])

    def test_alt_species_and_tags(self):
        r = scrape.parse_title("Stoat Thief (Medium, Stoat, Weasel, Street Gang)")
        self.assertEqual((r["species"], r["factions"], r["tags"]), ("Stoat / Weasel", [], ["Street Gangs"]))
        r = scrape.parse_title("Sparrow Watchbeast (Small, Sparrow, Bird, City Watch, Town Guard)")
        self.assertEqual((r["factions"], r["tags"]), ([], ["City Watch", "Town Guard"]))
        r = scrape.parse_title("Mouse Highlander (Small, Mouse, Clan)")
        self.assertEqual(r["factions"], ["Hillfolk"])
        r = scrape.parse_title("Weasel Wildling (Medium, Weasel, Wildlings, Old Ones)")
        self.assertEqual((r["factions"], r["tags"]), (["Wildlings"], []))
        r = scrape.parse_title("Tortoise Sage (Medium, Tortoise, Rare beast)")
        self.assertEqual(r["tags"], ["Rare Beast"])
        r = scrape.parse_title("Black Rat Rogue (Medium, Black rat)")
        self.assertEqual(r["species"], "Black Rat")

    def test_multiple_sizes_and_ghasts(self):
        r = scrape.parse_title("Mist-ghasts 2 - Mouse, Squirrel & Bird (Small, Medium, Ghast, Undead, Mouse, Squirrel, Bird)")
        self.assertEqual((r["size"], r["species"], r["factions"]), ("Small / Medium", "Mouse / Squirrel / Bird", ["Undead"]))
        self.assertEqual(r["tags"], ["Mist Ghast"])

    def test_no_tags(self):
        r = scrape.parse_title("Burrows & Badgers Starter Band A")
        self.assertEqual((r["name"], r["size"], r["species"]), ("Burrows & Badgers Starter Band A", None, None))


class ParseListing(unittest.TestCase):
    def test_cell(self):
        html = page([item("9353816", "Town Watch-mouse (Small, Mouse)", qty=2),
                     item("9414740", "Cat Duellist (Medium, Cat)", qty=0, price="4.79", vat="0.96")], 19)
        items, last = scrape.parse_listing(html)
        self.assertEqual(last, 19)
        a, b = items
        self.assertEqual(a["id"], "9353816")
        self.assertEqual(a["price"], 3.74)
        self.assertEqual(a["status"], "Low stock")
        self.assertTrue(a["image"].endswith("_250x250.jpg"))
        self.assertNotIn("_250x250", a["image_full"])
        self.assertIn("/picture/", a["image_full"])
        self.assertEqual(b["status"], "Out of stock")
        self.assertEqual(b["price"], 5.75)


class StarterBands(unittest.TestCase):
    def test_contents(self):
        c = scrape.parse_contents(BAND_PAGE)
        self.assertEqual([x["name"] for x in c],
                         ["Otter Knight 1", "Yorkshire Terrier Knight", "Hedgehog Magic User", "Objectives", "Polyhedral dice"])
        self.assertEqual(c[0]["code"], "BB214")
        self.assertEqual(c[3]["code"], "Acc15-a")
        self.assertIsNone(c[4]["code"])

    def test_inline_contents(self):
        html = '<div class="long-description">Five minis. Contains: BB219 Fox Hunter BB220 Cat Hunter Acc14 Objectives SUPPLIED UNPAINTED.</div>'
        self.assertEqual([x["name"] for x in scrape.parse_contents(html)], ["Fox Hunter", "Cat Hunter", "Objectives"])

    def test_link(self):
        c = [{"name": "Otter Knight 1"}, {"name": "Rabbit Squire"}, {"name": "Objectives"}]
        scrape.link_contents(c, [{"id": "1", "name": "Otter Knight"}, {"id": "2", "name": "Rabbit Squire"}])
        self.assertEqual([x["model_id"] for x in c], ["1", "2", None])


class Politeness(unittest.TestCase):
    def test_robots_blocks(self):
        sess = FakeSession({1: EMPTY})
        scrape.load_robots(sess)
        with self.assertRaises(RuntimeError):
            scrape.fetch(sess, scrape.BASE + "/Listing/Category/?categoryId=1&page=1")
        scrape.fetch(sess, scrape.LISTING_URL.format(cid=4555401, page=1, slug="miniatures"))

    def test_busy_stops(self):
        class Busy(FakeSession):
            def get(self, url, timeout=None):
                if url.endswith("/robots.txt"):
                    return FakeResp("")
                r = FakeResp(""); r.status_code = 429; r.headers = {"Retry-After": "120"}; return r
        scrape.load_robots(Busy({1: EMPTY}))
        with self.assertRaises(scrape.StoreBusy):
            scrape.fetch(Busy({1: EMPTY}), scrape.BASE + "/c/1/1/x.html")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        scrape.DATA_FILE = Path(self.tmp.name) / "models.json"
        scrape.REQUEST_DELAY_S = 0

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("BB_NOW", None)

    def run_at(self, when, trigger, pages, products=None):
        os.environ["BB_NOW"] = when
        scrape.requests.Session = lambda: FakeSession(pages, products)
        sys.argv = ["scrape.py", "--trigger", trigger]
        return scrape.main()

    def state(self):
        return json.loads(scrape.DATA_FILE.read_text())

    def test_lifecycle(self):
        p1 = {1: page([item("1", "A (Small, Mouse)"), item("2", "B (Medium, Cat)", qty=0)], 2),
              2: page([item("3", "C (Large, Hare)")], 2)}
        # Wed: first ever (manual) run = baseline, nothing new
        self.assertEqual(self.run_at("2026-10-14T09:00:00+00:00", "manual", p1), 0)
        s = self.state()
        self.assertEqual(len(s["models"]), 3)
        self.assertFalse(any(m["is_new"] for m in s["models"]))

        # Manual again 30 min later -> rate limited, file unchanged
        before = scrape.DATA_FILE.read_text()
        self.run_at("2026-10-14T09:30:00+00:00", "manual", p1)
        self.assertEqual(before, scrape.DATA_FILE.read_text())

        # Friday 17:30 UTC (= 18:30 BST): scheduled poll, new model D, B restocked, C gone
        p2 = {1: page([item("1", "A (Small, Mouse)"), item("2", "B (Medium, Cat)", qty=4),
                       item("4", "D (Massive, Badger, Undead)")], 1)}
        self.run_at("2026-10-16T17:30:00+00:00", "scheduled", p2)
        s = self.state()
        by = {m["id"]: m for m in s["models"]}
        self.assertEqual(s["models"][0]["id"], "4")          # new at top
        self.assertTrue(by["4"]["is_new"])
        self.assertTrue(by["2"]["is_restocked"])
        self.assertEqual(by["3"]["status"], "No longer listed")

        # Second cron the same evening (18:30 UTC) -> skipped
        before = scrape.DATA_FILE.read_text()
        self.run_at("2026-10-16T18:30:00+00:00", "scheduled", p2)
        self.assertEqual(before, scrape.DATA_FILE.read_text())

        # Not Friday -> scheduled skipped
        self.run_at("2026-10-17T18:30:00+00:00", "scheduled", p2)
        self.assertEqual(before, scrape.DATA_FILE.read_text())

        # Tuesday manual: E appears; D still new (until next Friday)
        p3 = {1: page([item("1", "A (Small, Mouse)"), item("2", "B (Medium, Cat)", qty=4),
                       item("4", "D (Massive, Badger, Undead)"), item("5", "E (Small, Shrew)")], 1)}
        self.run_at("2026-10-20T12:00:00+00:00", "manual", p3)
        by = {m["id"]: m for m in self.state()["models"]}
        self.assertTrue(by["4"]["is_new"] and by["5"]["is_new"])

        # Next Friday poll (GMT now, 18:30 UTC = 18:30 UK): D (seen at the previous
        # Friday poll) is no longer new; E (found since then) still is
        self.run_at("2026-10-30T18:30:00+00:00", "scheduled", p3)
        by = {m["id"]: m for m in self.state()["models"]}
        self.assertFalse(by["4"]["is_new"])
        self.assertTrue(by["5"]["is_new"])
        # The Friday after: nothing new any more
        self.run_at("2026-11-06T18:45:00+00:00", "scheduled", p3)
        self.assertFalse(any(m["is_new"] for m in self.state()["models"]))
        # 17:30 UTC in GMT is 17:30 UK -> too early
        s_before = scrape.DATA_FILE.read_text()
        self.run_at("2026-11-13T17:30:00+00:00", "scheduled", p3)
        self.assertEqual(s_before, scrape.DATA_FILE.read_text())

    def test_sections_and_new_category_baseline(self):
        mini = {1: page([item("1", "A (Small, Mouse)"), item("9363670", "Burrows & Badgers Starter Band A", qty=0)], 1)}
        # State from before extras were tracked: Miniatures only
        saved, scrape.CATEGORIES = scrape.CATEGORIES, scrape.CATEGORIES[:1]
        self.run_at("2026-10-09T17:30:00+00:00", "scheduled", {4555401: mini})
        scrape.CATEGORIES = saved
        rules = {1: page([item("9253895", "Burrows & Badgers 2nd Edition Hardback Rulebook", price="25.00", vat="0.00"),
                          item("9363670", "Burrows & Badgers Starter Band A", qty=0)], 1)}
        acc = {1: page([item("9583388", "Burrows & Badgers Roster Pad")], 1)}
        self.run_at("2026-10-14T09:00:00+00:00", "manual",
                    {4555401: mini, 4555400: rules, 4562767: acc}, {"9363670": BAND_PAGE})
        by = {m["id"]: m for m in self.state()["models"]}
        self.assertEqual(by["1"]["section"], "models")
        self.assertEqual(by["9363670"]["section"], "extras")
        self.assertEqual(by["9363670"]["categories"], ["Miniatures", "Game Rules & Starter Bands"])
        self.assertEqual(len(by["9363670"]["contents"]), 5)
        self.assertEqual(by["9253895"]["section"], "extras")
        # Newly tracked categories are a baseline, not "New"
        self.assertFalse(by["9253895"]["is_new"] or by["9583388"]["is_new"])
        # ...but a genuinely new accessory later is
        acc2 = {1: page([item("9583388", "Burrows & Badgers Roster Pad"), item("9999999", "Dice Bag")], 1)}
        self.run_at("2026-10-14T11:00:00+00:00", "manual",
                    {4555401: mini, 4555400: rules, 4562767: acc2}, {"9363670": BAND_PAGE})
        by = {m["id"]: m for m in self.state()["models"]}
        self.assertTrue(by["9999999"]["is_new"])
        # Band contents come from cache on the second run (product page not re-fetched)
        self.assertEqual(len(by["9363670"]["contents"]), 5)
        self.assertEqual(by["9363670"]["contents_checked"], "2026-10-14T09:00:00Z")

    def test_abort_on_collapse(self):
        many = {1: page([item(str(i), f"M{i} (Small, Mouse)") for i in range(1, 41)], 1)}
        self.run_at("2026-10-14T09:00:00+00:00", "manual", many)
        few = {1: page([item("1", "M1 (Small, Mouse)")], 1)}
        self.assertEqual(self.run_at("2026-10-14T11:00:00+00:00", "manual", few), 1)
        self.assertEqual(len(self.state()["models"]), 40)


if __name__ == "__main__":
    unittest.main()
