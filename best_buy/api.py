"""The /best-buy/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs).

Caching (cache.cached_call) sits here, on the shared pieces as well as on
the answers, so endpoints that read the same thing pay one fetch:

  best_buy.product        a product in full (the page's five operations in
                          one request); products/details answers it
  best_buy.identity       who a SKU is (name, link, rating): the header of
                          products/availability; an unknown SKU is a 404
                          there, where the fulfillment call itself answers
                          "not available" for any number
  best_buy.bsin           the SKU behind a BSIN (a product link without
                          /sku/): every product endpoint resolves through it
  best_buy.nearest_store  the store nearest a ZIP code, for
                          products/availability without `store`

The three listings (products/search, categories/products, deals/list) are
one upstream call: the result page's own query, as a keyword search or as
a category listing, with the caller's filters as facets.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import config
from cache import cached_call
from best_buy import cache_config as ttl
from best_buy import fetch, parsers, refs
from best_buy.fetch import BestBuyNotFound, BestBuyUpstreamError
from scraper_utils import fanout
from scraper_utils.values import amount

BULK_WORKERS = 5        # products/bulk: 20 products are five requests of four, sent side by side

_SORT_NAMES = {token: name for name, token in refs.SORTS.items()}
_REVIEW_SORT_NAMES = {token: name for name, token in refs.REVIEW_SORTS.items()}
_CONDITION_NAMES = {token: name for name, token in refs.CONDITIONS.items()}
_RATING_NAMES = {token: name for name, token in refs.MIN_RATINGS.items()}
_DEAL_NAMES = {token: name for name, token in refs.DEAL_TYPES.items()}


# ---- shared: a product reference -> its SKU ------------------------------------------

def _bsin_sku(bsin):
    skus = parsers.bsin_skus(fetch.fetch_bsin(bsin))
    if not skus:
        raise BestBuyNotFound(f"product {bsin} not found")
    return skus[0]


def _sku(product):
    """The SKU of a validated `product` param: a SKU as it is, a BSIN
    through the site's lookup (cached)."""
    if not refs.is_bsin(product):
        return product
    return cached_call("best_buy.bsin", {"bsin": product}, _bsin_sku, ttl.BSIN_CACHE)


def _fetch_identity(sku):
    answer = fetch.fetch_identity(sku)
    identity = parsers.parse_identity(answer)
    if identity is None:
        if fetch.missing(answer, "productBySkuId"):
            raise BestBuyNotFound(f"product {sku} not found")
        raise BestBuyUpstreamError(f"product {sku} answered no record")
    return identity


def _identity(sku):
    return cached_call("best_buy.identity", {"sku": sku}, _fetch_identity, ttl.IDENTITY_CACHE)


# ---- listings ------------------------------------------------------------------------

def _user_facets(data):
    """The caller's filters as (facet, value) pairs, in a stable order."""
    facets = []
    for brand in data.get("brand") or []:
        facets.append((refs.FACET_BRAND, brand))
    low, high = data.get("min_price"), data.get("max_price")
    if low is not None or high is not None:
        facets.append((refs.FACET_PRICE, f"{_bound(low or 0)} to {_bound(high if high is not None else refs.MAX_PRICE_BOUND)}"))
    if data.get("min_rating"):
        facets.append((refs.FACET_RATING, data["min_rating"]))
    if data.get("condition"):
        facets.append((refs.FACET_CONDITION, data["condition"]))
    if data.get("on_sale"):
        facets.append((refs.FACET_OFFERS, refs.ON_SALE))
    if data.get("sold_by_best_buy"):
        facets.append((refs.FACET_SOLD_BY, refs.SOLD_BY_BEST_BUY))
    facets += [tuple(pair) for pair in data.get("filter") or []]
    return facets


def _bound(value):
    """A price bound as the site writes it: 500, 199.99."""
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _check_facets(answer, facets):
    """The site silently drops a refinement on a facet it does not have,
    and would answer the unrefined listing: say so instead."""
    applied = parsers.applied_facets((answer.get("data") or {}).get("search") or {})
    unknown = sorted({name for name, _ in facets if name not in applied})
    if unknown:
        raise ValueError(f"filter: no facet named {', '.join(repr(n) for n in unknown)} on this listing; "
                         "use the `facet` of a facet the result page returns.")


