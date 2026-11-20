"""Best Buy transport: plain curl_cffi with chrome TLS on the calls the
storefront's own pages make. No browser, no login, no cookie and no token.
One session per request thread on the exit config.BEST_BUY_EXIT names (a
gateway exit in production: the site resets the GraphQL calls of the
server's own address); a retryable failure drops the session, so the retry
rides a fresh exit, and an exit the site refused is set aside for a while
in favour of the next one (config.BEST_BUY_FALLBACK_EXITS).

Three upstream surfaces, all on www.bestbuy.com (2026-11-19):

  the GraphQL gateway   POST /gateway/graphql
      one operation as {operationName, variables, extensions, query}, or
      a JSON array of them: the gateway answers an array in the same
      order. An array carries at most 25 operations (30 is HTTP 400), and
      a body above about 64 KB is never answered (73 KB hung until the
      timeout, 133 KB was HTTP 413), so batches are cut at 20 operations
      and 62 KB.
      Answers are HTTP 200 even when they fail; what failed is in
      `errors`, each with a `path` and an `extensions.code`:
        NOT_FOUND on <root>.<id> with a null root   the record does not exist
        401 on ...fulfillmentOptions                always: the gateway serves
                                                    that field only through
                                                    the GET below
        NOT_FOUND on ...arModels, reviewInfo...     a field the record lacks
      A wrong enum value or variable type is HTTP 400 "Bad User Input"
      with no detail; introspection is off.
  fulfillment           GET /gateway/graphql/fulfillment?variables=<json>
      shipping, delivery and store pickup of one SKU for a ZIP code and a
      store; with showNearbyLocations the stock of the stores around it.
      Several comma-separated SKUs answer their button states only.
      An unknown SKU answers NOT_AVAILABLE, not an error.
  search suggestions    GET /suggest/v1/fragment/suggest/www?query&count&searchVariant
      count is 1 to 20 (21 is HTTP 400 "maximum is 20"). The call must
      not carry an Origin header: with one it answers 403 "Invalid CORS
      request".

A refusal of the exit is not an HTTP status: the edge resets the HTTP/2
stream (curl error 92) or holds the request open without answering until
the client gives up. Both are raised as BestBuyBlocked.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  BestBuyUpstreamError  transport failure / 5xx / an answer without data   retryable
  BestBuyBlocked        403 / 429 / a reset or held request / a non-JSON   retryable, on the next exit
                        page
  BestBuyBadRequest     the site rejected the parameters (HTTP 400)        never retried
  BestBuyNotFound       unknown product / category / store / seller        never retried
"""
import json
import threading
import time
import uuid
from urllib.parse import quote

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from best_buy import queries
from best_buy.refs import MAX_PAGE_SIZE, MAX_STORES, MAX_SUGGESTIONS, SITE
from scraper_utils import retry, sessions

IMPERSONATE = "chrome"
TIMEOUT = 12            # a 100-row page answers in about 3 s; an exit the site turned against is held open until this runs out

GRAPHQL_URL = SITE + "/gateway/graphql"
FULFILLMENT_URL = SITE + "/gateway/graphql/fulfillment"
SUGGEST_URL = SITE + "/suggest/v1/fragment/suggest/www"

MAX_BATCH_OPERATIONS = 20       # the gateway takes 25 in one body and refuses 30
MAX_BATCH_BYTES = 62000         # 60.1 KB (four products) answers, 73 KB hung until the timeout

# The site's own defaults for a visitor it has not located (the variables
# its pages send): its home ZIP code and the store next to it.
SITE_ZIP = "55423"
SITE_STORE = "7"

LISTING_CLIENT = "plp-web"      # x-client-id of the search / category page's calls
PRODUCT_CLIENT = "pdp-web"      # x-client-id of the product page's calls
_CLIENT_LIBRARY = {LISTING_CLIENT: "4.2.10", PRODUCT_CLIENT: "4.1.6"}   # each page's own Apollo build

