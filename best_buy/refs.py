"""Reference resolvers, link builders and the choice tables for /best-buy/*.

ONE param per input: `product` accepts a SKU, a BSIN OR any bestbuy.com
product link; `category` a category id OR a category link; `store` a store
number OR a stores.bestbuy.com link; `seller` a marketplace seller id.
Resolvers raise ValueError with a user-facing message (RefField turns it
into a 400).

Identifier shapes (www.bestbuy.com, 2026-11-19):
  * SKU       the product id every call takes: digits only. Best Buy's own
              items have 7 digits (6668952), marketplace items 8 (13300403).
  * BSIN      ten letters and digits (J3K4L6QW5C): one item across its
              sellers and conditions. A product link carries it:
                /product/<slug>/<BSIN>
                /product/<slug>/<BSIN>/sku/<SKU>
                /product/<slug>/<BSIN>/sku/<SKU>/openbox?condition=excellent
                /product/<slug>/<BSIN>/sku/<SKU>/reviews
              A link without /sku/ is resolved through the site's BSIN
              lookup (the first SKU it lists, Best Buy's own when it sells
              the item). The older forms still redirect to it and are read
              here as well: /site/<slug>/<SKU>.p?skuId=<SKU>,
              /site/reviews/<slug>/<SKU>, /site/questions/<slug>/<SKU>.
  * category  lower-case letters then digits for nearly all of them:
              abcat0500000, pcmcat247400050000, cat00000 (the root); a few
              are words (sc_digital_platform). A category link ends
              /<id>.c?id=<id>.
  * store     the store number (482). A store page is
              stores.bestbuy.com/<state>/<city>/<address>-<number>.html;
              stores.bestbuy.com/<number> redirects to it.
  * seller    a marketplace seller's number (6562), the `seller.id` of a
              product sold by a third party. Best Buy itself is "bby".
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

SITE = "https://www.bestbuy.com"
HOSTS = ("bestbuy.com",)
STORES_SITE = "https://stores.bestbuy.com"
ROOT_CATEGORY = "cat00000"

SKU_RE = re.compile(r"^[0-9]{5,10}$")
BSIN_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{10}$")
CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_-]{2,39}$")
STORE_RE = re.compile(r"^[0-9]{1,6}$")
SELLER_RE = re.compile(r"^[0-9]{1,9}$")
ZIP_RE = re.compile(r"^[0-9]{5}$")

MAX_BULK_PRODUCTS = 20      # five upstream requests of four products (fetch.MAX_BATCH_PRODUCTS)

# The site's own limits, measured 2026-11-19: a listing page of 100 answers
# and 101 does not; page 100 of 18 and page 24 of 100 are the last that
# answer (2,400 results); a reviews or questions page takes 100 as well.
MAX_PAGE_SIZE = 100
MAX_LISTING_PAGE = 100
MAX_LISTING_RESULTS = 2400

_PRODUCT_HELP = ("Must be a Best Buy SKU (6668952), a BSIN (J3K4L6QW5C) or a bestbuy.com product link "
                 "(https://www.bestbuy.com/product/.../J3K4L6QW5C/sku/6668952).")
_CATEGORY_HELP = ("Must be a category id (abcat0500000, pcmcat247400050000) or a bestbuy.com category link "
                  "(https://www.bestbuy.com/site/.../abcat0500000.c?id=abcat0500000).")
_STORE_HELP = ("Must be a store number (482) or a stores.bestbuy.com link "
               "(https://stores.bestbuy.com/ny/new-york/60-w-23rd-st-482.html).")
_SELLER_HELP = "Must be a marketplace seller id: the number in a product's seller.id (6562)."

# public sort name -> the site's own token (the `sortOptions` of a result
# page, plus New-Arrivals, which the site accepts on every listing)
SORTS = {
    "best_match": "Best-Match",
    "best_selling": "Best-Selling",
    "price_low_to_high": "Price-Low-To-High",
    "price_high_to_low": "Price-High-To-Low",
    "customer_rating": "Customer-Rating",
    "best_discount": "Best-Discount",
    "new_arrivals": "New-Arrivals",
}
# public name -> ProductReviewSortValue (every other value is a 400 upstream)
REVIEW_SORTS = {
    "best_match": "BEST_MATCH",
    "newest": "NEWEST",
    "oldest": "OLDEST",
    "most_helpful": "MOST_HELPFUL",
    "highest_rated": "HIGHEST_RATED",
    "lowest_rated": "LOWEST_RATED",
}
# public name -> the value of the site's condition_facet
CONDITIONS = {"new": "New", "open_box": "Open-Box", "refurbished": "Refurbished"}
# public name -> the value of the site's customerreviews_facet
MIN_RATINGS = {"1": "1 & Up", "2": "2 & Up", "3": "3 & Up", "4": "4 & Up", "5": "5", "top_rated": "Top-Rated"}
# public name -> the value of the site's currentoffers_facet (deals/list);
# the seasonal values (Black Friday Deal, Doorbuster) come and go and are
# reachable through `filter` on the listing routes; "Package Deals" lists
# packages only, which the listings leave out
DEAL_TYPES = {"on_sale": "On Sale", "clearance": "Clearance", "member_deals": "Plus & Total Member Deals"}
# the open-box condition codes of a SKU (buyingOptions / openBoxCondition)
OPEN_BOX_CONDITIONS = {2: "excellent", 1: "good", 0: "fair"}

# the facets the named params fill; `filter` may not repeat them
FACET_BRAND = "brand_facet"
FACET_PRICE = "currentprice_facet"
FACET_RATING = "customerreviews_facet"
FACET_CONDITION = "condition_facet"
FACET_OFFERS = "currentoffers_facet"
FACET_SOLD_BY = "bbyonly_facet"
FACET_CATEGORY = "category_facet"
ON_SALE = "On Sale"
SOLD_BY_BEST_BUY = "Best Buy"
MAX_PRICE_BOUND = 999999    # the open end of a price range: the site ignores "500 to *"
NAMED_FACETS = {FACET_BRAND: "brand", FACET_PRICE: "min_price / max_price", FACET_RATING: "min_rating",
                FACET_CONDITION: "condition", FACET_SOLD_BY: "sold_by_best_buy", FACET_CATEGORY: "category"}

MAX_STORES = 25             # the store lookup answers 25 stores at most, whatever limit it is given
DEFAULT_STORE_RADIUS = 50   # miles
MAX_STORE_RADIUS = 250      # the radius the site's own store picker searches
NEAREST_STORE_RADIUS = 250
MAX_SUGGESTIONS = 20        # the suggest service: "maximum is 20"
MAX_SIMILAR = 25            # the compare block answered 17 rows when asked for 25


# ---- links ---------------------------------------------------------------------------

def absolute(link):
    """A site path or link -> an absolute bestbuy.com link; None for nothing."""
    if not isinstance(link, str) or not link.strip():
        return None
    link = link.strip()
    if link.startswith("//"):
        return "https:" + link
    if link.startswith("/"):
        return SITE + link
    return link


def store_link(store):
    """stores.bestbuy.com/<number>, which redirects to the store's own page."""
    return f"{STORES_SITE}/{store}" if store else None