def _filters(data, listing, hidden=()):
    """What the listing was refined by, read back from the site's own
    account of the request (automatic refinements left out)."""
    by_facet = {row["facet"]: row["values"] for row in listing["applied_filters"] if not row["is_automatic"]}
    low, high = data.get("min_price"), data.get("max_price")
    named = {refs.FACET_BRAND, refs.FACET_PRICE, refs.FACET_RATING, refs.FACET_CONDITION, refs.FACET_SOLD_BY,
             refs.FACET_CATEGORY, *hidden}
    extra = [{"facet": facet, "value": value} for facet, values in by_facet.items() if facet not in named
             for value in values if not (facet == refs.FACET_OFFERS and value == refs.ON_SALE and data.get("on_sale"))]
    return {
        "brand": by_facet.get(refs.FACET_BRAND) or [],
        "min_price": amount(low),
        "max_price": amount(high),
        "min_rating": _rating_name((by_facet.get(refs.FACET_RATING) or [None])[0]),
        "condition": _CONDITION_NAMES.get((by_facet.get(refs.FACET_CONDITION) or [None])[0]),
        "is_on_sale": bool(data.get("on_sale")),
        "is_sold_by_best_buy": bool(data.get("sold_by_best_buy")),
        "filter": extra,
    }


def _rating_name(token):
    """The site's rating value -> what min_rating takes: 4, or "top_rated"."""
    name = _RATING_NAMES.get(token)
    return int(name) if name and name.isdigit() else name


def _listing(data, query=None, category=None, base=(), default_sort=None):
    """One listing page: `base` are the route's own facets, then the
    caller's. Returns (answer, parsed listing)."""
    page, limit = data["page"], data["limit"]
    if page > refs.last_listing_page(limit):
        raise ValueError(f"page: the site serves {refs.MAX_LISTING_RESULTS} results of a listing at most; with "
                         f"limit={limit} the last page is {refs.last_listing_page(limit)}.")
    facets = _user_facets(data)
    answer = fetch.fetch_listing(query=query, category=category, page=page, limit=limit,
                                 sort=data.get("sort") or default_sort, facets=[*base, *facets],
                                 within=data.get("query") if category and not query else None)
    listing = parsers.parse_listing(answer, page, limit)
    if facets and not listing["redirect_link"] and (answer["data"].get("search") or {}).get("numFound") is not None:
        _check_facets(answer, facets)
    return answer, listing


def _listing_body(data, listing, hidden=()):
    return {
        "sort": listing["sort"] or _SORT_NAMES.get(data.get("sort")),
        "filters": _filters(data, listing, hidden),
        "products": listing["products"],
        "facets": listing["facets"],
        "sort_options": listing["sort_options"],
        "pagination": listing["pagination"],
    }


def _redirect_category(link):
    """The category a search redirect points at, or None."""
    try:
        return refs.resolve_category(link) if link else None
    except ValueError:
        return None


def _products_search(**data):
    query = data["query"]
    base = [(refs.FACET_CATEGORY, data["category"])] if data.get("category") else []
    answer, listing = _listing(data, query=query, base=base)
    redirected = None
    target = _redirect_category(listing["redirect_link"])
    if target:
        # the site answers some queries ("gift cards") with a category link
        # and no rows: the visitor lands on that category's listing
        redirected = target
        answer, listing = _listing({**data, "query": None}, category=target, default_sort=refs.SORTS["best_selling"])
    return {
        "query": query,
        "corrected_query": listing["corrected_query"],
        "category": data.get("category"),
        "detected_category": listing["detected_category"],
        "redirected_to_category": redirected,
        "redirect_link": listing["redirect_link"] if not redirected else None,
        "related_searches": listing["related_searches"],
        **_listing_body(data, listing),
    }


def products_search(**data):
    return cached_call("best_buy.search", data, _products_search, ttl.LISTING_CACHE)