# The headers of the pages' own GraphQL calls, as captured from the search
# and the product page on 2026-11-19: every header but the ones a session
# sets itself. Per call are added: referer, x-client-id, x-page-request-id
# and x-requested-for-operation-name.
GRAPHQL_HEADERS = {
    "accept": "application/graphql-response+json,application/json;q=0.9",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "origin": SITE,
    "pragma": "no-cache",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-dynatrace": "",
}
# The page's fulfillment GET (captured the same day): same-origin, so no Origin.
FULFILLMENT_HEADERS = {
    "accept": "*/*",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-dynatrace": "",
}
# The search box's suggestion GET (captured the same day). The page really
# sends "authorization: undefined"; an Origin header is refused (403).
SUGGEST_HEADERS = {
    "accept": "application/json",
    "authorization": "undefined",
    "content-type": "application/json",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-client-id": "Search-Web-View",
}
SUGGEST_VARIANT = "B"           # the searchVariant the search box sends


class BestBuyUpstreamError(UpstreamError):
    """Transport failure, 5xx or an answer without data — retryable."""


class BestBuyBlocked(BestBuyUpstreamError, Blocked):
    """403 / 429, a reset stream or a page where JSON was expected — retryable."""


class BestBuyBadRequest(BadRequest):
    """The site rejected the parameters (HTTP 400) — never retried."""


class BestBuyNotFound(NotFound):
    """No such product, category, store or seller — never retried."""


# ---- sessions and the exit ladder ----------------------------------------------------

# One curl session per request thread (a curl handle must not be shared
# across threads). The calls are stateless: no cookie or token is carried.
_local = threading.local()

# The site holds or cuts the GraphQL calls of an exit it has turned against
# (no HTTP status, see _send): on 2026-11-19 it did so to an exit that had
# carried a few hundred requests in a quarter of an hour, for 45 minutes
# and more, and to the exits of the same continent with it. Such an exit is
# set aside for config.BEST_BUY_BLOCK_HOLD_S and new sessions ride the next
# one of config.BEST_BUY_EXIT + config.BEST_BUY_FALLBACK_EXITS.
# Process-wide: one thread's refusal spares the other 63 the same wait.
_held = {}                  # exit -> time.monotonic() until which it is set aside
_held_lock = threading.Lock()


def exits():
    """The exits in the order they are tried."""
    return [config.BEST_BUY_EXIT, *(getattr(config, "BEST_BUY_FALLBACK_EXITS", None) or ())]


def current_exit(now=None):
    """The first exit that is not set aside; when all are, the one whose
    hold ends first."""
    now = time.monotonic() if now is None else now
    ladder = exits()
    with _held_lock:
        for name in ladder:
            if _held.get(name, 0) <= now:
                return name
        return min(ladder, key=lambda name: _held.get(name, 0))


def hold_exit(name, now=None):
    """Set an exit aside after the site refused it."""
    now = time.monotonic() if now is None else now
    with _held_lock:
        _held[name] = now + config.BEST_BUY_BLOCK_HOLD_S


def _build_session():
    name = current_exit()
    sess = egress.exit_session(name, impersonate=IMPERSONATE)
    _local.exit = name
    return sess


def _session():
    """The thread's session. One built on an exit that has since been set
    aside (by any thread) is replaced, so no thread waits out a refusal
    another has already met."""
    if getattr(_local, "session", None) is not None and getattr(_local, "exit", None) != current_exit():
        _drop_session()
    return sessions.thread_session(_local, _build_session)


def _drop_session():
    """Close the thread's session so the retry rides a fresh connection / exit."""
    sessions.drop(_local)


def _on_failure(error):
    """After a retryable failure: an exit the site refused is set aside,
    and the session is dropped either way."""
    if isinstance(error, BestBuyBlocked):
        hold_exit(getattr(_local, "exit", None) or config.BEST_BUY_EXIT)
    _drop_session()


def _retrying(fn):
    """fn() under the shared retry policy: only BestBuyUpstreamError
    (Blocked included) is retried, each time on a fresh session, and on the
    next exit when the site refused this one; NotFound and BadRequest
    surface at once."""
    return retry.run(fn, config, BestBuyUpstreamError, _on_failure)


