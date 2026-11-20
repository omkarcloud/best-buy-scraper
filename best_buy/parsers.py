"""GraphQL / JSON answers of www.bestbuy.com -> clean dicts for every
/best-buy/* endpoint. Pure functions, no I/O; every one tolerates a missing
or null piece (the gateway nulls any field it could not resolve and says so
in `errors`).

Fixtures (best_buy/fixtures/, raw answers captured 2026-11-19 through
fetch.py's own calls, never edited):
  listing_search_laptop.json            a search page, 24 rows in full
  listing_search_page_2.json            its second page
  listing_search_past_end.json          a page past the site's last one (search null + NOT_FOUND)
  listing_search_no_results.json        a search that matches nothing
  listing_search_typo.json              a misspelt query the site corrected
  listing_search_redirect.json          a query the site answers with a category link only
  listing_search_filtered.json          brand x2 + price range + rating + condition, sorted by price
  listing_search_unknown_facet.json     a facet the site does not have: it is not applied
  listing_search_package_rows.json      a result with a package row (captured before fetch.py asked the
                                        site to leave packages out): the row is skipped
  listing_category.json                 a category listing, with its category record
  listing_category_within.json          the same category, searched within
  listing_category_unknown.json         an unknown category id
  listing_deals.json                    the whole catalog's "On Sale" listing
  products_two.json                     the five product answers of a Best Buy item and of a
                                        marketplace item without reviews
  products_unknown.json                 the five answers of an unknown SKU
  offers_identity*.json                 a product with the SKUs of its 14 other sellers, a
                                        marketplace item sold by one seller, an unknown SKU
  prices_open_box.json                  one SKU's price as new, excellent, good (not offered) and fair
  identity.json / identity_unknown.json the review-stats answer: name, link, rating
  bsin.json / bsin_unknown.json         the SKUs behind a BSIN
  reviews*.json                         [reviews answer, review-stats answer] of one request: a page,
                                        page 2 newest first, a star filter, a text search, past the
                                        end, a product without reviews, an unknown SKU
  questions*.json                       [questions answer, review-stats answer]: a page (AI-generated
                                        questions first), page 2, past the end, a product without
                                        questions, an unknown SKU
  similar.json / similar_unknown.json   the compare block's products
  fulfillment*.json                     a stocked item with 27 nearby stores, a marketplace item
                                        (shipping only), an unknown SKU (NOT_AVAILABLE)
  suggestions.json / suggestions_none.json
  category_*.json                       the root, a department, a leaf, an unknown id
  stores_10001.json / stores_unknown_zip.json / store_482.json / store_unknown.json
  seller_6562.json / seller_unknown.json

Units and forms: prices are US dollars as numbers; dates come as
2026-10-07T05:00Z, 2026-05-15T12:02:33.000-05:00 and 2026-11-15 and leave
as ISO text (UTC where a clock time is given); descriptions are plain text,
only the AI review summary is HTML.
"""
import re

from best_buy import refs
from scraper_utils.dates import iso_day, iso_utc
from scraper_utils.text import clean, html_to_text, multiline, text_list
from scraper_utils.values import amount, as_dict, as_list, bool_or_none, g, num, number, rating, to_int

CURRENCY = "USD"

_SORT_NAMES = {token: name for name, token in refs.SORTS.items()}
_SELLER_TYPES = {"1P": "best_buy", "3P": "marketplace"}
_OPEN_BOX_TYPES = {"excellent": 2, "good": 1, "fair": 0}
# US time-zone abbreviations the shipping cut-off is printed in -> hours from UTC
_ZONE_HOURS = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
               "AKST": -9, "AKDT": -8, "HST": -10, "UTC": 0, "GMT": 0}
_LOCAL_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))? ?([A-Z]{2,4})$")
_MEASURE_RE = re.compile(r"^\s*(-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z][A-Za-z .]*)\s*$")


# ---- small helpers -------------------------------------------------------------------

def _text(value):
    """clean(), with the site's own placeholder words for "nothing" as None."""
    value = clean(value)
    return None if value is None or value.lower() in ("none", "n/a", "na", "null") else value


def _money(value):
    value = amount(value)
    return value if value is not None and value >= 0 else None


def _link(value):
    return refs.absolute(value)