def _category_listing(data, category, base=()):
    answer, listing = _listing(data, category=category, base=base, default_sort=refs.SORTS["best_selling"])
    record = parsers.parse_listing_category(answer)
    if record is None:
        raise BestBuyNotFound(f"category {category} not found")
    return record, listing


def _categories_products(**data):
    record, listing = _category_listing(data, data["category"])
    return {"category": record, "query": data.get("query"), **_listing_body(data, listing)}


def categories_products(**data):
    return cached_call("best_buy.category_products", data, _categories_products, ttl.LISTING_CACHE)


def _deals_list(**data):
    category = data.get("category") or refs.ROOT_CATEGORY
    record, listing = _category_listing(data, category, base=[(refs.FACET_OFFERS, data["type"])])
    record.pop("subcategories", None)
    return {"type": _DEAL_NAMES.get(data["type"], data["type"]),
            "category": record if data.get("category") else None,
            **_listing_body(data, listing, hidden=(refs.FACET_OFFERS,))}


def deals_list(**data):
    return cached_call("best_buy.deals", data, _deals_list, ttl.LISTING_CACHE)


# ---- products/details and products/bulk ----------------------------------------------

def _parsed(sku, parts):
    """One SKU's five answers -> its record; None when the SKU does not
    exist; an upstream error when the main answer is simply empty."""
    record = parsers.parse_product(parts)
    if record is not None:
        return record
    if parsers.product_missing(parts):
        return None
    raise BestBuyUpstreamError(f"product {sku} answered no record")


def _product(sku):
    record = _parsed(sku, fetch.fetch_products([sku])[0])
    if record is None:
        raise BestBuyNotFound(f"product {sku} not found")
    return record


def _products_details(product):
    sku = _sku(product)
    return cached_call("best_buy.product", {"sku": sku}, _product, ttl.PRODUCT_CACHE)


def products_details(**data):
    return _products_details(data["product"])


_bulk_pool = None
_bulk_pool_lock = threading.Lock()


def _pool():
    """The long-lived workers that send a bulk call's requests side by
    side. Long-lived, so each worker keeps its own HTTP session."""
    global _bulk_pool
    with _bulk_pool_lock:
        if _bulk_pool is None:
            _bulk_pool = ThreadPoolExecutor(max_workers=BULK_WORKERS, thread_name_prefix="best-buy-bulk")
        return _bulk_pool


def _resolve_or_none(product):
    try:
        return _sku(product)
    except BestBuyNotFound:
        return None


def _batch(skus):
    return [_parsed(sku, parts) for sku, parts in zip(skus, fetch.fetch_products(skus))]


def _products_bulk(products):
    resolved = [(ref, _resolve_or_none(ref)) for ref in products]
    skus = list(dict.fromkeys(sku for _, sku in resolved if sku))
    size = fetch.MAX_BATCH_PRODUCTS
    chunks = [skus[i:i + size] for i in range(0, len(skus), size)]
    records = {}
    for chunk, rows in zip(chunks, fanout.on_pool(_pool(), [partial(_batch, chunk) for chunk in chunks])):
        records.update(dict(zip(chunk, rows)))
    found, not_found, seen = [], [], set()
    for ref, sku in resolved:
        record = records.get(sku) if sku else None
        if record is None:
            not_found.append(ref)
        elif sku not in seen:
            seen.add(sku)
            found.append(record)
    return {"requested_count": len(products), "found_count": len(found), "not_found": not_found, "products": found}


def products_bulk(**data):
    return cached_call("best_buy.bulk", data, _products_bulk, ttl.BULK_CACHE)


# ---- products/offers -----------------------------------------------------------------