def _fresh(sess):
    """The session without the cookies earlier answers set: every request
    leaves as a first visit (on the gateway exit the address changes per
    request, and the edge's bot cookies are tied to the address that got them)."""
    try:
        sess.cookies.clear()
    except Exception:
        pass
    return sess


# ---- one request ---------------------------------------------------------------------

def _send(method, url, label, headers, body=None):
    try:
        resp = _fresh(_session()).request(method, url, headers=headers, data=body, timeout=TIMEOUT)
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        if "HTTP/2 stream" in text or "INTERNAL_ERROR" in text:
            raise BestBuyBlocked(f"the site reset the connection for {label} (it refuses this exit)")
        if "timed out" in text.lower() or "GatewayError" in text:
            raise BestBuyBlocked(f"the site held {label} without answering (it refuses this exit)")
        raise BestBuyUpstreamError(f"request failed for {label}: {text[:200]}")
    status = resp.status_code
    if status in (401, 403, 429):
        raise BestBuyBlocked(f"HTTP {status} for {label}: {resp.text[:80]!r}")
    if status == 400:
        raise BestBuyBadRequest(_error_message(resp) or f"the site rejected {label}")
    if status == 404:
        raise BestBuyNotFound(f"{label} not found")
    if status != 200:
        raise BestBuyUpstreamError(f"HTTP {status} for {label}")
    try:
        return resp.json()
    except Exception:
        raise BestBuyBlocked(f"non-JSON answer for {label}: {resp.text[:120]!r}")


def _error_message(resp):
    """What a 400 says: the suggest service's `moreInfo`, or the gateway's
    first error message."""
    try:
        payload = resp.json()
    except Exception:
        return ""
    if isinstance(payload, dict):
        if payload.get("moreInfo"):
            return str(payload["moreInfo"])[:200]
        errors = payload.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return f"the site rejected the request: {str(errors[0].get('message') or '')[:160]}"
    return ""


# ---- the GraphQL gateway -------------------------------------------------------------

def operation(name, query, variables, client=PRODUCT_CLIENT):
    """One operation as the page's Apollo client posts it, with the
    x-client-id its page sends."""
    return {"client": client,
            "body": {"operationName": name, "variables": variables,
                     "extensions": {"clientLibrary": {"name": "@apollo/client", "version": _CLIENT_LIBRARY[client]}},
                     "query": query}}


def page_operation(const, variables, client=PRODUCT_CLIENT):
    """An operation of the site's own (queries.py), by its constant name."""
    return operation(queries.OPERATION_NAMES[const], getattr(queries, const), variables, client)


def _graphql_headers(op_name, client, referer):
    return {**GRAPHQL_HEADERS,
            "referer": referer or SITE + "/",
            "x-client-id": client,
            "x-page-request-id": str(uuid.uuid4()),
            "x-requested-for-operation-name": op_name}


def _checked(payload, label):
    """One operation's answer -> {"data": {...}, "errors": [...]}. An answer
    with no data at all is an upstream failure (retried)."""
    if not isinstance(payload, dict):
        raise BestBuyUpstreamError(f"unexpected answer for {label}: {str(payload)[:120]!r}")
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    data = payload.get("data")
    if not isinstance(data, dict):
        codes = sorted({error_code(e) for e in errors})
        raise BestBuyUpstreamError(f"{label} answered no data ({', '.join(codes) or 'no error given'})")
    return {"data": data, "errors": [e for e in errors if isinstance(e, dict)]}


def graphql(op, label, referer=None):
    """POST one operation -> {"data", "errors"}."""
    body = op["body"]
    headers = _graphql_headers(body["operationName"], op["client"], referer)
    return _retrying(lambda: _checked(_send("POST", GRAPHQL_URL, label, headers, json.dumps(body)), label))


def batches(ops):
    """The operations cut into bodies the gateway answers: at most
    MAX_BATCH_OPERATIONS and MAX_BATCH_BYTES each, order kept."""
    out, current, size = [], [], 2
    for op in ops:
        length = len(json.dumps(op["body"])) + 2
        if current and (len(current) >= MAX_BATCH_OPERATIONS or size + length > MAX_BATCH_BYTES):
            out.append(current)
            current, size = [], 2
        current.append(op)
        size += length
    if current:
        out.append(current)
    return out