def _utc(value):
    """A timestamp -> ISO UTC text; a bare date stays a date."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return iso_utc(value) or iso_day(value)


def local_time_utc(value):
    """'2026-11-19T16:00 CST' (the shipping cut-off) -> '2026-11-19T22:00:00Z';
    None for anything else or an abbreviation not in the table."""
    match = _LOCAL_TIME_RE.match(value.strip()) if isinstance(value, str) else None
    if not match or match.group(7) not in _ZONE_HOURS:
        return None
    return iso_utc("%s-%s-%sT%s:%s:%s%+03d:00" % (*match.groups()[:5], match.group(6) or "00",
                                                  _ZONE_HOURS[match.group(7)]))


def measurement(value):
    """'0.75 inches' -> {"value": 0.75, "unit": "inches"}; None when the
    text is not a number with a unit."""
    match = _MEASURE_RE.match(value) if isinstance(value, str) else None
    if not match:
        return None
    return {"value": number(match.group(1)), "unit": match.group(2).strip().lower()}


def _duration_seconds(value):
    """'1:51' / '111' -> 111."""
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    if not all(p.isdigit() for p in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def open_box_name(code):
    """The open-box condition code of a SKU (2 / 1 / 0, as int or text) ->
    'excellent' / 'good' / 'fair'; None for a new item."""
    code = to_int(code)
    return refs.OPEN_BOX_CONDITIONS.get(code) if code is not None else None


def _condition(product):
    """'new' / 'refurbished' / 'pre-owned' ... as the site names it, lower
    case; an open-box copy is 'open_box'."""
    if open_box_name(product.get("openBoxCondition")):
        return "open_box"
    value = clean(g(product, "condition", "type"))
    return value.lower().replace("-", "_").replace(" ", "_") if value else None


def _seller(seller):
    """{"type": "best_buy" | "marketplace", "id", "name", "rating",
    "reviews_count"}; the id is None for Best Buy itself."""
    seller = as_dict(seller)
    if not seller:
        return None
    market = as_dict(seller.get("marketplaceSeller"))
    raw_id = seller.get("id")
    kind = _SELLER_TYPES.get(seller.get("classification"))
    if kind is None:
        kind = "best_buy" if raw_id == "bby" else ("marketplace" if raw_id or market else None)
    stats = as_dict(g(market, "sellerReviewStats", "stats"))
    return {
        "type": kind,
        "id": None if kind == "best_buy" else to_int(market.get("id") or raw_id),
        "name": "Best Buy" if kind == "best_buy" else clean(market.get("name")),
        "rating": rating(stats.get("averageRating")),
        "reviews_count": to_int(stats.get("totalReviewCount")),
    }


def _badges(product):
    """The product's badges, both kinds the site prints: the campaign badge
    (badges) and the card labels (badgesV2), repeats dropped by name."""
    out, seen = [], set()
    for badge in as_list(product.get("badgesV2")):
        name = clean(as_dict(badge).get("label"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "type": clean(badge.get("type")), "description": clean(badge.get("description")),
                    "link": _link(badge.get("experienceUrl")), "category_id": clean(badge.get("categoryId")),
                    "category_name": clean(badge.get("categoryName")), "starts_at": None, "ends_at": None})
    for badge in as_list(product.get("badges")):
        name = clean(as_dict(badge).get("displayName"))
        if not name:
            continue
        dates = {"starts_at": _utc(badge.get("startDate")), "ends_at": _utc(badge.get("endDate"))}
        match = next((b for b in out if b["name"].lower() == name.lower()), None)
        if match:
            match.update(dates)
            match["link"] = match["link"] or _link(badge.get("detailsHref"))
            continue
        out.append({"name": name, "type": None, "description": None, "link": _link(badge.get("detailsHref")),
                    "category_id": None, "category_name": None, **dates})
    return out


def _buying_options(options):
    """[{condition: 'new' | 'open_box_excellent' ..., link}] from the
    product's buyingOptions (type New / Excellent / Good / Fair)."""
    out = []
    for option in as_list(options):
        kind = clean(as_dict(option).get("type"))
        if not kind:
            continue
        low = kind.lower()
        out.append({"condition": f"open_box_{low}" if low in _OPEN_BOX_TYPES else low.replace(" ", "_").replace("-", "_"),
                    "link": _link(option.get("pdpUrl"))})
    return out


def open_box_codes(options):
    """The open-box condition codes a product's buyingOptions offer, best first."""
    kinds = [str(as_dict(o).get("type") or "").lower() for o in as_list(options)]
    return [code for name, code in _OPEN_BOX_TYPES.items() if name in kinds]


def parse_price(price):
    """The site's price object -> {current, regular, savings, ...}. All
    amounts are US dollars."""
    price = as_dict(price)
    if not price:
        return None
    current = _money(price.get("displayableCustomerPrice"))
    if current is None:
        current = _money(price.get("customerPrice"))
    regular = _money(price.get("displayableRegularPrice"))
    finance = as_dict(price.get("financeOption"))
    return {
        "current": current,
        "regular": regular if regular is not None else current,
        "currency": CURRENCY if current is not None else None,
        "savings": _money(price.get("totalSavings")),
        "savings_percent": number(price.get("totalSavingsPercent")),
        "open_box_savings": _money(price.get("openBoxSavings")),
        "member_savings": _money(price.get("totalPaidMemberSavings")),
        "is_on_sale": bool(current is not None and regular is not None and current < regular),
        "deal_label": clean(price.get("puckDisplayMessage")),
        "deal_link": _link(price.get("puckMessageURL")),
        "deal_ends_at": _utc(price.get("dealExpirationTimeStamp")),
        "restricted_price_message": clean(price.get("restrictedPriceDisplayMessage")),
        "is_eco_rebate_eligible": bool_or_none(price.get("isEcoRebateEligible")),
        "financing": {
            "months": to_int(finance.get("financeTerm")),
            "monthly_payment": _money(finance.get("monthlyPayment")),
            "plan_type": clean(finance.get("planType")),
            "rate": number(finance.get("rate")),
            "total_cost": _money(finance.get("totalCost")),
        } if finance else None,
        "installments": [{
            "vendor": clean(option.get("vendor")),
            "type": clean(option.get("type")),
            "count": to_int(option.get("numberOfInstallments")),
            "amount": _money(option.get("installmentAmount")),
            "service_fee": _money(option.get("installmentServiceFee")),
        } for option in as_list(price.get("paymentOptions")) if isinstance(option, dict)],
        "gift_skus": [str(gift["skuId"]) for gift in as_list(price.get("giftSkus"))
                      if isinstance(gift, dict) and gift.get("skuId")],
    }


def _review_summaries(info):
    """The reviews other sites syndicate to the product (a brand's own site)."""
    return [{
        "source": clean(row.get("clientDisplayName")),
        "rating": rating(row.get("overallRating")),
        "reviews_count": to_int(row.get("totalReviewCount")),
        "link": _link(row.get("displayLink")),
    } for row in as_list(as_dict(info).get("syndicatedReviewSummary")) if isinstance(row, dict)]


def _category_path(lineage, leaf=None):
    """primaryLineage (nearest parent first) -> [{id, name, link}] from
    the root down, the category itself last when `leaf` is given."""
    path = [{"id": clean(row.get("id")), "name": clean(row.get("name")), "link": _link(row.get("seoUrl"))}
            for row in reversed(as_list(lineage)) if isinstance(row, dict) and row.get("id")]
    if leaf and leaf.get("id"):
        path.append(leaf)
    return path


def _variations(display):
    """The other versions of the item a listing row offers (its colours,
    sizes): [{bsin, sku, options: [{name, value, image}]}]."""
    display = as_dict(display)
    names = {t.get("rawName"): clean(t.get("displayName")) for t in as_list(display.get("variationTypes"))
             if isinstance(t, dict)}
    out = []
    for row in as_list(display.get("productBsinVariations")):
        if not isinstance(row, dict) or not row.get("bsin"):
            continue
        out.append({
            "bsin": clean(row.get("bsin")),
            "sku": clean(g(row, "bsinProduct", "featuredSKU", "skuId")),
            "options": [{"name": names.get(v.get("rawName")) or clean(str(v.get("rawName") or "").rpartition(":")[2]),
                         "value": clean(v.get("value")), "image": _link(g(v, "image", "piscesHref"))}
                        for v in as_list(row.get("variations")) if isinstance(v, dict) and v.get("value")],
        })
    return out


