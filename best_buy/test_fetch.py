"""Offline tests for best_buy/fetch.py: request building, batching, the
exit ladder and the reading of an answer's errors. Nothing here touches
the network (the session is a stand-in).

    python -m pytest best_buy/test_fetch.py -q
"""
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from best_buy import fetch, queries  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class Response:
    def __init__(self, payload=None, status=200, text=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class Cookies:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class Session:
    """Answers each request with the next item of `answers` (a Response, or
    an exception to raise) and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent = []
        self.cookies = Cookies()
        self.closed = False

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.sent.append({"method": method, "url": url, "headers": headers, "data": data, "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self):
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    """Route fetch's sessions to stand-ins: wire(answers) -> the list of
    sessions built (one per exit tried)."""
    built = []
    monkeypatch.setattr(config, "RETRY_BACKOFF", 0)
    monkeypatch.setattr(config, "BEST_BUY_EXIT", "gateway:gb")
    monkeypatch.setattr(config, "BEST_BUY_FALLBACK_EXITS", ("gateway:de", "gateway:in"))
    fetch._held.clear()
    fetch._drop_session()

    def install(answers):
        fetch._drop_session()       # a session kept from the call before has no answer left
        del built[:]
        queue = list(answers)

        def exit_session(name, impersonate="chrome"):
            session = Session([queue.pop(0)])
            session.exit = name
            built.append(session)
            return session
        monkeypatch.setattr(fetch.egress, "exit_session", exit_session)
        return built
    yield install
    fetch._held.clear()
    fetch._drop_session()


# ---- request building ----------------------------------------------------------------

def test_no_header_dict_carries_a_language_or_a_cookie():
    for headers in (fetch.GRAPHQL_HEADERS, fetch.FULFILLMENT_HEADERS, fetch.SUGGEST_HEADERS):
        assert not {"accept-language", "cookie", "user-agent"} & {k.lower() for k in headers}
    # the suggestion call is refused with an Origin header (403 "Invalid CORS request")
    assert "origin" not in {k.lower() for k in fetch.SUGGEST_HEADERS}
    assert "origin" not in {k.lower() for k in fetch.FULFILLMENT_HEADERS}
    assert fetch.GRAPHQL_HEADERS["origin"] == "https://www.bestbuy.com"


def test_graphql_headers_are_the_pages():
    headers = fetch._graphql_headers("ReviewStats_Init", fetch.PRODUCT_CLIENT, None)
    assert headers["accept"] == "application/graphql-response+json,application/json;q=0.9"
    assert headers["x-client-id"] == "pdp-web" and headers["x-requested-for-operation-name"] == "ReviewStats_Init"
    assert headers["referer"] == "https://www.bestbuy.com/" and headers["x-dynatrace"] == ""
    assert len(headers["x-page-request-id"]) == 36
    assert fetch._graphql_headers("x", "plp-web", None)["x-page-request-id"] != headers["x-page-request-id"]


def test_operation_body():
    op = fetch.page_operation("PRODUCT_REVIEW_STATS", {"skuId": "6668952", "openBoxCondition": None})
    assert op["client"] == "pdp-web"
    assert list(op["body"]) == ["operationName", "variables", "extensions", "query"]
    assert op["body"]["operationName"] == "ReviewStats_Init" and op["body"]["query"] == queries.PRODUCT_REVIEW_STATS
    assert op["body"]["extensions"] == {"clientLibrary": {"name": "@apollo/client", "version": "4.1.6"}}
    assert fetch.page_operation("SEARCH", {}, fetch.LISTING_CLIENT)["body"]["extensions"]["clientLibrary"]["version"] == "4.2.10"


def test_queries_are_whole():
    for const, name in queries.OPERATION_NAMES.items():
        text = getattr(queries, const)
        assert f" {name}(" in text and "${" not in text, const
    assert "pagination:{page:1 pageSize:$pageSize}" in queries.QUESTIONS
    for own in (queries.CATEGORY, queries.STORES_BY_ZIP, queries.STORE, queries.SELLER, queries.OFFERS, queries.BSIN):
        assert own.count("{") == own.count("}") and own.startswith("query ")


def test_search_variables():
    v = fetch.listing_variables(query="oled tv", page=3, limit=50, sort="Price-Low-To-High",
                                facets=[("brand_facet", "LG"), ("currentprice_facet", "500 to 900")])
    assert v["input"] == {"site": "WWW", "queryType": "SEARCH", "query": "oled tv"}
    assert v["categoryId"] == "oled tv" and v["isBrowse"] is False and v["narrowerTermEnabled"] is False
    assert v["pagination"] == {"pageNumber": 3, "offset": 50} and v["numDetailedProducts"] == 50
    assert v["sort"] == {"sort": "Price-Low-To-High"}
    assert v["filter"]["facets"] == [{"facetField": "brand_facet", "value": "LG"},
                                     {"facetField": "currentprice_facet", "value": "500 to 900"}]
    assert v["filter"]["browsedCategory"] is None and v["filter"]["searchWithinTerm"] is None
    # organic rows in full, no packages, no sponsored placements, nothing about a visitor
    assert v["useWithBestMedia"] is False and v["skipSearchDocuments"] is False and v["filter"]["removeCombos"] is True
    assert "searchWithBestMediaInput" not in v and v["productPriceInput"]["visitorId"] == ""
    text = json.dumps(v)
    assert "xForwardedFor" not in text and "userAgent" not in text


def test_category_variables():
    v = fetch.listing_variables(category="abcat0500000", within="gaming")
    assert v["input"] == {"site": "WWW", "queryType": "BROWSE", "query": "categoryid$abcat0500000"}
    assert v["categoryId"] == "abcat0500000" and v["isBrowse"] is True and v["narrowerTermEnabled"] is True
    assert v["filter"]["browsedCategory"] == "abcat0500000" and v["filter"]["searchWithinTerm"] == "gaming"
    assert v["sort"] == {"sort": ""} and v["pagination"] == {"pageNumber": 1, "offset": 24}


def test_product_operations_and_batches():
    ops = fetch.product_operations("6668952")
    assert [op["body"]["operationName"] for op in ops] == [queries.OPERATION_NAMES[p] for p in fetch.PRODUCT_PARTS]
    assert all(op["body"]["variables"]["skuId"] == "6668952" for op in ops)
    assert fetch.MAX_BATCH_PRODUCTS == 4
    four = [op for sku in ("6668952", "13300403", "6690109", "6690170") for op in fetch.product_operations(sku)]
    assert [len(chunk) for chunk in fetch.batches(four)] == [20]
    assert len(json.dumps([op["body"] for op in four])) <= fetch.MAX_BATCH_BYTES
    prices = [fetch.page_operation("PRODUCT_PRICE", fetch.price_variables("6668952", code)) for code in [None] * 18]
    chunks = fetch.batches(prices)
    assert [len(chunk) for chunk in chunks] == [9, 9]
    assert all(len(json.dumps([op["body"] for op in chunk])) <= fetch.MAX_BATCH_BYTES for chunk in chunks)
    many = [fetch.page_operation("PRODUCT_REVIEW_STATS", {"skuId": str(n), "openBoxCondition": None}) for n in range(45)]
    assert [len(chunk) for chunk in fetch.batches(many)] == [20, 20, 5]
    assert fetch.batches([]) == []


def test_price_variables():
    assert fetch.price_variables("1", 2)["openBoxCondition"] == 2
    assert fetch.price_variables("1")["openBoxCondition"] is None
    assert fetch.price_variables("1")["productPriceInput"]["visitorId"] == ""


# ---- one request ---------------------------------------------------------------------

def test_graphql_posts_one_operation(wire):
    sessions = wire([Response(load("bsin.json"))])
    answer = fetch.fetch_bsin("J3K4L6QW5C")
    assert answer["data"]["bsinProduct"]["bsin"] == "J3K4L6QW5C" and answer["errors"] == []
    sent = sessions[0].sent[0]
    assert sent["method"] == "POST" and sent["url"] == "https://www.bestbuy.com/gateway/graphql"
    assert json.loads(sent["data"])["variables"] == {"bsin": "J3K4L6QW5C"}
    assert sent["timeout"] == fetch.TIMEOUT and sessions[0].exit == "gateway:gb"
    assert sessions[0].cookies.cleared == 1       # every request leaves without earlier cookies


def test_batch_keeps_the_order_and_checks_the_count(wire):
    reviews, identity = load("reviews.json")
    sessions = wire([Response([reviews, identity])])
    got = fetch.fetch_reviews("6668952", 2, 50, "NEWEST", ratings=[1, 2], search="battery")
    assert got[0]["data"]["productBySkuId"]["reviews"]["totalResults"] == 434
    body = json.loads(sessions[0].sent[0]["data"])
    assert [op["operationName"] for op in body] == ["StandaloneCustomerReviewList_Init", "ReviewStats_Init"]
    assert body[0]["variables"] == {"onlyIfRelated": True, "skuId": "6668952", "page": 2, "pageSize": 50,
                                    "sortBy": "NEWEST", "includeExtendedFields": True, "ratings": "1,2",
                                    "searchText": "battery"}


def test_batch_with_a_short_answer_is_retried(wire):
    reviews, identity = load("reviews.json")
    sessions = wire([Response([reviews]), Response([reviews, identity])])
    assert len(fetch.fetch_reviews("6668952", 1, 20, "BEST_MATCH")) == 2
    assert len(sessions) == 2 and sessions[0].closed


def test_questions_page_is_written_into_the_query(wire):
    questions, identity = load("questions_page_2.json")
    sessions = wire([Response([questions, identity])])
    fetch.fetch_questions("6668952", 3, 500, "hdmi")
    body = json.loads(sessions[0].sent[0]["data"])[0]
    assert "pagination:{page:3 pageSize:$pageSize}" in body["query"]
    assert body["variables"] == {"skuId": "6668952", "searchText": "hdmi", "pageSize": 100}


def test_fulfillment_is_a_get_with_the_variables_in_the_link(wire):
    sessions = wire([Response(load("fulfillment.json"))])
    fetch.fetch_fulfillment("6668952", "10001", "482")
    sent = sessions[0].sent[0]
    assert sent["method"] == "GET" and sent["data"] is None
    assert sent["url"].startswith("https://www.bestbuy.com/gateway/graphql/fulfillment?variables=%7B%22")
    variables = json.loads(parse_qs(urlsplit(sent["url"]).query)["variables"][0])["fulfillmentOptionsInput"]
    assert variables["sku"] == "6668952" and variables["shipping"] == {"destinationZipCode": "10001"}
    assert variables["inStorePickup"] == {"searchNearby": True, "showNearbyLocations": True, "storeId": "482"}
    assert variables["buttonState"] == {"context": "PDP", "destinationZipCode": "10001", "storeId": "482"}
    assert "origin" not in sent["headers"] and sent["headers"]["x-client-id"] == "pdp-web"


def test_suggestions_request(wire):
    sessions = wire([Response(load("suggestions.json"))])
    fetch.fetch_suggestions("lap top", 50)
    sent = sessions[0].sent[0]
    assert sent["url"] == "https://www.bestbuy.com/suggest/v1/fragment/suggest/www?query=lap%20top&count=20&searchVariant=B"
    assert "origin" not in sent["headers"] and sent["headers"]["authorization"] == "undefined"
    assert sent["headers"]["x-request-id"].count("-") == 5


# ---- failures and the exit ladder ----------------------------------------------------

def test_a_reset_stream_moves_to_the_next_exit(wire):
    reset = RuntimeError("Failed to perform, curl: (92) HTTP/2 stream 1 reset by server (error 0x2 INTERNAL_ERROR)")
    sessions = wire([reset, Response(load("bsin.json"))])
    assert fetch.fetch_bsin("J3K4L6QW5C")["data"]["bsinProduct"]
    assert [s.exit for s in sessions] == ["gateway:gb", "gateway:de"] and sessions[0].closed
    assert fetch.current_exit() == "gateway:de"            # the first exit is set aside for the hold


def test_a_held_request_moves_on_and_every_exit_is_tried(wire):
    held = RuntimeError("Failed to perform, curl: (28) Operation timed out after 12001 milliseconds with 0 bytes received")
    sessions = wire([held, held, held])
    with pytest.raises(fetch.BestBuyBlocked) as e:
        fetch.fetch_bsin("J3K4L6QW5C")
    assert "held" in str(e.value)
    assert [s.exit for s in sessions] == ["gateway:gb", "gateway:de", "gateway:in"]


def test_a_session_on_an_exit_set_aside_by_another_thread_is_replaced(wire):
    sessions = wire([Response(load("bsin.json")), Response(load("bsin.json"))])
    fetch.fetch_bsin("J3K4L6QW5C")
    assert [s.exit for s in sessions] == ["gateway:gb"]
    fetch.hold_exit("gateway:gb")                   # another thread met the refusal
    fetch.fetch_bsin("J3K4L6QW5C")
    assert [s.exit for s in sessions] == ["gateway:gb", "gateway:de"] and sessions[0].closed


def test_hold_expires(monkeypatch):
    monkeypatch.setattr(config, "BEST_BUY_EXIT", "gateway:gb")
    monkeypatch.setattr(config, "BEST_BUY_FALLBACK_EXITS", ("gateway:de",))
    monkeypatch.setattr(config, "BEST_BUY_BLOCK_HOLD_S", 300)
    fetch._held.clear()
    assert fetch.exits() == ["gateway:gb", "gateway:de"] and fetch.current_exit(now=0) == "gateway:gb"
    fetch.hold_exit("gateway:gb", now=1000)
    assert fetch.current_exit(now=1100) == "gateway:de" and fetch.current_exit(now=1301) == "gateway:gb"
    fetch.hold_exit("gateway:de", now=1100)
    assert fetch.current_exit(now=1200) == "gateway:gb"    # both held: the one whose hold ends first
    monkeypatch.setattr(config, "BEST_BUY_FALLBACK_EXITS", ())
    assert fetch.exits() == ["gateway:gb"] and fetch.current_exit(now=1200) == "gateway:gb"
    fetch._held.clear()


@pytest.mark.parametrize("status, error", [(403, fetch.BestBuyBlocked), (429, fetch.BestBuyBlocked),
                                           (500, fetch.BestBuyUpstreamError), (413, fetch.BestBuyUpstreamError)])
def test_retryable_statuses(wire, status, error):
    sessions = wire([Response({}, status), Response({}, status), Response({}, status)])
    with pytest.raises(error):
        fetch.fetch_bsin("J3K4L6QW5C")
    assert len(sessions) == 3


def test_a_page_where_json_was_expected_is_a_block(wire):
    wire([Response(None, 200, "<html>Access Denied</html>")] * 3)
    with pytest.raises(fetch.BestBuyBlocked):
        fetch.fetch_bsin("J3K4L6QW5C")


def test_bad_request_is_not_retried_and_names_the_reason(wire):
    sessions = wire([Response({"errorCode": "400", "moreInfo": "Invalid suggest count Minimum suggest count is 1, and maximum is 20"}, 400)])
    with pytest.raises(fetch.BestBuyBadRequest) as e:
        fetch.fetch_suggestions("laptop", 10)
    assert "maximum is 20" in str(e.value) and len(sessions) == 1
    sessions = wire([Response({"errors": [{"message": "Error - Bad User Input"}]}, 400)])
    with pytest.raises(fetch.BestBuyBadRequest) as e:
        fetch.fetch_bsin("J3K4L6QW5C")
    assert "Bad User Input" in str(e.value)


def test_an_answer_without_data_is_retried(wire):
    sessions = wire([Response({"errors": [{"message": "x", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}], "data": None}),
                     Response(load("bsin.json"))])
    assert fetch.fetch_bsin("J3K4L6QW5C")["data"]["bsinProduct"]
    assert len(sessions) == 2


def test_listing_failure_and_unknown_category(wire):
    broken = {"data": {"search": {"numFound": None}},
              "errors": [{"path": ["search", "numFound"], "extensions": {"code": "INTERNAL_SERVER_ERROR"}}]}
    sessions = wire([Response(broken), Response(load("listing_search_no_results.json"))])
    assert fetch.fetch_listing(query="laptop")["data"]["search"]["numFound"] == 0
    assert len(sessions) == 2
    # a page past the end (every field NOT_FOUND) and an unknown category are answers, not failures
    sessions = wire([Response(load("listing_search_past_end.json"))])
    assert fetch.fetch_listing(query="laptop", page=100, limit=100)["data"]["search"]["documents"] is None
    assert len(sessions) == 1
    sessions = wire([Response(load("listing_category_unknown.json"))])
    assert not (fetch.fetch_listing(category="zzzz999")["data"].get("productCategory") or {}).get("id")
    assert len(sessions) == 1


# ---- reading an answer's errors ------------------------------------------------------

def test_missing_and_record():
    unknown = load("identity_unknown.json")
    assert fetch.missing(unknown, "productBySkuId") is True
    with pytest.raises(fetch.BestBuyNotFound):
        fetch.record(unknown, "productBySkuId", "product 1")
    known = load("identity.json")
    assert fetch.missing(known, "productBySkuId") is False
    assert fetch.record(known, "productBySkuId", "product 1")["skuId"] == "6668952"
    with pytest.raises(fetch.BestBuyUpstreamError):
        fetch.record({"data": {"productBySkuId": None}, "errors": []}, "productBySkuId", "product 1")
    assert fetch.missing(load("store_unknown.json"), "storeById") is True
    assert ["productBySkuId", "skuId"] in fetch.error_paths(unknown, "NOT_FOUND")
    assert fetch.error_code({"extensions": {"code": 401}}) == "401" and fetch.error_code({}) == ""