def graphql_batch(ops, label, referer=None):
    """POST several operations as ONE array body (at most one batches()
    chunk) -> their answers in the same order."""
    if not ops:
        return []
    first = ops[0]
    headers = _graphql_headers(first["body"]["operationName"], first["client"], referer)
    body = json.dumps([op["body"] for op in ops])
    if len(ops) > MAX_BATCH_OPERATIONS or len(body) > MAX_BATCH_BYTES:
        raise ValueError(f"{label}: {len(ops)} operations / {len(body)} bytes is more than one body carries")

    def once():
        payload = _send("POST", GRAPHQL_URL, label, headers, body)
        if not isinstance(payload, list) or len(payload) != len(ops):
            raise BestBuyUpstreamError(f"{label}: {len(ops)} operations sent, "
                                       f"{len(payload) if isinstance(payload, list) else 'no list'} answered")
        return [_checked(item, label) for item in payload]
    return _retrying(once)


# ---- reading an answer's errors ------------------------------------------------------

def error_code(error):
    return str(((error or {}).get("extensions") or {}).get("code") or "")


def error_paths(answer, code=None):
    """The `path` of every error of an answer (of one code when given)."""
    return [list(e.get("path") or []) for e in answer.get("errors") or [] if code is None or error_code(e) == code]


def missing(answer, root):
    """True when the answer says the record at `root` does not exist: the
    root came back null (or absent) with a NOT_FOUND on it."""
    if (answer.get("data") or {}).get(root) is not None:
        return False
    return any(path[:1] == [root] for path in error_paths(answer, "NOT_FOUND"))


def record(answer, root, label):
    """answer["data"][root] as a dict. NotFound when the site says the
    record does not exist; an upstream error when it is simply absent."""
    value = (answer.get("data") or {}).get(root)
    if isinstance(value, dict):
        return value
    if missing(answer, root):
        raise BestBuyNotFound(f"{label} not found")
    codes = sorted({error_code(e) for e in answer.get("errors") or []})
    raise BestBuyUpstreamError(f"{label} answered no {root} ({', '.join(codes) or 'no error given'})")


# ---- listing: search and category browse ---------------------------------------------

def listing_variables(query=None, category=None, page=1, limit=24, sort=None, facets=(), within=None):
    """The variables of the result page's own query, for a keyword search
    (`query`) or a category listing (`category`). They are the page's, with
    four differences: the sponsored placements are off (the organic rows
    come in `search.documents`), every row is asked in full
    (numDetailedProducts = the page size), package rows are left out
    (removeCombos: a package row carries SKUs only, no name or price) and
    nothing that identifies a visitor is sent."""
    browse = category is not None and not query
    return {
        "input": {"site": "WWW", "queryType": "BROWSE" if browse else "SEARCH",
                  "query": f"categoryid${category}" if browse else query},
        "categoryId": category if browse else query,
        "isBrowse": browse,
        "isPromo": False,
        "isFlash": False,
        "testing": {},
        "pagination": {"pageNumber": page, "offset": limit},
        "filter": {
            "browsedCategory": category if browse else None,
            "enableMarketplace": True,
            "effectivePlanPaidMemberType": None,
            "facets": [{"facetField": name, "value": value} for name, value in facets],
            "searchWithinTerm": within if browse else None,
            "deviceClass": "l",
            "removeCombos": True,
            "availability": {"enableDeliveryFacet": False, "inventoryType": None, "zipCode": SITE_ZIP,
                             "preferredStore": "", "availableStoresList": ""},
            "collapse": True,
            "enableBsinCollapse": None,
            "previewDate": None,
            "enableNestedFacet": True,
            "autoFacet": True,
        },
        "destinationZipCode": SITE_ZIP,
        "locationId": "",
        "includeFragment": False,
        "isSafeMode": False,
        "sellerId": None,
        "isSellerPage": False,
        "sort": {"sort": sort or ""},
        "useWithBestMedia": False,
        "skipSearchDocuments": False,
        "isBestbuyMember": False,
        "fetchGridDocs": True,
        "fetchListDocs": False,
        "multiImageEnabled": False,
        "useCaboSucoFields": True,
        "useDisplayablePriceFields": True,
        "useEcoRebatesFields": True,
        "useGiftWithPurchaseFields": True,
        "useMembershipUpsellFields": True,
        "useOffersFields": True,
        "usePlusXOffersFields": True,
        "usePromotionalOptionListFields": True,
        "useSpendAndGetFields": True,
        "useOfferRankings": False,
        "productPriceInput": _price_input("plp", "medium-plp"),
        "skuOffersInput": _offers_input(),
        "fulfillmentInput": {
            "shipping": {"destinationZipCode": SITE_ZIP},
            "delivery": {"destinationZipCode": SITE_ZIP, "deliveryDateOption": "EARLIEST_AVAILABLE_DATE"},
            "inStorePickup": {"storeId": SITE_STORE, "searchNearby": True, "showNearbyLocations": False},
            "profileCode": None,
            "buttonState": {"fulfillmentOption": None, "context": "PLP", "destinationZipCode": SITE_ZIP,
                            "storeId": SITE_STORE},
        },
        "numDetailedProducts": limit,
        "imageLimit": 1,
        "salesChannel": "LargeView",
        "includeAppleIntelligenceFragment": False,
        "narrowerTermEnabled": browse,
        "badgeFilter": {"variantId": "A"},
    }