def _models_3d(models):
    """The product's 3D model files (the site's "view in your room")."""
    return [{"link": _link(m.get("uri")), "format": clean(m.get("format")), "size": clean(m.get("type")),
             "is_animated": bool_or_none(m.get("animated"))}
            for m in as_list(models) if isinstance(m, dict) and m.get("uri")]


# ---- listing rows --------------------------------------------------------------------

def parse_listing_product(product):
    """One product of a search / category row, as the result page asks
    for it."""
    product = as_dict(product)
    info = as_dict(product.get("reviewInfo"))
    group = as_dict(product.get("bsinProduct"))
    sku = clean(product.get("skuId"))
    return {
        "sku": sku,
        "bsin": clean(product.get("bsin")),
        "name": clean(g(product, "name", "short")),
        "title": clean(g(product, "name", "title")),
        "brand": clean(product.get("brand")),
        "model_number": clean(g(product, "manufacturer", "modelNumber")),
        "link": _link(g(product, "url", "skuSpecificUrl") or g(product, "url", "pdp")),
        "condition": _condition(product),
        "open_box_condition": open_box_name(product.get("openBoxCondition")),
        "product_types": text_list(as_list(product.get("whatItIs"))),
        "image": _link(g(product, "primaryImage", "piscesHref") or g(product, "primaryImage", "href")),
        "price": parse_price(product.get("price")),
        "rating": rating(info.get("averageRating")),
        "reviews_count": to_int(info.get("reviewCount")),
        "syndicated_reviews": _review_summaries(info),
        "seller": _seller(product.get("seller")),
        "buying_options": _buying_options(product.get("buyingOptions")),
        "other_skus": [{
            "sku": clean(row.get("skuId")),
            "condition": _condition(row),
            "seller_type": _SELLER_TYPES.get(g(row, "seller", "classification")),
        } for row in as_list(group.get("products")) if isinstance(row, dict) and row.get("skuId") and row.get("skuId") != sku],
        "variations": _variations(product.get("productVariationListDisplay")),
        "badges": _badges(product),
        "offers_count": len([o for o in as_list(g(product, "offers", "offers")) if isinstance(o, dict)]),
        "models_3d": _models_3d(product.get("arModels")),
        "status": clean(product.get("dotComDisplayStatus")),
        "release_date": iso_day(product.get("dotComStreetDate")) or clean(product.get("releaseDateDisplayValue")),
        "esrb_rating": clean(g(product, "esrbRating", "code")),
        "is_new": bool_or_none(product.get("isNew")),
        "is_recalled": bool_or_none(product.get("productRecalled")),
        "has_parental_advisory": bool_or_none(product.get("parentalAdvisory")),
    }


def parse_facets(facets):
    """[{facet, name, values: [{value, count, category_id, children}]}]:
    `facet` and a value's `value` are what the `filter` param takes."""
    out = []
    for facet in as_list(facets):
        if not isinstance(facet, dict) or not facet.get("facetField"):
            continue
        values = []
        for row in as_list(facet.get("values")):
            if not isinstance(row, dict) or _text(row.get("value")) is None:
                continue        # the site's "None" bucket (products without the attribute) is not a value
            values.append({
                "value": str(row["value"]),
                "count": to_int(row.get("count")),
                "category_id": clean(row.get("categoryPath")),
                "children": [{"value": str(child["value"]), "category_id": clean(child.get("categoryPath"))}
                             for child in as_list(row.get("nestedValues"))
                             if isinstance(child, dict) and child.get("value")],
            })
        out.append({"facet": facet["facetField"], "name": clean(facet.get("displayName")),
                    "parent_facet": clean(facet.get("parentFacetField")), "values": values})
    return out


def applied_facets(search):
    """{facet: [values]} the site says it applied (requestInfo.appliedFacets)."""
    out = {}
    for row in as_list(g(search, "requestInfo", "appliedFacets")):
        if isinstance(row, dict) and row.get("id"):
            out.setdefault(row["id"], []).extend(str(v) for v in as_list(row.get("values")))
    return out