def _products_offers(product):
    sku = _sku(product)
    answer = fetch.fetch_offer_identity(sku)
    identity = parsers.parse_offer_identity(answer)
    if identity is None:
        if fetch.missing(answer, "productBySkuId"):
            raise BestBuyNotFound(f"product {sku} not found")
        raise BestBuyUpstreamError(f"offers of {sku} answered no product")
    asked =[(sku, None, identity["seller"], identity["condition"] or "new", identity["link"])]
    asked += [(sku, code, identity["seller"], None, identity["link"]) for code in identity["open_box_codes"]]
    asked += [(row["sku"], None, row["seller"], row["condition"] or "new", row["link"]) for row in identity["other_skus"]]
    answers = fetch.fetch_offer_prices([(row[0], row[1]) for row in asked])
    offers = [offer for offer in (parsers.parse_offer(answer, row[0], row[2], row[3], row[4])
                                  for row, answer in zip(asked, answers)) if offer]
    offers.sort(key=lambda o: o["price"])
    prices = [o["price"] for o in offers]
    return {
        "sku": identity["sku"],
        "bsin": identity["bsin"],
        "name": identity["name"],
        "link": identity["link"],
        "offers_count": len(offers),
        "lowest_price": min(prices) if prices else None,
        "highest_price": max(prices) if prices else None,
        "currency": parsers.CURRENCY if prices else None,
        "offers": offers,
    }


def products_offers(**data):
    return cached_call("best_buy.offers", data, _products_offers, ttl.OFFERS_CACHE)


# ---- products/availability -----------------------------------------------------------

def _fetch_stores(zip, radius):
    rows = parsers.parse_stores(fetch.fetch_stores(zip, radius))
    if rows is None:
        raise ValueError(f"zip: the site does not know the ZIP code {zip}.")
    return rows


def _nearest_store(zip):
    rows = _fetch_stores(zip, refs.NEAREST_STORE_RADIUS)
    return rows[0]["id"] if rows else None


def _products_availability(product, zip, store):
    sku = _sku(product)
    identity = _identity(sku)
    if not store:
        store = cached_call("best_buy.nearest_store", {"zip": zip}, _nearest_store, ttl.NEAREST_STORE_CACHE)
    answer = fetch.fetch_fulfillment(sku, zip, store or fetch.SITE_STORE)
    return {"name": identity["name"], "link": identity["link"], **parsers.parse_availability(answer, sku, zip)}


def products_availability(**data):
    data = {**data, "zip": data.get("zip") or config.BEST_BUY_DEFAULT_ZIP}
    answer = cached_call("best_buy.availability", data, _products_availability, ttl.AVAILABILITY_CACHE)
    return {"sku": answer["sku"], "name": answer["name"], "link": answer["link"],
            **{k: v for k, v in answer.items() if k not in ("sku", "name", "link")}}


# ---- products/reviews ----------------------------------------------------------------