def _price_input(context, display_location):
    """The page's productPriceInput for an anonymous visitor."""
    return {"customerId": None, "planPaidMemberType": None, "salesChannel": "LargeView", "selectedCabo": None,
            "selectedSuco": None, "customerAttributes": None, "usePriceWithCart": True, "cartTimestamp": None,
            "useSuco": True, "useCabo": True, "visitorId": "", "context": context,
            "displayLocation": display_location}


def _offers_input():
    return {"salesChannel": "LargeView", "effectivePlanPaidMemberType": None, "checkmarkMessagingRequired": True,
            "maxOffers": 10, "filterFinanceMinPurchaseAmount": False}


def fetch_listing(query=None, category=None, page=1, limit=24, sort=None, facets=(), within=None):
    """One result page of a search or a category -> {"data", "errors"}."""
    variables = listing_variables(query, category, page, limit, sort, facets, within)
    label = f"category {category}" if variables["isBrowse"] else f"search {query!r}"
    referer = f"{SITE}/site/searchpage.jsp?st={quote(query)}" if query else SITE + "/"
    label = f"{label} page {page}"
    op = page_operation("SEARCH", variables, LISTING_CLIENT)

    def once():
        answer = _checked(_send("POST", GRAPHQL_URL, label, _graphql_headers(op["body"]["operationName"], op["client"], referer),
                                json.dumps(op["body"])), label)
        # A page past the end answers every field of `search` NOT_FOUND; any
        # other code on one of them is the search service failing.
        failed = sorted({error_code(e) for e in answer["errors"]
                         if list(e.get("path") or [])[:1] == ["search"] and len(e.get("path") or []) <= 2
                         and error_code(e) != "NOT_FOUND"})
        unknown_category = variables["isBrowse"] and not (answer["data"].get("productCategory") or {}).get("id")
        if failed and not unknown_category:     # an unknown category fails its search too: api.py answers 404
            raise BestBuyUpstreamError(f"{label}: the search service failed ({', '.join(failed)})")
        return answer
    return _retrying(once)


# ---- products ------------------------------------------------------------------------

PRODUCT_PARTS = ("PRODUCT", "PRODUCT_FEATURES", "PRODUCT_PRICE", "PRODUCT_REVIEW_STATS", "PRODUCT_SELLER")
MAX_BATCH_PRODUCTS = MAX_BATCH_OPERATIONS // len(PRODUCT_PARTS)     # 4: five products in one body were never answered