def reachable_pages(total, limit):
    """How many pages of `limit` the site serves for `total` results (it
    stops at refs.last_listing_page)."""
    if not total or not limit:
        return 0
    return min(-(-total // limit), refs.last_listing_page(limit))


def parse_listing(answer, page, limit):
    """One result page -> the rows, the facets and everything the page
    says about the request. A page past the end (search null) is an empty
    page; `redirect_link` is set when the site answers the query with a
    link instead of results."""
    search = as_dict(g(answer, "data", "search"))
    info = as_dict(search.get("requestInfo"))
    # a row without a product is a package (several SKUs sold together):
    # fetch.py asks the site to leave those out, and one that comes anyway is skipped
    products = [parse_listing_product(doc["product"]) for doc in as_list(search.get("documents"))
                if isinstance(doc, dict) and isinstance(doc.get("product"), dict) and doc["product"].get("skuId")]
    total = to_int(search.get("numFound"))
    redirect = next((r for r in as_list(search.get("redirect")) if isinstance(r, str) and r.strip()), None)
    auto = [row for row in as_list(info.get("appliedFacets"))
            if isinstance(row, dict) and str(row.get("autoFacet")).lower() == "true"]
    category = as_dict(info.get("categoryFacet"))
    sort_token = g(info, "sort", "value")
    return {
        "corrected_query": clean(g(search, "suggestQueryInfo", "correctedQuery")),
        "redirect_link": _link(redirect),
        "sort": _SORT_NAMES.get(sort_token, clean(sort_token)),
        "detected_category": {"id": clean(category.get("categoryPath")), "name": clean(category.get("categoryName"))}
        if auto and category.get("categoryPath") else None,
        "applied_filters": [{"facet": row["id"], "values": [str(v) for v in as_list(row.get("values"))],
                             "is_automatic": str(row.get("autoFacet")).lower() == "true"}
                            for row in as_list(info.get("appliedFacets")) if isinstance(row, dict) and row.get("id")],
        "products": products,
        "facets": parse_facets(search.get("facets")),
        "sort_options": [_SORT_NAMES.get(o.get("value"), clean(o.get("value")))
                         for o in as_list(search.get("sortOptions")) if isinstance(o, dict) and o.get("value")],
        "quick_filters": [{"facet": clean(q.get("facetName")), "value": clean(q.get("facetValue"))}
                          for q in as_list(search.get("quickFilters")) if isinstance(q, dict) and q.get("facetName")],
        "related_searches": text_list([g(t, "searchTerm") for t in as_list(search.get("relatedSearchTerms"))]),
        "pagination": {"page": page, "items_per_page": limit,
                       "total_pages": reachable_pages(total, limit) if total is not None else 0,
                       "total_count": total or 0},
    }


def parse_listing_category(answer):
    """The category record a category listing carries: id, name, link, the
    path from the root and the direct subcategories."""
    category = as_dict(g(answer, "data", "productCategory"))
    if not category.get("id"):
        return None
    leaf = {"id": clean(category.get("id")), "name": clean(category.get("name")), "link": _link(category.get("seoUrl"))}
    return {
        **leaf,
        "path": _category_path(g(category, "broaderTerms", "primaryLineage")),
        "subcategories": _subcategories(category.get("narrowerTerms")),
    }


def _subcategories(rows):
    out = []
    for row in as_list(rows):
        if not isinstance(row, dict) or not row.get("id") or not row.get("name") or row.get("name") == row.get("id"):
            continue        # a child named by its own id (homepage-pushdown, leaderboardfooter) is an ad slot
        out.append({"id": clean(row.get("id")), "name": clean(row.get("name")), "link": _link(row.get("seoUrl")),
                    "started_at": _utc(row.get("startDate")), "ends_at": _utc(row.get("endDate"))})
    return out


# ---- products/details ----------------------------------------------------------------

def _specifications(groups):
    out = []
    for group in as_list(groups):
        if not isinstance(group, dict):
            continue
        items = [{"name": clean(spec.get("displayName")), "value": _text(spec.get("value")),
                  "definition": clean(spec.get("definition"))}
                 for spec in as_list(group.get("specifications")) if isinstance(spec, dict) and spec.get("displayName")]
        if items:
            out.append({"group": clean(group.get("name")), "items": items})
    return out


def _images(product):
    """One row per picture: the full-size file with its view; the prescaled
    500 px copies the site lists beside each are left out."""
    out, seen = [], set()
    for image in as_list(product.get("images")):
        link = _link(as_dict(image).get("piscesHref"))
        if not link or "/prescaled/" in link or link in seen:
            continue
        seen.add(link)
        out.append({"link": link, "view": clean(image.get("rel")), "alt_text": multiline(image.get("altText"))})
    return out


def _videos(videos):
    out = []
    for video in as_list(g(videos, "results")):
        if not isinstance(video, dict) or not video.get("videoDirectLink"):
            continue
        thumbs = sorted((t for t in as_list(video.get("thumbnails")) if isinstance(t, dict) and t.get("href")),
                        key=lambda t: to_int(t.get("width")) or 0, reverse=True)
        out.append({
            "id": clean(video.get("id")),
            "title": clean(video.get("title")),
            "description": clean(video.get("videoDescription")),
            "link": _link(video.get("videoDirectLink")),
            "duration_seconds": _duration_seconds(video.get("durationInSeconds")) or _duration_seconds(video.get("duration")),
            "thumbnail": _link(thumbs[0]["href"]) if thumbs else None,
            "kind": clean(video.get("creativeElements")),
            "published_at": _utc(video.get("onlineDate")),
            "captions": [{"language": clean(c.get("lang")), "link": _link(c.get("href"))}
                         for c in as_list(video.get("captions")) if isinstance(c, dict) and c.get("href")],
            "tags": [tag for tag in text_list(as_list(video.get("tags"))) if not tag.isdigit()],
        })
    return out


def _highlights(highlights):
    out = []
    for entry in as_list(as_dict(highlights).get("entries")):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        out.append({
            "name": clean(entry.get("name")),
            "value": clean(entry.get("value")),
            "level": clean(entry.get("classification")),
            "description": multiline(entry.get("description")),
            "levels": [{"name": clean(level.get("name")), "description": multiline(level.get("description")),
                        "bullets": text_list(as_list(level.get("bullets"))), "icon": _link(level.get("icon"))}
                       for level in as_list(entry.get("classifications")) if isinstance(level, dict)],
        })
    return out


def _attributes(rows):
    """operationalAttributes -> [{name, values}], the vendor's embed links
    (DemoURL_*) left out."""
    out = []
    for row in as_list(rows):
        name = clean(as_dict(row).get("displayName"))
        values = text_list(as_list(as_dict(row).get("values")))
        if not name or not values or name.lower().startswith("demourl"):
            continue
        out.append({"name": name, "values": values})
    return out


def product_missing(parts):
    """True when the five answers say the SKU does not exist."""
    main = as_dict(parts.get("PRODUCT"))
    if g(main, "data", "productBySkuId") is not None:
        return False
    return any(path[:2] == ["productBySkuId", "skuId"] for path in
               [list(e.get("path") or []) for e in as_list(main.get("errors"))
                if g(e, "extensions", "code") == "NOT_FOUND"])


def parse_product(parts):
    """The product page's five answers ({part: {"data", "errors"}}) -> one
    record. A part that failed leaves its fields null; None when the main
    record itself is absent."""
    main = as_dict(g(parts, "PRODUCT", "data"))
    product = as_dict(main.get("productBySkuId"))
    if not product.get("skuId"):
        return None
    features = as_dict(g(parts, "PRODUCT_FEATURES", "data", "productBySkuId"))
    priced = as_dict(g(parts, "PRODUCT_PRICE", "data", "productBySkuId"))
    stats = as_dict(g(parts, "PRODUCT_REVIEW_STATS", "data", "productBySkuId"))
    sold = as_dict(g(parts, "PRODUCT_SELLER", "data", "productBySkuId"))
    info = as_dict(product.get("reviewInfo"))
    shown = as_dict(g(stats, "reviewInfo", "displayableStats"))
    summaries = as_dict(info.get("aiSummaries"))
    categories = [row for row in as_list(g(product, "hierarchy", "bbypres")) if isinstance(row, dict)]
    primary = next((row for row in categories if row.get("primary")), categories[0] if categories else {})
    detail = as_dict(primary.get("categoryDetail"))
    leaf = {"id": clean(primary.get("id")), "name": clean(detail.get("name")), "link": _link(detail.get("seoUrl"))}
    dimension = as_dict(product.get("dimension"))
    esrb = as_dict(product.get("esrbRating"))
    return {
        "sku": clean(product.get("skuId")),
        "bsin": clean(product.get("bsin")),
        "name": clean(g(product, "name", "short")),
        "brand": clean(product.get("brand")),
        "model_number": clean(g(product, "manufacturer", "modelNumber")),
        "upc": clean(product.get("upc")),
        "link": _link(g(stats, "url", "skuSpecificUrl") or g(product, "url", "pdp")),
        "condition": _condition(product),
        "product_types": text_list(as_list(product.get("whatItIs"))),
        "product_class": clean(g(product, "typeInfo", "definition")),
        "status": clean(product.get("dotComDisplayStatus")),
        "description": multiline(g(product, "description", "long") or g(features, "description", "long")),
        "features": [{"title": clean(f.get("title")), "description": multiline(f.get("description"))}
                     for f in as_list(features.get("features")) if isinstance(f, dict)],
        "highlights": _highlights(product.get("highlights")),
        "included_items": text_list(as_list(features.get("includedItems"))),
        "specifications": _specifications(product.get("specificationGroups")),
        "dimensions": {key: measurement(dimension.get(key)) for key in ("height", "width", "depth", "weight")}
        if dimension else None,
        "category": leaf if leaf["id"] else None,
        "category_path": _category_path(g(detail, "broaderTerms", "primaryLineage"), leaf) if leaf["id"] else [],
        "image": _link(g(product, "primaryImage", "piscesHref")),
        "images": _images(product),
        "videos": _videos(main.get("videos")),
        "price": parse_price(priced.get("price")),
        "buying_options": _buying_options(priced.get("buyingOptions")),
        "rating": rating(shown.get("rating")) or rating(info.get("averageRating")),
        "reviews_count": to_int(shown.get("reviewCount")) if shown.get("reviewCount") is not None
        else to_int(info.get("reviewCount")),
        "review_summary": multiline(info.get("reviewSummary")),
        "customers_love": html_to_text(summaries.get("whatCustomersLoveProse")),
        "customers_love_points": [line.lstrip("- ").strip() for line in
                                  (html_to_text(summaries.get("whatCustomersLoveBullets")) or "").splitlines() if line.strip("- ")],
        "seller": _seller(sold.get("seller")),
        "badges": _badges(product),
        "attributes": _attributes(features.get("operationalAttributes") or product.get("operationalAttributes")),
        "logos": [{"name": clean(logo.get("name")), "image": _link(logo.get("piscesHref"))}
                  for logo in as_list(features.get("supportedLogos")) if isinstance(logo, dict) and logo.get("name")],
        "manuals": [{"type": clean(doc.get("documentType")), "link": _link(doc.get("assetUrl")),
                     "languages": text_list(as_list(doc.get("language")))}
                    for doc in as_list(features.get("productManuals")) if isinstance(doc, dict) and doc.get("assetUrl")],
        "documents": [{"name": clean(doc.get("displayName")), "values": text_list(as_list(doc.get("values")))}
                      for doc in as_list(features.get("documents")) if isinstance(doc, dict) and doc.get("displayName")],
        "disclaimers": text_list([multiline(d) for d in as_list(features.get("disclaimers"))]),
        "proposition_65_warning": _text(features.get("proposition65WarningTypeDescription")),
        "restrictions": text_list([g(r, "restrictionCategory") for r in as_list(product.get("restrictions"))]),
        "esrb": {"rating": clean(esrb.get("code")), "descriptors": text_list(as_list(esrb.get("descriptors"))),
                 "interactive_elements": text_list(as_list(esrb.get("interactiveElements")))} if esrb else None,
        "release_date": iso_day(product.get("dotComStreetDate")) or clean(product.get("releaseDateDisplayValue")),
        "eco_rebates": text_list([g(p, "amountLabel") for p in
                                  as_list(g(features, "ecoRebates", "productRebateDetails", "rebatePrograms"))]),
        "in_store_service_type": clean(product.get("inStoreServiceType")),
        "open_box_check_level": to_int(product.get("functionalCheckLevel")),
        "models_3d": _models_3d(product.get("arModels")),
    }


# ---- products/offers -----------------------------------------------------------------

def parse_offer_identity(answer):
    """The offers query's product: who it is and the SKUs that sell the
    same item (its BSIN's products)."""
    product = as_dict(g(answer, "data", "productBySkuId"))
    if not product.get("skuId"):
        return None
    sku = clean(product.get("skuId"))
    group = as_dict(product.get("bsinProduct"))
    return {
        "sku": sku,
        "bsin": clean(product.get("bsin")),
        "name": clean(g(product, "name", "short")),
        "link": _link(g(product, "url", "skuSpecificUrl") or g(product, "url", "pdp")),
        "condition": _condition(product),
        "seller": _seller(product.get("seller")),
        "open_box_codes": open_box_codes(product.get("buyingOptions")),
        "other_skus": [{
            "sku": clean(row.get("skuId")),
            "condition": _condition(row),
            "seller": _seller(row.get("seller")),
            "link": _link(g(row, "url", "skuSpecificUrl") or g(row, "url", "pdp")),
        } for row in as_list(group.get("products")) if isinstance(row, dict) and row.get("skuId") and row.get("skuId") != sku],
    }


def parse_offer(answer, sku, seller, condition, link=None):
    """One price answer -> an offer row; None when the site has no price
    for it (a condition that is not on sale answers nulls)."""
    product = as_dict(g(answer, "data", "productBySkuId"))
    price = parse_price(product.get("price"))
    if not price or price["current"] is None:
        return None
    code = to_int(g(product, "price", "openBoxCondition"))
    grade = open_box_name(code)
    if grade:
        condition = f"open_box_{grade}"
        option = next((o for o in _buying_options(product.get("buyingOptions")) if o["condition"] == condition), None)
        link = (option or {}).get("link") or link
    return {
        "sku": sku,
        "condition": condition,
        "seller": seller,
        "price": price["current"],
        "regular_price": price["regular"],
        "currency": price["currency"],
        "savings": price["savings"],
        "savings_percent": price["savings_percent"],
        "deal_label": price["deal_label"],
        "link": link,
    }


# ---- products/availability -----------------------------------------------------------

def _pickup_row(row):
    row = as_dict(row)
    return {
        "is_available": bool_or_none(row.get("pickupEligible")),
        "is_in_stock": bool_or_none(row.get("instoreInventoryAvailable")),
        "ready_from": iso_day(row.get("minDate")),
        "ready_by": iso_day(row.get("maxDate")),
        "ready_in_hours": to_int(row.get("minPickupInHours")),
        "is_in_store_only": bool_or_none(row.get("inStoreOnly")),
        "is_backordered": bool_or_none(row.get("backordered")),
        "is_preorder": bool_or_none(row.get("preorderable")),
    }


def _pickup_store(store):
    store = as_dict(store)
    if not store.get("storeId"):
        return None
    return {"id": clean(store.get("storeId")), "name": clean(store.get("name")), "city": clean(store.get("city")),
            "zip": clean(store.get("zip")), "link": refs.store_link(clean(store.get("storeId")))}


def parse_availability(answer, sku, zip_code):
    """The fulfillment answer of one SKU -> button state, shipping,
    delivery, pickup at the store and at the stores around it. The site
    prints 9999 for every stock quantity, so none is given."""
    options = as_dict(g(answer, "data", "fulfillmentOptions"))
    button = next((b for b in as_list(options.get("buttonStates")) if isinstance(b, dict)), {})
    shipping = as_dict(g(options, "shippingDetails", 0, "shippingAvailability", 0))
    services = {to_int(s.get("customerLosGroupId")): s for s in as_list(shipping.get("levelOfServices"))
                if isinstance(s, dict)}
    default_group = to_int(shipping.get("defaultCustomerLosGroupId"))
    delivery = as_dict(g(options, "deliveryDetails", 0, "deliveryAvailability", 0))
    pickup = as_dict(g(options, "ispuDetails", 0))
    state = clean(button.get("buttonState"))
    nearby = []
    for row in as_list(pickup.get("nearbyLocations")):
        store = _pickup_store(as_dict(row).get("store"))
        if store:
            nearby.append({**store, "distance_miles": num(row.get("distance")),
                           **_pickup_row(g(row, "availability", 0))})
    return {
        "sku": sku,
        "zip": zip_code,
        "status": state.lower() if state else None,
        "status_text": clean(button.get("displayText")),
        "is_purchasable": state in ("ADD_TO_CART", "PRE_ORDER") if state else None,
        "condition": (clean(button.get("condition")) or "").lower() or None,
        "shipping": {
            "is_available": bool_or_none(shipping.get("shippingEligible")),
            "is_backordered": bool_or_none(shipping.get("backordered")),
            "is_preorder": bool_or_none(shipping.get("preorderable")),
            "is_shipped_by_vendor": bool_or_none(shipping.get("fulfillByVendor")),
            "is_digital": bool(shipping.get("downloadEligible") or shipping.get("emailEligible")) if shipping else None,
            "options": [{
                "name": clean(group.get("name")),
                "code": clean(as_dict(services.get(to_int(group.get("customerLosGroupId")))).get("code")),
                "price": _money(group.get("price")),
                "currency": CURRENCY,
                "arrives_from": iso_day(group.get("minLineItemMaxDate")),
                "arrives_by": iso_day(group.get("maxLineItemMaxDate")),
                "order_by": local_time_utc(as_dict(services.get(to_int(group.get("customerLosGroupId")))).get("cutOffTime")),
                "is_default": to_int(group.get("customerLosGroupId")) == default_group,
            } for group in as_list(shipping.get("customerLOSGroup")) if isinstance(group, dict)],
        } if shipping else None,
        "delivery": {
            "is_available": bool_or_none(delivery.get("deliveryEligible")),
            "is_installation_available": bool_or_none(delivery.get("installationEligible")),
            "is_backordered": bool_or_none(delivery.get("backordered")),
        } if delivery else None,
        "pickup": {"store": _pickup_store(pickup.get("store")), **_pickup_row(g(pickup, "ispuAvailability", 0))}
        if pickup.get("store") else None,
        "nearby_stores_checked": to_int(pickup.get("numberOfStoresChecked")),
        "nearby_distance_checked_miles": num(pickup.get("maximumDistanceChecked")),
        "nearby_stores": nearby,
    }


# ---- products/reviews ----------------------------------------------------------------

def parse_review(review):
    review = as_dict(review)
    source = clean(g(review, "syndicationSource", "displayName"))
    return {
        "id": clean(review.get("id")),
        "rating": to_int(review.get("rating")),
        "title": clean(review.get("title")),
        "text": multiline(review.get("text")),
        "author": clean(review.get("userNickname")),
        "submitted_at": _utc(review.get("submissionTime")),
        "days_owned": to_int(review.get("daysOfOwnership")),
        "is_recommended": bool_or_none(review.get("isRecommended")),
        "is_verified_purchaser": bool_or_none(review.get("isVerifiedPurchaser")),
        "is_incentivized": bool(review.get("isIncentivized") or any(
            (clean(as_dict(b).get("name")) or "").lower() == "incentivized" for b in as_list(review.get("badges")))),
        "helpful_count": to_int(review.get("positiveFeedbackCount")),
        "unhelpful_count": to_int(review.get("negativeFeedbackCount")),
        "comments_count": to_int(review.get("commentCount")) or 0,
        "images": [_link(i.get("piscesUrl")) for i in as_list(review.get("images")) if isinstance(i, dict) and i.get("piscesUrl")],
        "badges": text_list([as_dict(b).get("name") for b in as_list(review.get("badges"))]),
        "source": source or "Best Buy",
        "is_syndicated": bool(source),
        "reviewed_sku": clean(g(review, "reviewedProduct", "skuId")),
        "responses": [{
            "author": clean(r.get("name")),
            "department": clean(r.get("department")),
            "text": multiline(r.get("response")),
            "type": clean(r.get("responseType")),
            "source": clean(r.get("responseSource")),
            "responded_at": _utc(r.get("responseTime")),
        } for r in as_list(review.get("clientResponses")) if isinstance(r, dict)],
        "review_id_at_source": clean(review.get("bazaarvoiceId")),
    }


def parse_reviews(answer):
    """One reviews page -> {"total", "reviews", "photos"}; None when the
    answer carries no product."""
    product = g(answer, "data", "productBySkuId")
    if not isinstance(product, dict):
        return None
    block = as_dict(product.get("reviews"))
    return {
        "total": to_int(block.get("totalResults")) or 0,
        "reviews": [parse_review(r) for r in as_list(block.get("results")) if isinstance(r, dict)],
        "photos": [{"review_id": clean(i.get("reviewId")), "link": _link(i.get("url") or i.get("fallbackUrl")),
                    "thumbnail": _link(i.get("thumbnailUrl"))}
                   for i in as_list(g(product, "reviewImages", "images")) if isinstance(i, dict) and (i.get("url") or i.get("fallbackUrl"))],
    }


def parse_identity(answer):
    """The review-stats answer -> who the product is: sku, name, link,
    rating and review count. None when the SKU does not exist."""
    product = g(answer, "data", "productBySkuId")
    if not isinstance(product, dict) or not product.get("skuId"):
        return None
    shown = as_dict(g(product, "reviewInfo", "displayableStats"))
    return {
        "sku": clean(product.get("skuId")),
        "name": clean(g(product, "name", "short")),
        "link": _link(g(product, "url", "skuSpecificUrl") or g(product, "url", "relativePdp")),
        "rating": rating(shown.get("rating")),
        "reviews_count": to_int(shown.get("reviewCount")),
    }


# ---- products/questions --------------------------------------------------------------

def _answer(row):
    row = as_dict(row)
    return {
        "id": clean(row.get("id")),
        "text": multiline(row.get("text")),
        "author": clean(row.get("userNickname")),
        "answered_at": _utc(row.get("submissionTime")),
        "helpful_count": to_int(row.get("positiveFeedbackCount")),
        "unhelpful_count": to_int(row.get("negativeFeedbackCount")),
        "badges": text_list([as_dict(b).get("name") for b in as_list(row.get("badges"))]),
        "images": [_link(i.get("piscesUrl")) for i in as_list(row.get("images")) if isinstance(i, dict) and i.get("piscesUrl")],
    }


def parse_questions(answer):
    """One questions page -> {"counts", "summary", "link", "questions"};
    None when the answer carries no product."""
    product = g(answer, "data", "productBySkuId")
    if not isinstance(product, dict):
        return None
    info = as_dict(product.get("questionInfo"))
    questions = []
    for row in as_list(g(product, "questions", "results")):
        if not isinstance(row, dict):
            continue
        questions.append({
            "id": clean(row.get("id")),
            "question": clean(row.get("title")),
            "details": multiline(row.get("text")),
            "author": clean(row.get("userNickname")),
            "asked_at": _utc(row.get("submissionTime")),
            "is_ai_generated": bool_or_none(row.get("isAiGenerated")),
            "answers_count": to_int(row.get("answerCount")) or 0,
            "helpful_count": to_int(row.get("positiveFeedbackCount")),
            "unhelpful_count": to_int(row.get("negativeFeedbackCount")),
            "images": [_link(i.get("piscesUrl")) for i in as_list(row.get("images")) if isinstance(i, dict) and i.get("piscesUrl")],
            "answers": [_answer(a) for a in as_list(row.get("answers")) if isinstance(a, dict)],
        })
    return {
        "is_enabled": bool_or_none(info.get("isQnaEnabled")),
        "counts": {
            "questions": to_int(info.get("totalQuestionCount")) or 0,
            "answered_questions": to_int(info.get("answeredQuestionCount")) or 0,
            "answers": to_int(info.get("totalAnswerCount")) or 0,
            "ai_questions": to_int(info.get("aiQuestionCount")) or 0,
            "ai_answers": to_int(info.get("aiAnswerCount")) or 0,
        },
        "summary": multiline(info.get("aiSummary")),
        "link": _link(info.get("qnaStandalonePage")),
        "questions": questions,
    }


# ---- products/suggestions ------------------------------------------------------------

def parse_suggestions(payload):
    body = as_dict(as_dict(payload).get("suggestionResponse"))
    rows = []
    for row in as_list(body.get("suggestions")):
        term = clean(as_dict(row).get("term"))
        if not term:
            continue
        rows.append({
            "term": term,
            "categories": [{"id": clean(c.get("id")), "name": clean(c.get("name"))}
                           for c in as_list(row.get("category")) if isinstance(c, dict) and c.get("id")],
            "skus": [str(p["skuId"]) for p in as_list(row.get("products")) if isinstance(p, dict) and p.get("skuId")],
        })
    return {"corrected_query": clean(g(body, "spellCheck", "correctedQuery")), "suggestions": rows}


# ---- products/similar ----------------------------------------------------------------

def _compare_row(item):
    item = as_dict(item)
    info = as_dict(item.get("reviewInfo"))
    shown = as_dict(info.get("displayableStats"))
    price = _money(g(item, "price", "displayableCustomerPrice"))
    return {
        "sku": clean(item.get("skuId")),
        "name": clean(g(item, "name", "short")),
        "link": _link(g(item, "url", "relativePdp")),
        "reviews_link": _link(g(item, "url", "reviewStandaloneUrl")),
        "image": _link(g(item, "primaryImage", "piscesHref")),
        "price": price,
        "currency": CURRENCY if price is not None else None,
        "rating": rating(shown.get("rating")) or rating(info.get("averageRating")),
        "reviews_count": to_int(shown.get("reviewCount")) if shown.get("reviewCount") is not None
        else to_int(info.get("reviewCount")),
        "syndicated_reviews": _review_summaries(info),
        "pros": text_list([g(f, "name") or f for f in as_list(info.get("proFeatures"))]),
        "cons": text_list([g(f, "name") or f for f in as_list(info.get("conFeatures"))]),
        "description": multiline(g(item, "description", "long")),
        "specifications": _specifications(item.get("specificationGroups")),
    }


def parse_similar(answer):
    """The compare block -> {"product": the row asked about, "products":
    the comparable ones}; None when the SKU does not exist."""
    product = g(answer, "data", "productBySkuId")
    if not isinstance(product, dict) or not product.get("skuId"):
        return None
    rows = []
    for placement in as_list(g(answer, "data", "recommendations", "subPlacements")):
        for rec in as_list(as_dict(placement).get("recommendations")):
            item = as_dict(rec).get("item")
            if isinstance(item, dict) and item.get("skuId"):
                rows.append(_compare_row(item))
    return {"product": _compare_row(product), "products": rows}


# ---- categories ----------------------------------------------------------------------

def parse_category(answer):
    """The category query -> the category, its path from the root and its
    direct subcategories; None when the id is unknown."""
    category = g(answer, "data", "productCategory")
    if not isinstance(category, dict) or not category.get("id") or not category.get("name"):
        return None
    meta = as_dict(category.get("meta"))
    children = _subcategories(category.get("narrowerTerms"))
    return {
        "id": clean(category.get("id")),
        "name": clean(category.get("name")),
        "link": _link(category.get("seoUrl")),
        "path": _category_path(g(category, "broaderTerms", "primaryLineage")),
        "title": clean(meta.get("title")),
        "description": clean(meta.get("description")),
        "keywords": text_list(as_list(meta.get("keywords"))),
        "started_at": _utc(category.get("startDate")),
        "template": clean(category.get("template")),
        "subcategories_count": len(children),
        "subcategories": children,
    }


# ---- stores --------------------------------------------------------------------------

def _hours(rows):
    out = []
    for row in as_list(rows):
        if not isinstance(row, dict) or not row.get("date"):
            continue
        closed = bool(row.get("isClosed"))
        out.append({"date": iso_day(row.get("date")), "opens": None if closed else clean(row.get("open")),
                    "closes": None if closed else clean(row.get("close")), "is_closed": closed,
                    "is_open_24_hours": bool(row.get("isAvailable24Hours"))})
    return out


def parse_store(store, distance=None):
    """A store location -> one row; None when it carries no id."""
    store = as_dict(store)
    store_id = clean(store.get("locationId"))
    if not store_id:
        return None
    place = as_dict(store.get("physicalLocation"))
    hours = {str(h.get("type") or "").upper(): h.get("openHours") for h in as_list(store.get("hours")) if isinstance(h, dict)}
    row = {
        "id": store_id,
        "name": clean(store.get("displayName")) or clean(store.get("name")),
        "internal_name": clean(store.get("name")),
        "link": refs.store_link(store_id),
        "status": clean(store.get("status")),
        "type": clean(store.get("storeType")),
        "location_type": clean(store.get("locationSubType")) or clean(store.get("locationType")),
        "phone": clean(store.get("phone")),
        "address": {
            "line1": clean(place.get("addr1")),
            "line2": clean(place.get("addr2")),
            "city": clean(place.get("city")),
            "state": clean(place.get("state")),
            "zip": clean(place.get("zipCode")),
            "country": clean(place.get("country")),
        },
        "latitude": num(place.get("latitude")),
        "longitude": num(place.get("longitude")),
        "timezone": clean(g(store, "timeZone", "description")),
        "services": text_list(as_list(store.get("services"))),
        "has_pickup": bool_or_none(store.get("hasPickup")),
        "is_open_box_shipped_from_store": bool_or_none(store.get("openBoxShipFromStoreEligible")),
        "opened_on": iso_day(store.get("openDate")),
        "closed_on": iso_day(store.get("closeDate")),
        "hours": _hours(hours.get("SALES")),
        "curbside_hours": _hours(hours.get("CURB")),
    }
    if distance is not None:
        row = {**{k: row[k] for k in ("id", "name")}, "distance_miles": num(distance),
               **{k: v for k, v in row.items() if k not in ("id", "name")}}
    return row


def parse_stores(answer):
    """The store lookup -> rows nearest first; None when the site could
    not place the ZIP code (it answers no lookup at all)."""
    lookup = g(answer, "data", "storeLookupByZip")
    if not isinstance(lookup, dict):
        return None
    rows = []
    for item in as_list(lookup.get("stores")):
        row = parse_store(as_dict(item).get("storeLocation"), as_dict(item).get("distance"))
        if row:
            rows.append(row)
    return rows


# ---- sellers -------------------------------------------------------------------------

def parse_seller(answer):
    """The seller query -> the seller's record; None when the id is
    unknown (the site answers every field null)."""
    seller = g(answer, "data", "marketplaceSeller")
    if not isinstance(seller, dict) or seller.get("id") is None:
        return None
    stats = as_dict(g(seller, "sellerReviewStats", "stats"))
    spread = as_dict(stats.get("ratingDistribution"))
    status = clean(seller.get("status"))
    return {
        "id": to_int(seller.get("id")),
        "name": clean(seller.get("name")),
        "description": multiline(seller.get("description")),
        "status": status.lower() if status else None,
        "rating": rating(stats.get("averageRating")),
        "reviews_count": to_int(stats.get("totalReviewCount")) or 0,
        "rating_distribution": [{"stars": stars, "count": to_int(spread.get(word)) or 0}
                                for stars, word in ((5, "five"), (4, "four"), (3, "three"), (2, "two"), (1, "one"))]
        if spread else [],
        "recent_reviews": [{
            "id": clean(r.get("reviewId")),
            "rating": to_int(r.get("rating")),
            "title": clean(r.get("reviewTitle")),
            "text": multiline(r.get("reviewText")),
            "submitted_at": _utc(r.get("submittedAt")),
        } for r in as_list(g(seller, "reviews", "results")) if isinstance(r, dict)],
    }


# ---- BSIN ----------------------------------------------------------------------------

def bsin_skus(answer):
    """The SKUs behind a BSIN, Best Buy's own new item first, then other
    new ones, then the rest; [] for an unknown BSIN."""
    rows = [r for r in as_list(g(answer, "data", "bsinProduct", "products")) if isinstance(r, dict) and r.get("skuId")]

    def rank(row):
        own = g(row, "seller", "classification") == "1P"
        new = _condition(row) == "new"
        return (0 if own and new else 1 if new else 2 if own else 3)
    return [str(r["skuId"]) for r in sorted(rows, key=rank)]