def last_listing_page(limit):
    """The last page of `limit` rows the site serves: it stops at 2,400
    results and at page 100, whichever comes first."""
    return max(1, min(MAX_LISTING_PAGE, MAX_LISTING_RESULTS // max(1, limit)))


def _link_parts(value, hosts=HOSTS):
    """(path, query dict) of a link to one of `hosts`, or of a bare site
    path; None when the value is neither. A link to another host raises."""
    if value.startswith("/") and not value.startswith("//"):
        parsed = urlparse(SITE + value)
        return unquote(parsed.path), parse_qs(parsed.query)
    names = tuple(n for h in hosts for n in (h + "/", "www." + h + "/", "stores." + h + "/"))
    if not value.startswith(("http://", "https://", "//") + names):
        return None
    url = value
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in hosts):
        raise ValueError("Must be a bestbuy.com link.")
    return unquote(parsed.path or "/"), parse_qs(parsed.query)


# ---- resolvers -----------------------------------------------------------------------

_PRODUCT_PATH_RE = re.compile(r"/product/[^/]+/([A-Za-z0-9]{10})(?:/sku/([0-9]{5,10}))?(?:/|$)")
_OLD_PRODUCT_PATH_RE = re.compile(r"/([0-9]{5,10})\.p$")
_OLD_SECTION_PATH_RE = re.compile(r"^/site/(?:reviews|questions)/[^/]+/([0-9]{5,10})/?$")


def resolve_product(value):
    """SKU, BSIN or product link -> the SKU (digits) or, for a BSIN and for
    a link that names no SKU, the BSIN (api.py looks its SKU up)."""
    value = (value or "").strip()
    parts = _link_parts(value)
    if parts is not None:
        path, query = parts
        match = _PRODUCT_PATH_RE.search(path)
        if match:
            bsin, sku = match.group(1).upper(), match.group(2)
            if sku:
                return sku
            if BSIN_RE.match(bsin):
                return bsin
            raise ValueError(_PRODUCT_HELP)
        sku = (query.get("skuId") or [""])[0].strip()
        if not SKU_RE.match(sku):
            old = _OLD_PRODUCT_PATH_RE.search(path) or _OLD_SECTION_PATH_RE.match(path)
            sku = old.group(1) if old else ""
        if SKU_RE.match(sku):
            return sku
        raise ValueError(_PRODUCT_HELP)
    if SKU_RE.match(value):
        return value
    if BSIN_RE.match(value.upper()):
        return value.upper()
    raise ValueError(_PRODUCT_HELP)


def is_bsin(product):
    return bool(product) and not product.isdigit()


def _resolve_many(value, limit):
    refs = []
    for raw in str(value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        ref = resolve_product(item)
        if ref not in refs:
            refs.append(ref)
    if not refs:
        raise ValueError(_PRODUCT_HELP)
    if len(refs) > limit:
        raise ValueError(f"At most {limit} products per call.")
    return refs


def resolve_products(value):
    """'6668952, J3K4L6QW5C, <link>' -> the references in the order given,
    repeats dropped. At most MAX_BULK_PRODUCTS."""
    return _resolve_many(value, MAX_BULK_PRODUCTS)


def resolve_category(value):
    """Category id or category link -> the category id."""
    value = (value or "").strip()
    parts = _link_parts(value)
    if parts is not None:
        path, query = parts
        code = (query.get("id") or [""])[0].strip()
        if not CATEGORY_RE.match(code):
            match = re.search(r"/([a-z][a-z0-9_-]{2,39})\.c$", path)
            code = match.group(1) if match else ""
        if CATEGORY_RE.match(code) and not _PRODUCT_PATH_RE.search(path):
            return code
        raise ValueError(_CATEGORY_HELP)
    if CATEGORY_RE.match(value.lower()):
        return value.lower()
    raise ValueError(_CATEGORY_HELP)


def resolve_store(value):
    """Store number or stores.bestbuy.com link -> the store number."""
    value = (value or "").strip()
    parts = _link_parts(value)
    if parts is not None:
        path, _ = parts
        match = re.search(r"(?:-|/)([0-9]{1,6})(?:\.html)?/?$", path)
        if match:
            return str(int(match.group(1)))
        raise ValueError(_STORE_HELP)
    if STORE_RE.match(value):
        return str(int(value))
    raise ValueError(_STORE_HELP)


def resolve_seller(value):
    """Marketplace seller id -> the id as an int."""
    value = (value or "").strip()
    if SELLER_RE.match(value) and int(value) > 0:
        return int(value)
    raise ValueError(_SELLER_HELP)


def resolve_zip(value):
    """A US ZIP code: five digits (ZIP+4 is cut to its first five)."""
    value = (value or "").strip()
    match = re.match(r"^([0-9]{5})(?:-[0-9]{4})?$", value)
    if not match:
        raise ValueError("A US ZIP code has 5 digits (10001).")
    return match.group(1)