def price_variables(sku, open_box_condition=None):
    return {"skuId": sku, "openBoxCondition": open_box_condition,
            "productPriceInput": _price_input("Product-Page", "pdpLarge"),
            "skuOffersInput": _offers_input(),
            "useCaboSucoFields": True, "useDisplayablePriceFields": True, "useEcoRebatesFields": True,
            "useGiftWithPurchaseFields": True, "useMembershipUpsellFields": True, "useOffersFields": False,
            "usePlusXOffersFields": True, "usePromotionalOptionListFields": True, "useSpendAndGetFields": True,
            "useOfferRankings": True}


def product_operations(sku):
    """The product page's five operations for one SKU, in PRODUCT_PARTS order."""
    return [
        page_operation("PRODUCT", {"skuId": sku}),
        page_operation("PRODUCT_FEATURES", {"skuId": sku, "locationId": SITE_STORE, "getStoreZipcode": True,
                                            "enableEcoRebates": True, "shippingZipcode": SITE_ZIP}),
        page_operation("PRODUCT_PRICE", price_variables(sku)),
        page_operation("PRODUCT_REVIEW_STATS", {"skuId": sku, "openBoxCondition": None}),
        page_operation("PRODUCT_SELLER", {"skuId": sku, "isSellerDetailsOrderDetails": False}),
    ]


def fetch_products(skus):
    """The five answers of each SKU, for at most MAX_BATCH_PRODUCTS SKUs in
    one request -> [{part: answer}] in the order asked."""
    if len(skus) > MAX_BATCH_PRODUCTS:
        raise ValueError(f"at most {MAX_BATCH_PRODUCTS} products per request")
    ops = [op for sku in skus for op in product_operations(sku)]
    answers = graphql_batch(ops, f"products {','.join(skus)}")
    size = len(PRODUCT_PARTS)
    return [dict(zip(PRODUCT_PARTS, answers[i * size:(i + 1) * size])) for i in range(len(skus))]


def fetch_offer_identity(sku):
    """Who a SKU is and which other SKUs sell the same item -> {"data", "errors"}."""
    return graphql(operation("ProductOffers", queries.OFFERS, {"skuId": sku}), f"offers of {sku}")


def fetch_offer_prices(pairs):
    """The price answer of each (sku, open-box condition code or None) ->
    [answer] in the order asked. The price operation is 6.7 KB, so a body
    carries nine of them: more are sent as several requests."""
    ops = [page_operation("PRODUCT_PRICE", price_variables(sku, code)) for sku, code in pairs]
    answers = []
    for chunk in batches(ops):
        answers += graphql_batch(chunk, f"prices of {len(chunk)} offers")
    return answers


def fetch_identity(sku):
    """The product page's review-stats answer: name, link, rating, count."""
    return graphql(page_operation("PRODUCT_REVIEW_STATS", {"skuId": sku, "openBoxCondition": None}),
                   f"product {sku}")


def fetch_bsin(bsin):
    """The SKUs behind a BSIN -> {"data", "errors"}; an unknown BSIN
    answers its `products` null."""
    return graphql(operation("BsinProducts", queries.BSIN, {"bsin": bsin}), f"bsin {bsin}")


def fetch_reviews(sku, page, limit, sort, ratings=None, search=None):
    """One page of reviews and, in the same request, the product's
    review-stats answer -> (reviews answer, identity answer)."""
    variables = {"onlyIfRelated": True, "skuId": sku, "page": page, "pageSize": min(limit, MAX_PAGE_SIZE),
                 "sortBy": sort, "includeExtendedFields": True}
    if ratings:
        variables["ratings"] = ",".join(str(r) for r in ratings)
    if search:
        variables["searchText"] = search
    ops = [page_operation("REVIEWS", variables),
           page_operation("PRODUCT_REVIEW_STATS", {"skuId": sku, "openBoxCondition": None})]
    reviews, identity = graphql_batch(ops, f"reviews of {sku} page {page}")
    return reviews, identity


_QUESTIONS_PAGE = "pagination:{page:1 pageSize:$pageSize}"