def _products_reviews(product, page, limit, sort, rating, query):
    sku = _sku(product)
    raw, raw_identity = fetch.fetch_reviews(sku, page, limit, sort, rating, query)
    identity, parsed = parsers.parse_identity(raw_identity), parsers.parse_reviews(raw)
    if identity is None or parsed is None:
        if fetch.missing(raw, "productBySkuId") or fetch.missing(raw_identity, "productBySkuId"):
            raise BestBuyNotFound(f"product {sku} not found")
        raise BestBuyUpstreamError(f"reviews of {sku} answered no product")
    total = parsed["total"]
    return {
        "sku": sku,
        "name": identity["name"],
        "link": identity["link"],
        "rating": identity["rating"],
        "reviews_count": identity["reviews_count"] or 0,
        "sort": _REVIEW_SORT_NAMES.get(sort, sort),
        "rating_filter": rating or [],
        "query": query,
        "photos": parsed["photos"],
        "reviews": parsed["reviews"],
        "pagination": {"page": page, "items_per_page": limit, "total_count": total,
                       "total_pages": -(-total // limit) if total else 0},
    }


def products_reviews(**data):
    return cached_call("best_buy.reviews", data, _products_reviews, ttl.REVIEWS_CACHE)


# ---- products/questions --------------------------------------------------------------

def _products_questions(product, page, limit, query):
    sku = _sku(product)
    raw, raw_identity = fetch.fetch_questions(sku, page, limit, query)
    identity, parsed = parsers.parse_identity(raw_identity), parsers.parse_questions(raw)
    if identity is None or parsed is None:
        if fetch.missing(raw, "productBySkuId") or fetch.missing(raw_identity, "productBySkuId"):
            raise BestBuyNotFound(f"product {sku} not found")
        raise BestBuyUpstreamError(f"questions of {sku} answered no product")
    rows = parsed["questions"]
    if query:
        # the site counts a product's questions, not a search's matches
        pagination = {"page": page, "items_per_page": limit, "total_count": None, "total_pages": None,
                      "has_next": len(rows) >= limit}
    else:
        total = parsed["counts"]["questions"]
        pagination = {"page": page, "items_per_page": limit, "total_count": total,
                      "total_pages": -(-total // limit) if total else 0}
    return {
        "sku": sku,
        "name": identity["name"],
        "link": identity["link"],
        "questions_link": parsed["link"],
        "query": query,
        "counts": parsed["counts"],
        "summary": parsed["summary"],
        "questions": rows,
        "pagination": pagination,
    }


def products_questions(**data):
    return cached_call("best_buy.questions", data, _products_questions, ttl.QUESTIONS_CACHE)


# ---- products/suggestions ------------------------------------------------------------

def _products_suggestions(query, limit):
    parsed = parsers.parse_suggestions(fetch.fetch_suggestions(query, limit))
    rows = parsed["suggestions"][:limit]
    return {"query": query, "corrected_query": parsed["corrected_query"], "count": len(rows), "suggestions": rows}


def products_suggestions(**data):
    return cached_call("best_buy.suggestions", data, _products_suggestions, ttl.SUGGESTIONS_CACHE)


# ---- products/similar ----------------------------------------------------------------

def _products_similar(product, limit):
    sku = _sku(product)
    answer = fetch.fetch_similar(sku, limit)
    parsed = parsers.parse_similar(answer)
    if parsed is None:
        if fetch.missing(answer, "productBySkuId"):
            raise BestBuyNotFound(f"product {sku} not found")
        raise BestBuyUpstreamError(f"products similar to {sku} answered no product")
    rows = parsed["products"][:limit]
    return {"product": parsed["product"], "count": len(rows), "products": rows}


def products_similar(**data):
    return cached_call("best_buy.similar", data, _products_similar, ttl.SIMILAR_CACHE)


# ---- categories/list -----------------------------------------------------------------

def _categories_list(category):
    category = category or refs.ROOT_CATEGORY
    answer = fetch.fetch_category(category)
    record = parsers.parse_category(answer)
    if record is None:
        if fetch.missing(answer, "productCategory") or fetch.error_paths(answer, "NOT_FOUND"):
            raise BestBuyNotFound(f"category {category} not found")
        raise BestBuyUpstreamError(f"category {category} answered no record")
    return record


def categories_list(**data):
    return cached_call("best_buy.categories", data, _categories_list, ttl.CATEGORIES_CACHE)


# ---- stores --------------------------------------------------------------------------

def _stores_search(zip, radius):
    rows = _fetch_stores(zip, radius)
    return {"zip": zip, "radius_miles": radius, "count": len(rows), "stores": rows}


def stores_search(**data):
    return cached_call("best_buy.stores", data, _stores_search, ttl.STORES_CACHE)


def _stores_details(store):
    answer = fetch.fetch_store(store)
    row = parsers.parse_store((answer.get("data") or {}).get("storeById"))
    if row is None:
        if fetch.missing(answer, "storeById"):
            raise BestBuyNotFound(f"store {store} not found")
        raise BestBuyUpstreamError(f"store {store} answered no record")
    return row


def stores_details(**data):
    return cached_call("best_buy.store", data, _stores_details, ttl.STORES_CACHE)


# ---- sellers -------------------------------------------------------------------------

def _sellers_details(seller):
    record = parsers.parse_seller(fetch.fetch_seller(seller))
    if record is None:
        raise BestBuyNotFound(f"seller {seller} not found")
    return record


def sellers_details(**data):
    return cached_call("best_buy.seller", data, _sellers_details, ttl.SELLER_CACHE)
