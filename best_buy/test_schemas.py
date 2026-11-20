"""Offline tests for the /best-buy/* request schemas and the reference
resolvers.

    python -m pytest best_buy/test_schemas.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from best_buy import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

SLUG = "dell-15-6-2k-touchscreen-laptop-intel-13th-generation-core-i7-1355u-2023-16gb-memory-512-gb-storage-carbon-black"
BSIN_LINK = f"https://www.bestbuy.com/product/{SLUG}/J3K4L6QW5C"
SKU_LINK = BSIN_LINK + "/sku/6668952"
CATEGORY_LINK = "https://www.bestbuy.com/site/all-laptops/pc-laptops/pcmcat247400050000.c?id=pcmcat247400050000"
STORE_LINK = "https://stores.bestbuy.com/ny/new-york/60-w-23rd-st-482.html"


def ok(schema, query):
    data, error = load_query(schema, query)
    assert error is None, error
    return data


def bad(schema, query, field):
    data, error = load_query(schema, query)
    assert data is None and field in error["errors"], (data, error)
    return error["errors"][field][0]


# ---- refs --------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("6668952", "6668952"),
    (" 13300403 ", "13300403"),
    ("J3K4L6QW5C", "J3K4L6QW5C"),
    ("j3k4l6qw5c", "J3K4L6QW5C"),
    (SKU_LINK, "6668952"),
    (SKU_LINK + "?intl=nosplash#tabbed-customerreviews", "6668952"),
    (SKU_LINK + "/openbox?condition=excellent", "6668952"),
    (SKU_LINK + "/reviews", "6668952"),
    (BSIN_LINK, "J3K4L6QW5C"),
    (BSIN_LINK.replace("https://www.", ""), "J3K4L6QW5C"),
    (f"/product/{SLUG}/J3K4L6QW5C/sku/6668952", "6668952"),
    ("https://www.bestbuy.com/site/dell-laptop/6668952.p?skuId=6668952", "6668952"),
    ("https://www.bestbuy.com/site/dell-laptop/6668952.p", "6668952"),
    ("https://www.bestbuy.com/site/reviews/dell-laptop/6668952", "6668952"),
    ("https://www.bestbuy.com/site/questions/dell-laptop/6668952", "6668952"),
])
def test_resolve_product(value, expected):
    assert refs.resolve_product(value) == expected


@pytest.mark.parametrize("value", [
    "", "laptop", "1234", "12345678901", "J3K4L6QW5", "J3K4L6QW5C1", "66 68952", "6668952,6690109",
    "https://www.amazon.com/product/x/J3K4L6QW5C/sku/6668952", "https://www.bestbuy.com/", CATEGORY_LINK,
    "https://www.bestbuy.com/product/x/short", STORE_LINK.replace("stores.bestbuy.com", "www.bestbuy.com"),
])
def test_resolve_product_rejects(value):
    with pytest.raises(ValueError) as e:
        refs.resolve_product(value)
    assert "bestbuy.com" in str(e.value)


def test_is_bsin():
    assert refs.is_bsin("J3K4L6QW5C") and not refs.is_bsin("6668952")


def test_resolve_products():
    assert refs.resolve_products(f"6668952, J3K4L6QW5C ,{SKU_LINK},,") == ["6668952", "J3K4L6QW5C"]
    with pytest.raises(ValueError):
        refs.resolve_products(" , ")
    with pytest.raises(ValueError):
        refs.resolve_products("6668952,not a sku")
    skus = ",".join(str(6600000 + n) for n in range(refs.MAX_BULK_PRODUCTS))
    assert len(refs.resolve_products(skus)) == 20
    with pytest.raises(ValueError) as e:
        refs.resolve_products(skus + ",6699999")
    assert "At most 20" in str(e.value)


@pytest.mark.parametrize("value, expected", [
    ("abcat0500000", "abcat0500000"),
    ("PCMCAT247400050000", "pcmcat247400050000"),
    ("cat00000", "cat00000"),
    ("sc_digital_platform", "sc_digital_platform"),
    (CATEGORY_LINK, "pcmcat247400050000"),
    ("https://www.bestbuy.com/site/electronics/computers-pcs/abcat0500000.c", "abcat0500000"),
    ("/site/Electronics/Gift-Cards/cat09000.c?id=cat09000", "cat09000"),
    ("https://www.bestbuy.com/site/searchpage.jsp?id=pcat17071&st=laptop", "pcat17071"),
])
def test_resolve_category(value, expected):
    assert refs.resolve_category(value) == expected


@pytest.mark.parametrize("value", ["", "Not A Category!", "ab", "https://example.com/x.c?id=abcat0500000", SKU_LINK,
                                   "https://www.bestbuy.com/"])
def test_resolve_category_rejects(value):
    with pytest.raises(ValueError) as e:
        refs.resolve_category(value)
    assert "category" in str(e.value).lower() or "bestbuy.com" in str(e.value)


@pytest.mark.parametrize("value, expected", [
    ("482", "482"), ("0482", "482"), ("7", "7"), (STORE_LINK, "482"), ("https://stores.bestbuy.com/482", "482"),
    ("stores.bestbuy.com/mn/roseville/1643-county-road-b2-7.html", "7"),
])
def test_resolve_store(value, expected):
    assert refs.resolve_store(value) == expected


@pytest.mark.parametrize("value", ["", "new york", "1234567", "https://stores.bestbuy.com/ny/new-york.html",
                                   "https://stores.example.com/482"])
def test_resolve_store_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_store(value)


def test_resolve_seller_and_zip():
    assert refs.resolve_seller("6562") == 6562
    for value in ("bby", "0", "", "-5", "65 62"):
        with pytest.raises(ValueError):
            refs.resolve_seller(value)
    assert refs.resolve_zip("10001") == "10001" and refs.resolve_zip(" 10001-1234 ") == "10001"
    for value in ("1000", "100011", "abcde", ""):
        with pytest.raises(ValueError) as e:
            refs.resolve_zip(value)
        assert "5 digits" in str(e.value)


def test_last_listing_page():
    assert refs.last_listing_page(18) == 100
    assert refs.last_listing_page(24) == 100
    assert refs.last_listing_page(100) == 24
    assert refs.last_listing_page(50) == 48


def test_links():
    assert refs.absolute("/site/x.c?id=1") == "https://www.bestbuy.com/site/x.c?id=1"
    assert refs.absolute("//pisces.bbystatic.com/a.jpg") == "https://pisces.bbystatic.com/a.jpg"
    assert refs.absolute("https://www.bestbuy.com/a") == "https://www.bestbuy.com/a"
    assert refs.absolute("") is None and refs.absolute(None) is None
    assert refs.store_link("482") == "https://stores.bestbuy.com/482" and refs.store_link(None) is None


# ---- listings ----------------------------------------------------------------------

def test_search_defaults():
    data = ok(schemas.ProductsSearchSchema, {"query": "  oled   tv "})
    assert data == {"query": "oled tv", "category": None, "page": 1, "limit": 24, "sort": None, "brand": None,
                    "min_price": None, "max_price": None, "min_rating": None, "condition": None, "on_sale": None,
                    "sold_by_best_buy": None, "filter": None}


def test_search_every_param():
    data = ok(schemas.ProductsSearchSchema, {
        "query": "laptop", "category": CATEGORY_LINK, "page": "3", "limit": "100", "sort": "PRICE_LOW_TO_HIGH",
        "brand": "HP, Dell,HP", "min_price": "500", "max_price": "899.99", "min_rating": "Top_Rated",
        "condition": "open_box", "on_sale": "true", "sold_by_best_buy": "1",
        "filter": "colorcat_facet:Black, features_facet:Touch Screen"})
    assert data["category"] == "pcmcat247400050000" and data["page"] == 3 and data["limit"] == 100
    assert data["sort"] == "Price-Low-To-High" and data["brand"] == ["HP", "Dell"]
    assert data["min_price"] == 500.0 and data["max_price"] == 899.99
    assert data["min_rating"] == "Top-Rated" and data["condition"] == "Open-Box"
    assert data["on_sale"] is True and data["sold_by_best_buy"] is True
    assert data["filter"] == [("colorcat_facet", "Black"), ("features_facet", "Touch Screen")]


@pytest.mark.parametrize("name, token", list(refs.SORTS.items()))
def test_search_sorts(name, token):
    assert ok(schemas.ProductsSearchSchema, {"query": "tv", "sort": name})["sort"] == token


@pytest.mark.parametrize("name, token", list(refs.MIN_RATINGS.items()))
def test_search_min_ratings(name, token):
    assert ok(schemas.ProductsSearchSchema, {"query": "tv", "min_rating": name})["min_rating"] == token


@pytest.mark.parametrize("query, field, message", [
    ({}, "query", "Missing data"),
    ({"query": "tv", "sort": "cheapest"}, "sort", "Must be one of: best_match"),
    ({"query": "tv", "page": "101"}, "page", "less than or equal to 100"),
    ({"query": "tv", "page": "0"}, "page", "greater than or equal to 1"),
    ({"query": "tv", "limit": "101"}, "limit", "less than or equal to 100"),
    ({"query": "tv", "min_price": "-1"}, "min_price", "greater than or equal to 0"),
    ({"query": "tv", "min_price": "9", "max_price": "3"}, "min_price", "higher than max_price"),
    ({"query": "tv", "min_rating": "6"}, "min_rating", "Must be one of: 1, 2, 3, 4, 5, top_rated"),
    ({"query": "tv", "condition": "used"}, "condition", "Must be one of: new, open_box, refurbished"),
    ({"query": "tv", "on_sale": "maybe"}, "on_sale", "Must be true or false"),
    ({"query": "tv", "filter": "colorcat_facet"}, "filter", "must be facet:value"),
    ({"query": "tv", "filter": "brand_facet:HP"}, "filter", "use brand"),
    ({"query": "tv", "filter": "currentprice_facet:1 to 2"}, "filter", "use min_price / max_price"),
    ({"query": "tv", "category": "Not A Category!"}, "category", "category id"),
    ({"query": "tv", "brand": ",".join(f"b{i}" for i in range(11))}, "brand", "At most 10"),
    ({"query": "tv", "zz": "1"}, "zz", "Unknown field"),
])
def test_search_rejects(query, field, message):
    assert message in bad(schemas.ProductsSearchSchema, query, field)


def test_filter_limits():
    pairs = ",".join(f"f{i}_facet:v" for i in range(11))
    assert "At most 10" in bad(schemas.ProductsSearchSchema, {"query": "tv", "filter": pairs}, "filter")
    assert ok(schemas.ProductsSearchSchema, {"query": "tv", "filter": " , "})["filter"] is None
    assert ok(schemas.ProductsSearchSchema, {"query": "tv", "filter": "a_facet:1,a_facet:1"})["filter"] == [("a_facet", "1")]


def test_categories_products():
    data = ok(schemas.CategoriesProductsSchema, {"category": CATEGORY_LINK, "query": "gaming", "limit": "5"})
    assert data["category"] == "pcmcat247400050000" and data["query"] == "gaming" and data["limit"] == 5
    assert ok(schemas.CategoriesProductsSchema, {"category": "abcat0500000"})["query"] is None
    assert "Missing data" in bad(schemas.CategoriesProductsSchema, {}, "category")
    assert "bestbuy.com category link" in bad(schemas.CategoriesProductsSchema, {"category": SKU_LINK}, "category")


def test_deals():
    data = ok(schemas.DealsListSchema, {})
    assert data["type"] == "On Sale" and data["category"] is None and data["page"] == 1 and data["limit"] == 24
    assert "on_sale" not in data
    assert ok(schemas.DealsListSchema, {"type": "clearance"})["type"] == "Clearance"
    assert ok(schemas.DealsListSchema, {"type": "member_deals"})["type"] == "Plus & Total Member Deals"
    assert ok(schemas.DealsListSchema, {"type": ""})["type"] == "On Sale"
    assert "Must be one of: on_sale, clearance, member_deals" in bad(schemas.DealsListSchema, {"type": "cheap"}, "type")
    assert "Unknown field" in bad(schemas.DealsListSchema, {"on_sale": "true"}, "on_sale")


# ---- products ----------------------------------------------------------------------

@pytest.mark.parametrize("schema", [schemas.ProductsDetailsSchema, schemas.ProductsOffersSchema,
                                    schemas.ProductsAvailabilitySchema, schemas.ProductsReviewsSchema,
                                    schemas.ProductsQuestionsSchema, schemas.ProductsSimilarSchema])
def test_product_param(schema):
    assert ok(schema, {"product": SKU_LINK})["product"] == "6668952"
    assert ok(schema, {"product": "j3k4l6qw5c"})["product"] == "J3K4L6QW5C"
    assert "Missing data" in bad(schema, {}, "product")
    assert "Must be a Best Buy SKU" in bad(schema, {"product": "not a product"}, "product")
    assert "Unknown field" in bad(schema, {"product": "6668952", "url": SKU_LINK}, "url")


def test_bulk():
    assert ok(schemas.ProductsBulkSchema, {"products": f"6668952,{BSIN_LINK}"}) == {"products": ["6668952", "J3K4L6QW5C"]}
    assert "Missing data" in bad(schemas.ProductsBulkSchema, {}, "products")
    many = ",".join(str(6600000 + n) for n in range(21))
    assert "At most 20 products" in bad(schemas.ProductsBulkSchema, {"products": many}, "products")


def test_availability():
    assert ok(schemas.ProductsAvailabilitySchema, {"product": "6668952"}) == {"product": "6668952", "zip": None, "store": None}
    data = ok(schemas.ProductsAvailabilitySchema, {"product": "6668952", "zip": "10001-1234", "store": STORE_LINK})
    assert data == {"product": "6668952", "zip": "10001", "store": "482"}
    assert "5 digits" in bad(schemas.ProductsAvailabilitySchema, {"product": "6668952", "zip": "1000"}, "zip")
    assert "store number" in bad(schemas.ProductsAvailabilitySchema, {"product": "6668952", "store": "chelsea"}, "store")


def test_reviews():
    assert ok(schemas.ProductsReviewsSchema, {"product": "6668952"}) == {
        "product": "6668952", "page": 1, "limit": 20, "sort": "BEST_MATCH", "rating": None, "query": None}
    data = ok(schemas.ProductsReviewsSchema, {"product": "6668952", "page": "2", "limit": "100", "sort": "lowest_rated",
                                              "rating": "2, 1,2", "query": " battery  life "})
    assert data == {"product": "6668952", "page": 2, "limit": 100, "sort": "LOWEST_RATED", "rating": [1, 2],
                    "query": "battery life"}
    assert ok(schemas.ProductsReviewsSchema, {"product": "6668952", "sort": ""})["sort"] == "BEST_MATCH"
    for name, token in refs.REVIEW_SORTS.items():
        assert ok(schemas.ProductsReviewsSchema, {"product": "6668952", "sort": name})["sort"] == token
    assert "Must be one of: best_match, newest" in bad(schemas.ProductsReviewsSchema, {"product": "1234567", "sort": "best"}, "sort")
    assert "less than or equal to 100" in bad(schemas.ProductsReviewsSchema, {"product": "1234567", "limit": "101"}, "limit")
    assert "is not one of" in bad(schemas.ProductsReviewsSchema, {"product": "1234567", "rating": "6"}, "rating")
    assert "Longer than maximum length" in bad(schemas.ProductsReviewsSchema, {"product": "1234567", "query": "x" * 101}, "query")


def test_questions():
    assert ok(schemas.ProductsQuestionsSchema, {"product": "6668952"}) == {
        "product": "6668952", "page": 1, "limit": 10, "query": None}
    assert ok(schemas.ProductsQuestionsSchema, {"product": "6668952", "limit": "100", "page": "3", "query": "hdmi"})["limit"] == 100
    assert "less than or equal to 100" in bad(schemas.ProductsQuestionsSchema, {"product": "6668952", "limit": "101"}, "limit")


def test_suggestions_and_similar():
    assert ok(schemas.ProductsSuggestionsSchema, {"query": "lap"}) == {"query": "lap", "limit": 10}
    assert ok(schemas.ProductsSuggestionsSchema, {"query": "lap", "limit": "20"})["limit"] == 20
    assert "less than or equal to 20" in bad(schemas.ProductsSuggestionsSchema, {"query": "lap", "limit": "21"}, "limit")
    assert "Missing data" in bad(schemas.ProductsSuggestionsSchema, {}, "query")
    assert ok(schemas.ProductsSimilarSchema, {"product": "6690109"}) == {"product": "6690109", "limit": 10}
    assert "less than or equal to 25" in bad(schemas.ProductsSimilarSchema, {"product": "6690109", "limit": "26"}, "limit")
    assert "greater than or equal to 1" in bad(schemas.ProductsSimilarSchema, {"product": "6690109", "limit": "0"}, "limit")


# ---- categories, stores, sellers ---------------------------------------------------

def test_categories_list():
    assert ok(schemas.CategoriesListSchema, {}) == {"category": None}
    assert ok(schemas.CategoriesListSchema, {"category": CATEGORY_LINK}) == {"category": "pcmcat247400050000"}
    assert "category id" in bad(schemas.CategoriesListSchema, {"category": "Not A Category!"}, "category")


def test_stores():
    assert ok(schemas.StoresSearchSchema, {"zip": "10001"}) == {"zip": "10001", "radius": 50}
    assert ok(schemas.StoresSearchSchema, {"zip": "55423", "radius": "250"})["radius"] == 250
    assert "Missing data" in bad(schemas.StoresSearchSchema, {}, "zip")
    assert "5 digits" in bad(schemas.StoresSearchSchema, {"zip": "abc"}, "zip")
    assert "less than or equal to 250" in bad(schemas.StoresSearchSchema, {"zip": "10001", "radius": "251"}, "radius")
    assert ok(schemas.StoresDetailsSchema, {"store": STORE_LINK}) == {"store": "482"}
    assert "store number" in bad(schemas.StoresDetailsSchema, {"store": "new york"}, "store")
    assert "Missing data" in bad(schemas.StoresDetailsSchema, {}, "store")


def test_sellers():
    assert ok(schemas.SellersDetailsSchema, {"seller": "6562"}) == {"seller": 6562}
    assert "marketplace seller id" in bad(schemas.SellersDetailsSchema, {"seller": "bby"}, "seller")
    assert "Missing data" in bad(schemas.SellersDetailsSchema, {}, "seller")


def test_every_field_is_described():
    for name in dir(schemas):
        schema = getattr(schemas, name)
        if isinstance(schema, type) and issubclass(schema, schemas.BaseSchema) and name.endswith("Schema") and not name.startswith("_") and schema is not schemas.BaseSchema:
            for field_name, field in schema().fields.items():
                assert field.metadata.get("description"), f"{name}.{field_name} has no description"