def fetch_questions(sku, page, limit, search=None):
    """One page of a product's questions and, in the same request, its
    review-stats answer -> (questions answer, identity answer). The page
    number is a literal in the site's own query text, so it is written
    into the text."""
    query = queries.QUESTIONS
    if _QUESTIONS_PAGE not in query:
        raise BestBuyUpstreamError("the questions query no longer carries its page literal")
    query = query.replace(_QUESTIONS_PAGE, f"pagination:{{page:{int(page)} pageSize:$pageSize}}")
    variables = {"skuId": sku, "searchText": search or "", "pageSize": min(limit, MAX_PAGE_SIZE)}
    ops = [operation(queries.OPERATION_NAMES["QUESTIONS"], query, variables),
           page_operation("PRODUCT_REVIEW_STATS", {"skuId": sku, "openBoxCondition": None})]
    questions, identity = graphql_batch(ops, f"questions of {sku} page {page}")
    return questions, identity


def fetch_similar(sku, limit):
    variables = {"useDisplayablePriceFields": True, "placement": "single-compare", "site": "dotcom-l",
                 "limit": limit, "skuId": sku,
                 "productPriceInput": {"salesChannel": "LargeView", "usePriceWithCart": True, "useSuco": True,
                                       "useCabo": True, "visitorId": ""}}
    return graphql(page_operation("SIMILAR", variables), f"products similar to {sku}")


# ---- fulfillment ---------------------------------------------------------------------

def _fulfillment(variables, label, op_name, client, referer=None):
    url = FULFILLMENT_URL + "?variables=" + quote(
        json.dumps({"fulfillmentOptionsInput": variables}, separators=(",", ":")), safe="")
    headers = {**FULFILLMENT_HEADERS, "referer": referer or SITE + "/", "x-client-id": client,
               "x-page-request-id": str(uuid.uuid4()), "x-requested-for-operation-name": op_name}
    return _retrying(lambda: _checked(_send("GET", url, label, headers), label))


def fetch_fulfillment(sku, zip_code, store, nearby=True):
    """Shipping, delivery and pickup of one SKU for a ZIP code and a store
    (the variables of the product page's add-to-cart call), with the
    stores around it when `nearby`."""
    variables = {
        "buttonState": {"context": "PDP", "destinationZipCode": zip_code, "storeId": store},
        "delivery": {"deliveryDateOption": "EARLIEST_AVAILABLE_DATE", "destinationZipCode": zip_code},
        "inStorePickup": {"searchNearby": True, "showNearbyLocations": bool(nearby), "storeId": store},
        "shipping": {"destinationZipCode": zip_code},
        "sku": sku,
    }
    return _fulfillment(variables, f"availability of {sku}", "AddToCart_FulfillmentDynamicQuery", PRODUCT_CLIENT)


# ---- suggestions ---------------------------------------------------------------------

def fetch_suggestions(term, limit):
    url = f"{SUGGEST_URL}?query={quote(term, safe='')}&count={min(limit, MAX_SUGGESTIONS)}&searchVariant={SUGGEST_VARIANT}"
    headers = {**SUGGEST_HEADERS, "referer": SITE + "/",
               "x-request-id": f"{uuid.uuid4()}-{int(time.time() * 1000)}"}
    label = f"suggestions {term!r}"

    def once():
        payload = _send("GET", url, label, headers)
        if not isinstance(payload, dict) or not isinstance(payload.get("suggestionResponse"), dict):
            raise BestBuyUpstreamError(f"unexpected answer for {label}: {str(payload)[:120]!r}")
        return payload
    return _retrying(once)


# ---- categories, stores, sellers (queries of our own) --------------------------------

def fetch_category(category):
    return graphql(operation("CategoryChildren", queries.CATEGORY, {"categoryId": category}, LISTING_CLIENT),
                   f"category {category}")


def fetch_stores(zip_code, radius):
    variables = {"zipCode": zip_code, "limit": MAX_STORES, "miles": radius}
    return graphql(operation("StoresByZip", queries.STORES_BY_ZIP, variables), f"stores near {zip_code}")


def fetch_store(store):
    return graphql(operation("StoreById", queries.STORE, {"locationId": store}), f"store {store}")


def fetch_seller(seller):
    return graphql(operation("SellerById", queries.SELLER, {"id": seller}), f"seller {seller}")
