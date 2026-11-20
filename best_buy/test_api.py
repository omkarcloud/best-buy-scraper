"""Offline tests for the endpoint logic (best_buy/api.py), with the fetch
layer and the cache replaced by fakes: the answers are the fixtures.

    python -m pytest best_buy/test_api.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from best_buy import api, fetch, refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402
from scraper_errors import NotFound, UpstreamError  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DELL, ZENBOOK, UNKNOWN = "6668952", "13300403", "9999999999"


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def params(schema, **query):
    data, error = load_query(schema, {k: str(v) for k, v in query.items()})
    assert error is None, error
    return data


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """cached_call runs the function every time and records the namespaces."""
    calls = []

    def cached_call(namespace, data, fn, ttl, **kw):
        calls.append((namespace, dict(data)))
        return fn(**data)
    monkeypatch.setattr(api, "cached_call", cached_call)
    return calls


@pytest.fixture
def listing(monkeypatch):
    """fetch_listing answers the fixture named for each call, in order, and
    records the calls."""
    sent = []

    def install(*names):
        queue = list(names)

        def fetch_listing(**kw):
            sent.append(kw)
            return {"errors": [], **load(queue.pop(0))}
        monkeypatch.setattr(fetch, "fetch_listing", fetch_listing)
        return sent
    return install


# ---- listings ------------------------------------------------------------------------

def test_search(listing):
    sent = listing("listing_search_laptop.json")
    answer = api.products_search(**params(schemas.ProductsSearchSchema, query="laptop"))
    assert sent == [{"query": "laptop", "category": None, "page": 1, "limit": 24, "sort": None, "facets": [], "within": None}]
    assert list(answer)[:7] == ["query", "corrected_query", "category", "detected_category", "redirected_to_category",
                                "redirect_link", "related_searches"]
    assert answer["query"] == "laptop" and answer["detected_category"] == {"id": "abcat0502000", "name": "Laptops"}
    assert answer["sort"] == "best_match" and len(answer["products"]) == 24 and answer["facets"]
    assert answer["filters"] == {"brand": [], "min_price": None, "max_price": None, "min_rating": None, "condition": None,
                                 "is_on_sale": False, "is_sold_by_best_buy": False, "filter": []}
    assert answer["pagination"]["total_pages"] == 100


def test_search_filters_become_facets(listing):
    sent = listing("listing_search_filtered.json")
    answer = api.products_search(**params(
        schemas.ProductsSearchSchema, query="laptop", brand="HP,Dell", min_price=500, max_price=900, min_rating=4,
        condition="new", sort="price_low_to_high", limit=10))
    assert sent[0]["facets"] == [("brand_facet", "HP"), ("brand_facet", "Dell"), ("currentprice_facet", "500 to 900"),
                                 ("customerreviews_facet", "4 & Up"), ("condition_facet", "New")]
    assert sent[0]["sort"] == "Price-Low-To-High"
    assert answer["filters"] == {"brand": ["HP", "Dell"], "min_price": 500, "max_price": 900, "min_rating": 4,
                                 "condition": "new", "is_on_sale": False, "is_sold_by_best_buy": False, "filter": []}
    assert answer["sort"] == "price_low_to_high"


def test_open_price_bounds(listing):
    sent = listing("listing_search_no_results.json", "listing_search_no_results.json", "listing_search_no_results.json")
    api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", min_price=2000))
    api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", max_price=199.99))
    api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", min_price=1, max_price=2))
    assert [call["facets"] for call in sent] == [[("currentprice_facet", "2000 to 999999")],
                                                 [("currentprice_facet", "0 to 199.99")],
                                                 [("currentprice_facet", "1 to 2")]]


def test_search_inside_a_category_and_flags(listing):
    sent = listing("listing_search_no_results.json")
    data = params(schemas.ProductsSearchSchema, query="laptop", category="abcat0502000", on_sale="true",
                  sold_by_best_buy="true", min_price=1, max_price=2)
    data["filter"] = None
    with pytest.raises(ValueError):       # the fixture did not apply these facets: the caller is told
        api.products_search(**data)
    assert sent[0]["facets"] == [("category_facet", "abcat0502000"), ("currentprice_facet", "1 to 2"),
                                 ("currentoffers_facet", "On Sale"), ("bbyonly_facet", "Best Buy")]


def test_a_facet_the_site_ignored_is_a_400(listing):
    listing("listing_search_unknown_facet.json")
    with pytest.raises(ValueError) as e:
        api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", filter="zz_facet:zz"))
    assert "no facet named 'zz_facet'" in str(e.value)


def test_no_results_with_a_filter_is_an_empty_page(listing):
    listing("listing_search_no_results.json")
    answer = api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", min_price=1, max_price=2))
    assert answer["products"] == [] and answer["pagination"] == {"page": 1, "items_per_page": 24, "total_pages": 0, "total_count": 0}


def test_page_past_the_end_is_an_empty_page(listing):
    listing("listing_search_past_end.json")
    answer = api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", page=24, limit=100, brand="HP"))
    assert answer["products"] == [] and answer["pagination"]["total_count"] == 0


def test_page_beyond_what_the_site_serves_is_a_400(listing):
    listing()
    with pytest.raises(ValueError) as e:
        api.products_search(**params(schemas.ProductsSearchSchema, query="laptop", page=25, limit=100))
    assert "the last page is 24" in str(e.value)


def test_search_redirect_is_followed_to_the_category(listing):
    sent = listing("listing_search_redirect.json", "listing_category.json")
    answer = api.products_search(**params(schemas.ProductsSearchSchema, query="gift cards", limit=24))
    assert [call.get("category") for call in sent] == [None, "cat09000"]
    assert sent[1]["query"] is None and sent[1]["sort"] == "Best-Selling" and sent[1]["within"] is None
    assert answer["query"] == "gift cards" and answer["redirected_to_category"] == "cat09000"
    assert answer["redirect_link"] is None and len(answer["products"]) == 24


def test_typo(listing):
    listing("listing_search_typo.json")
    answer = api.products_search(**params(schemas.ProductsSearchSchema, query="lapptop", limit=5))
    assert answer["query"] == "lapptop" and answer["corrected_query"] == "laptop" and len(answer["products"]) == 5


def test_category_products(listing):
    sent = listing("listing_category.json")
    answer = api.categories_products(**params(schemas.CategoriesProductsSchema, category="pcmcat247400050000", query="gaming"))
    assert sent[0]["category"] == "pcmcat247400050000" and sent[0]["query"] is None and sent[0]["within"] == "gaming"
    assert sent[0]["sort"] == "Best-Selling"          # the site's own default for a category page
    assert list(answer)[:2] == ["category", "query"] and answer["query"] == "gaming"
    assert answer["category"]["name"] == "Windows Laptops" and answer["category"]["subcategories"]
    assert len(answer["products"]) == 24


def test_category_products_unknown(listing):
    listing("listing_category_unknown.json")
    with pytest.raises(NotFound) as e:
        api.categories_products(**params(schemas.CategoriesProductsSchema, category="zzzz999"))
    assert str(e.value) == "category zzzz999 not found"


def test_deals(listing):
    sent = listing("listing_deals.json", "listing_deals.json")
    answer = api.deals_list(**params(schemas.DealsListSchema))
    assert sent[0]["category"] == refs.ROOT_CATEGORY and sent[0]["facets"] == [("currentoffers_facet", "On Sale")]
    assert answer["type"] == "on_sale" and answer["category"] is None and len(answer["products"]) == 24
    assert answer["filters"]["filter"] == []          # the deal type is the route's own facet, not a caller's filter
    answer = api.deals_list(**params(schemas.DealsListSchema, category="cat00000", type="on_sale", sort="best_discount"))
    assert sent[1]["sort"] == "Best-Discount" and sent[1]["category"] == "cat00000"
    assert answer["category"]["id"] == "cat00000" and "subcategories" not in answer["category"]


# ---- products ------------------------------------------------------------------------

def test_details(monkeypatch, no_cache):
    asked = []
    monkeypatch.setattr(fetch, "fetch_products", lambda skus: asked.append(skus) or load("products_two.json")[:1])
    product = api.products_details(product=DELL)
    assert asked == [[DELL]] and product["sku"] == DELL and product["price"]["current"] == 599.99
    assert no_cache == [("best_buy.product", {"sku": DELL})]


def test_details_by_bsin_resolves_through_the_lookup(monkeypatch, no_cache):
    monkeypatch.setattr(fetch, "fetch_bsin", lambda bsin: load("bsin.json"))
    monkeypatch.setattr(fetch, "fetch_products", lambda skus: load("products_two.json")[:1])
    assert api.products_details(product="J3K4L6QW5C")["sku"] == DELL
    assert [name for name, _ in no_cache] == ["best_buy.bsin", "best_buy.product"]
    monkeypatch.setattr(fetch, "fetch_bsin", lambda bsin: load("bsin_unknown.json"))
    with pytest.raises(NotFound) as e:
        api.products_details(product="ZZZZZZZZZ9")
    assert str(e.value) == "product ZZZZZZZZZ9 not found"


def test_details_unknown_and_empty(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_products", lambda skus: load("products_unknown.json"))
    with pytest.raises(NotFound) as e:
        api.products_details(product=UNKNOWN)
    assert str(e.value) == f"product {UNKNOWN} not found"
    monkeypatch.setattr(fetch, "fetch_products", lambda skus: [{"PRODUCT": {"data": {"productBySkuId": None}, "errors": []}}])
    with pytest.raises(UpstreamError):
        api.products_details(product=DELL)


def test_bulk(monkeypatch):
    two, unknown = load("products_two.json"), load("products_unknown.json")[0]
    by_sku = {DELL: two[0], ZENBOOK: two[1]}
    asked = []

    def fetch_products(skus):
        asked.append(list(skus))
        return [by_sku.get(sku, unknown) for sku in skus]
    monkeypatch.setattr(fetch, "fetch_products", fetch_products)
    monkeypatch.setattr(fetch, "fetch_bsin", lambda bsin: load("bsin.json") if bsin == "J3K4L6QW5C" else load("bsin_unknown.json"))
    answer = api.products_bulk(products=[DELL, ZENBOOK, UNKNOWN, "J3K4L6QW5C", "ZZZZZZZZZ9", "1111111", "2222222"])
    assert sorted(len(chunk) for chunk in asked) == [1, 4] and sum(asked, []).count(DELL) == 1
    assert answer["requested_count"] == 7 and answer["found_count"] == 2
    assert [p["sku"] for p in answer["products"]] == [DELL, ZENBOOK]
    assert answer["not_found"] == [UNKNOWN, "ZZZZZZZZZ9", "1111111", "2222222"]


def test_offers(monkeypatch):
    new, excellent, good, fair = load("prices_open_box.json")
    asked = []

    def fetch_offer_prices(pairs):
        asked.extend(pairs)
        return [{None: new, 2: excellent, 1: good, 0: fair}[code] for _, code in pairs]
    monkeypatch.setattr(fetch, "fetch_offer_identity", lambda sku: load("offers_identity.json"))
    monkeypatch.setattr(fetch, "fetch_offer_prices", fetch_offer_prices)
    answer = api.products_offers(product=DELL)
    assert asked[:4] == [(DELL, None), (DELL, 2), (DELL, 1), (DELL, 0)] and len(asked) == 18
    assert asked[4] == ("12575317", None)
    assert answer["sku"] == DELL and answer["bsin"] == "J3K4L6QW5C" and answer["currency"] == "USD"
    assert answer["offers_count"] == len(answer["offers"]) == 17         # the "good" copy is not on sale
    prices = [o["price"] for o in answer["offers"]]
    assert prices == sorted(prices) and answer["lowest_price"] == 625.99 and answer["highest_price"] == 749.99
    assert {o["condition"] for o in answer["offers"]} == {"new", "open_box_excellent", "open_box_fair", "refurbished_excellent"}
    sellers = {o["seller"]["name"] for o in answer["offers"]}
    assert "Best Buy" in sellers and "RYTEK" in sellers


def test_offers_unknown(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_offer_identity", lambda sku: load("offers_identity_unknown.json"))
    with pytest.raises(NotFound):
        api.products_offers(product=UNKNOWN)
    # a product the site failed to answer (no NOT_FOUND on it) is an upstream failure, not a 404
    failed = {"data": {"productBySkuId": None},
              "errors": [{"path": ["productBySkuId"], "extensions": {"code": "INTERNAL_SERVER_ERROR"}}]}
    monkeypatch.setattr(fetch, "fetch_offer_identity", lambda sku: failed)
    with pytest.raises(UpstreamError):
        api.products_offers(product=DELL)
    monkeypatch.setattr(fetch, "fetch_identity", lambda sku: failed)
    with pytest.raises(UpstreamError):
        api.products_availability(product=DELL, zip="10001", store=None)


def test_availability(monkeypatch, no_cache):
    asked = []
    monkeypatch.setattr(config, "BEST_BUY_DEFAULT_ZIP", "10001")
    monkeypatch.setattr(fetch, "fetch_identity", lambda sku: load("identity.json"))
    monkeypatch.setattr(fetch, "fetch_stores", lambda zip_code, radius: asked.append(("stores", zip_code, radius)) or load("stores_10001.json"))
    monkeypatch.setattr(fetch, "fetch_fulfillment", lambda sku, zip_code, store: asked.append(("fulfillment", sku, zip_code, store)) or load("fulfillment.json"))
    answer = api.products_availability(**params(schemas.ProductsAvailabilitySchema, product=DELL))
    assert asked == [("stores", "10001", 250), ("fulfillment", DELL, "10001", "482")]
    assert list(answer)[:4] == ["sku", "name", "link", "zip"] and answer["name"].startswith("Dell - ")
    assert answer["zip"] == "10001" and answer["pickup"]["store"]["id"] == "482" and len(answer["nearby_stores"]) == 27
    assert [name for name, _ in no_cache] == ["best_buy.availability", "best_buy.identity", "best_buy.nearest_store"]
    del asked[:]
    api.products_availability(**params(schemas.ProductsAvailabilitySchema, product=DELL, zip="55423", store="7"))
    assert asked == [("fulfillment", DELL, "55423", "7")]


def test_availability_unknown_sku_and_zip(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_identity", lambda sku: load("identity_unknown.json"))
    with pytest.raises(NotFound) as e:      # the fulfillment call itself answers "not available" for any number
        api.products_availability(product=UNKNOWN, zip="10001", store=None)
    assert str(e.value) == f"product {UNKNOWN} not found"
    monkeypatch.setattr(fetch, "fetch_identity", lambda sku: load("identity.json"))
    monkeypatch.setattr(fetch, "fetch_stores", lambda zip_code, radius: load("stores_unknown_zip.json"))
    with pytest.raises(ValueError) as e:
        api.products_availability(product=DELL, zip="00000", store=None)
    assert "zip: the site does not know the ZIP code 00000" in str(e.value)


def test_reviews(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a: tuple(load("reviews.json")))
    answer = api.products_reviews(**params(schemas.ProductsReviewsSchema, product=DELL))
    assert list(answer)[:5] == ["sku", "name", "link", "rating", "reviews_count"]
    assert answer["rating"] == 4.7 and answer["reviews_count"] == 434 and answer["sort"] == "best_match"
    assert answer["rating_filter"] == [] and answer["query"] is None and len(answer["reviews"]) == 20
    assert answer["pagination"] == {"page": 1, "items_per_page": 20, "total_count": 434, "total_pages": 22}
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a: tuple(load("reviews_rating_1_2.json")))
    answer = api.products_reviews(**params(schemas.ProductsReviewsSchema, product=DELL, rating="1,2", sort="newest"))
    assert answer["rating_filter"] == [1, 2] and answer["sort"] == "newest" and answer["reviews_count"] == 434
    assert answer["pagination"]["total_count"] == 13 and answer["pagination"]["total_pages"] == 1


def test_reviews_none_past_end_unknown(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a: tuple(load("reviews_none.json")))
    answer = api.products_reviews(**params(schemas.ProductsReviewsSchema, product=ZENBOOK))
    assert answer["reviews"] == [] and answer["reviews_count"] == 0 and answer["rating"] is None
    assert answer["pagination"] == {"page": 1, "items_per_page": 20, "total_count": 0, "total_pages": 0}
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a: tuple(load("reviews_past_end.json")))
    answer = api.products_reviews(**params(schemas.ProductsReviewsSchema, product=DELL, page=999))
    assert answer["reviews"] == [] and answer["pagination"]["total_count"] == 434
    monkeypatch.setattr(fetch, "fetch_reviews", lambda *a: tuple(load("reviews_unknown.json")))
    with pytest.raises(NotFound):
        api.products_reviews(**params(schemas.ProductsReviewsSchema, product=UNKNOWN))


def test_questions(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_questions", lambda *a: tuple(load("questions.json")))
    answer = api.products_questions(**params(schemas.ProductsQuestionsSchema, product=DELL))
    assert answer["sku"] == DELL and answer["counts"]["questions"] == 137 and len(answer["questions"]) == 10
    assert answer["pagination"] == {"page": 1, "items_per_page": 10, "total_count": 137, "total_pages": 14}
    answer = api.products_questions(**params(schemas.ProductsQuestionsSchema, product=DELL, query="battery"))
    assert answer["pagination"] == {"page": 1, "items_per_page": 10, "total_count": None, "total_pages": None, "has_next": True}
    monkeypatch.setattr(fetch, "fetch_questions", lambda *a: tuple(load("questions_past_end.json")))
    answer = api.products_questions(**params(schemas.ProductsQuestionsSchema, product=DELL, query="battery", page=9))
    assert answer["questions"] == [] and answer["pagination"]["has_next"] is False
    monkeypatch.setattr(fetch, "fetch_questions", lambda *a: tuple(load("questions_unknown.json")))
    with pytest.raises(NotFound):
        api.products_questions(**params(schemas.ProductsQuestionsSchema, product=UNKNOWN))


def test_suggestions_and_similar(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_suggestions", lambda term, limit: load("suggestions.json"))
    answer = api.products_suggestions(query="laptop", limit=3)
    assert answer["query"] == "laptop" and answer["count"] == 3 and len(answer["suggestions"]) == 3
    monkeypatch.setattr(fetch, "fetch_suggestions", lambda term, limit: load("suggestions_none.json"))
    assert api.products_suggestions(query="zzqxjkvw", limit=10) == {
        "query": "zzqxjkvw", "corrected_query": None, "count": 0, "suggestions": []}
    monkeypatch.setattr(fetch, "fetch_similar", lambda sku, limit: load("similar.json"))
    answer = api.products_similar(product="6690109", limit=4)
    assert answer["product"]["sku"] == "6690109" and answer["count"] == 4 and len(answer["products"]) == 4
    monkeypatch.setattr(fetch, "fetch_similar", lambda sku, limit: load("similar_unknown.json"))
    with pytest.raises(NotFound):
        api.products_similar(product=UNKNOWN, limit=4)


# ---- categories, stores, sellers -----------------------------------------------------

def test_categories_list(monkeypatch):
    asked = []
    monkeypatch.setattr(fetch, "fetch_category", lambda category: asked.append(category) or load("category_root.json"))
    assert api.categories_list(category=None)["id"] == "cat00000" and asked == ["cat00000"]
    monkeypatch.setattr(fetch, "fetch_category", lambda category: load("category_unknown.json"))
    with pytest.raises(NotFound) as e:
        api.categories_list(category="zzzz999")
    assert str(e.value) == "category zzzz999 not found"


def test_stores(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_stores", lambda zip_code, radius: load("stores_10001.json"))
    answer = api.stores_search(zip="10001", radius=50)
    assert answer["zip"] == "10001" and answer["radius_miles"] == 50 and answer["count"] == 25 == len(answer["stores"])
    monkeypatch.setattr(fetch, "fetch_stores", lambda zip_code, radius: load("stores_unknown_zip.json"))
    with pytest.raises(ValueError):
        api.stores_search(zip="00000", radius=50)
    monkeypatch.setattr(fetch, "fetch_store", lambda store: load("store_482.json"))
    assert api.stores_details(store="482")["name"] == "Chelsea (23rd and 6th)"
    monkeypatch.setattr(fetch, "fetch_store", lambda store: load("store_unknown.json"))
    with pytest.raises(NotFound):
        api.stores_details(store="999999")


def test_sellers(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_seller", lambda seller: load("seller_6562.json"))
    assert api.sellers_details(seller=6562)["name"] == "Velztorm"
    monkeypatch.setattr(fetch, "fetch_seller", lambda seller: load("seller_unknown.json"))
    with pytest.raises(NotFound) as e:
        api.sellers_details(seller=99999999)
    assert str(e.value) == "seller 99999999 not found"
