"""Offline tests for best_buy/parsers.py against the raw captures in
fixtures/ (taken 2026-11-19 through fetch.py's own calls, untrimmed).

    python -m pytest best_buy/test_parsers.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from best_buy import parsers  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DELL = "6668952"            # sold by Best Buy, 434 reviews
ZENBOOK = "13300403"        # a marketplace item without reviews


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def keys_are_clean(value, path="$"):
    """No camelCase key, no `url`, no '' / 'N/A' / 'None' value anywhere."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert key == key.lower() and key != "url" and not key.startswith("__"), f"{path}.{key}"
            keys_are_clean(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            keys_are_clean(item, f"{path}[{i}]")
    elif isinstance(value, str):
        assert value.strip() and value not in ("N/A", "None", "null"), path


# ---- small helpers -------------------------------------------------------------------

def test_local_time_utc():
    assert parsers.local_time_utc("2026-11-19T16:00 CST") == "2026-11-19T22:00:00Z"
    assert parsers.local_time_utc("2026-07-01T19:30 EDT") == "2026-07-01T23:30:00Z"
    assert parsers.local_time_utc("2026-11-19T16:00 XYZ") is None
    assert parsers.local_time_utc("2026-11-19") is None and parsers.local_time_utc(None) is None


def test_measurement():
    assert parsers.measurement("0.75 inches") == {"value": 0.75, "unit": "inches"}
    assert parsers.measurement("4 pounds") == {"value": 4, "unit": "pounds"}
    assert parsers.measurement("1,200.5 Pounds") == {"value": 1200.5, "unit": "pounds"}
    assert parsers.measurement("tall") is None and parsers.measurement(None) is None and parsers.measurement("") is None


def test_open_box_names():
    assert [parsers.open_box_name(c) for c in (2, "1", 0, None, 7, "x")] == ["excellent", "good", "fair", None, None, None]
    options = [{"type": "New"}, {"type": "Fair"}, {"type": "Excellent"}, None, {}]
    assert parsers.open_box_codes(options) == [2, 0]
    assert parsers.open_box_codes(None) == []


def test_parse_price_tolerates_nothing():
    assert parsers.parse_price(None) is None and parsers.parse_price({}) is None
    price = parsers.parse_price({"customerPrice": 10})
    assert price["current"] == 10 and price["regular"] == 10 and price["is_on_sale"] is False
    assert price["financing"] is None and price["installments"] == [] and price["gift_skus"] == []


# ---- listings ------------------------------------------------------------------------

def test_search_page():
    listing = parsers.parse_listing(load("listing_search_laptop.json"), 1, 24)
    assert listing["pagination"] == {"page": 1, "items_per_page": 24, "total_pages": 100, "total_count": 6707}
    assert len(listing["products"]) == 24
    assert listing["corrected_query"] is None and listing["redirect_link"] is None
    assert listing["sort"] == "best_match"
    assert listing["detected_category"] == {"id": "abcat0502000", "name": "Laptops"}
    assert listing["applied_filters"] == [{"facet": "category_facet", "values": ["abcat0502000"], "is_automatic": True}]
    assert listing["sort_options"] == ["best_match", "best_selling", "best_discount", "price_low_to_high",
                                       "price_high_to_low", "customer_rating"]
    assert listing["related_searches"][:2] == ["macbook", "ipad"]
    assert {"facet": "condition_facet", "value": "Open-Box"} in listing["quick_filters"]
    dell = next(p for p in listing["products"] if p["sku"] == DELL)
    assert dell["bsin"] == "J3K4L6QW5C" and dell["brand"] == "Dell" and dell["model_number"] == "LDC15250-7427BLK-PUS"
    assert dell["name"].startswith('Dell - 15.6" 2K Touchscreen Laptop') and dell["title"].startswith('15.6" 2K Touchscreen')
    assert dell["link"].endswith("/J3K4L6QW5C/sku/6668952") and dell["image"].startswith("https://pisces.bbystatic.com/")
    assert dell["condition"] == "new" and dell["open_box_condition"] is None and dell["product_types"] == ["Laptop Computer"]
    assert dell["price"]["current"] == 599.99 and dell["price"]["regular"] == 999.99 and dell["price"]["currency"] == "USD"
    assert dell["price"]["savings"] == 400 and dell["price"]["savings_percent"] == 40 and dell["price"]["is_on_sale"] is True
    assert dell["price"]["deal_label"] == "Black Friday Deal"
    assert dell["price"]["financing"] == {"months": 18, "monthly_payment": 33.34, "plan_type": "Deferred", "rate": 0,
                                          "total_cost": 599.99}
    assert dell["rating"] == 4.7 and dell["reviews_count"] == 434
    assert dell["seller"] == {"type": "best_buy", "id": None, "name": "Best Buy", "rating": None, "reviews_count": None}
    assert [o["condition"] for o in dell["buying_options"]] == ["new", "open_box_excellent", "open_box_good", "open_box_fair"]
    assert dell["buying_options"][1]["link"].endswith("/openbox?condition=excellent")
    assert {"sku": "12575317", "condition": "new", "seller_type": "marketplace"} in dell["other_skus"]
    assert dell["badges"][0]["name"] == "Core essentials PC" and dell["badges"][0]["ends_at"] == "2028-09-17T04:45:00Z"
    assert dell["status"] == "active" and dell["is_new"] is False and dell["is_recalled"] is False
    for product in listing["products"]:
        assert product["sku"] and product["name"] and product["link"] and product["price"]["current"] > 0
    keys_are_clean(listing)


def test_search_facets():
    facets = parsers.parse_listing(load("listing_search_laptop.json"), 1, 24)["facets"]
    by_name = {f["facet"]: f for f in facets}
    assert by_name["brand_facet"]["name"] == "Brand"
    assert {"value": "HP", "count": by_name["brand_facet"]["values"][0]["count"], "category_id": None, "children": []} \
        == by_name["brand_facet"]["values"][0]
    assert [v["value"] for v in by_name["condition_facet"]["values"]] == ["New", "Open-Box", "Refurbished"]
    category = by_name["category_facet"]["values"][0]
    assert category["value"] == "All Laptops" and category["category_id"] == "pcmcat138500050001"
    assert {"value": "Windows Laptops", "category_id": "pcmcat247400050000"} in category["children"]
    assert by_name["processormodelsv_facet"]["parent_facet"] == "parent_processormodelsv_facet"
    # the site's "None" bucket (products without the attribute) is not a value
    assert all(v["value"] != "None" for f in facets for v in f["values"])


def test_search_second_page_and_sparse_rows():
    listing = parsers.parse_listing(load("listing_search_page_2.json"), 2, 24)
    assert listing["pagination"]["page"] == 2 and len(listing["products"]) == 24
    first_page = {p["sku"] for p in parsers.parse_listing(load("listing_search_laptop.json"), 1, 24)["products"]}
    assert not first_page & {p["sku"] for p in listing["products"]}
    sold_by_others = [p for p in listing["products"] if p["seller"]["type"] == "marketplace"]
    assert sold_by_others and all(isinstance(p["seller"]["id"], int) for p in sold_by_others)
    keys_are_clean(listing)


def test_search_past_end_is_an_empty_page():
    listing = parsers.parse_listing(load("listing_search_past_end.json"), 100, 100)
    assert listing["products"] == [] and listing["facets"] == [] and listing["sort_options"] == []
    assert listing["pagination"] == {"page": 100, "items_per_page": 100, "total_pages": 0, "total_count": 0}


def test_search_no_results():
    listing = parsers.parse_listing(load("listing_search_no_results.json"), 1, 24)
    assert listing["products"] == [] and listing["pagination"]["total_count"] == 0
    assert listing["applied_filters"] == [{"facet": "currentprice_facet", "values": ["1 to 2"], "is_automatic": False}]
    assert parsers.applied_facets(load("listing_search_no_results.json")["data"]["search"]) == {"currentprice_facet": ["1 to 2"]}


def test_search_typo_is_corrected():
    listing = parsers.parse_listing(load("listing_search_typo.json"), 1, 5)
    assert listing["corrected_query"] == "laptop" and len(listing["products"]) == 5


def test_search_redirect():
    listing = parsers.parse_listing(load("listing_search_redirect.json"), 1, 24)
    assert listing["redirect_link"] == "https://www.bestbuy.com/site/Electronics/Gift-Cards/cat09000.c?id=cat09000"
    assert listing["products"] == [] and listing["pagination"]["total_count"] == 0


def test_search_filtered():
    listing = parsers.parse_listing(load("listing_search_filtered.json"), 1, 10)
    applied = {row["facet"]: row["values"] for row in listing["applied_filters"] if not row["is_automatic"]}
    assert applied == {"brand_facet": ["HP", "Dell"], "customerreviews_facet": ["4 & Up"], "condition_facet": ["New"],
                       "currentprice_facet": ["500 to 900"]}
    assert listing["sort"] == "price_low_to_high"
    prices = [p["price"]["current"] for p in listing["products"]]
    assert prices == sorted(prices) and 500 <= prices[0] and prices[-1] <= 900
    assert {p["brand"] for p in listing["products"]} <= {"HP", "Dell"}
    assert listing["pagination"]["total_pages"] == 14 and listing["pagination"]["total_count"] == 131


def test_unknown_facet_is_not_applied():
    search = load("listing_search_unknown_facet.json")["data"]["search"]
    assert "zz_facet" not in parsers.applied_facets(search)


def test_package_rows_are_skipped():
    raw = load("listing_search_package_rows.json")
    assert any(doc.get("combo") for doc in raw["data"]["search"]["documents"])
    listing = parsers.parse_listing(raw, 1, 24)
    assert [p["sku"] for p in listing["products"]] == [DELL]


def test_category_listing():
    raw = load("listing_category.json")
    listing = parsers.parse_listing(raw, 1, 24)
    assert len(listing["products"]) == 24 and listing["sort"] == "best_match" and listing["detected_category"] is None
    record = parsers.parse_listing_category(raw)
    assert record["id"] == "pcmcat247400050000" and record["name"] == "Windows Laptops"
    assert record["link"].endswith("/pcmcat247400050000.c?id=pcmcat247400050000")
    assert [c["id"] for c in record["path"]] == ["cat00000", "abcat0500000", "abcat0502000", "pcmcat138500050001"]
    assert {c["name"] for c in record["subcategories"]} >= {"2-in-1 Windows Laptops", "Refurbished Windows Laptops"}
    within = parsers.parse_listing(load("listing_category_within.json"), 1, 10)
    assert 0 < within["pagination"]["total_count"] < listing["pagination"]["total_count"]
    keys_are_clean(listing)


def test_category_listing_unknown():
    raw = load("listing_category_unknown.json")
    assert parsers.parse_listing_category(raw) is None
    assert parsers.parse_listing(raw, 1, 24)["products"] == []


def test_deals_listing():
    listing = parsers.parse_listing(load("listing_deals.json"), 1, 24)
    assert len(listing["products"]) == 24 and listing["pagination"]["total_count"] > 100000
    assert listing["pagination"]["total_pages"] == 100
    assert all(p["price"]["is_on_sale"] for p in listing["products"])


def test_reachable_pages():
    assert parsers.reachable_pages(0, 24) == 0 and parsers.reachable_pages(None, 24) == 0
    assert parsers.reachable_pages(131, 10) == 14 and parsers.reachable_pages(5000, 100) == 24
    assert parsers.reachable_pages(5000, 24) == 100 and parsers.reachable_pages(1, 100) == 1


# ---- products/details ----------------------------------------------------------------

def test_product():
    product = parsers.parse_product(load("products_two.json")[0])
    assert product["sku"] == DELL and product["bsin"] == "J3K4L6QW5C" and product["upc"] == "884116652427"
    assert product["brand"] == "Dell" and product["model_number"] == "LDC15250-7427BLK-PUS"
    assert product["link"].endswith("/J3K4L6QW5C/sku/6668952") and product["condition"] == "new"
    assert product["product_types"] == ["Laptop Computer"] and product["product_class"] == "Laptop_Computers"
    assert product["status"] == "active" and product["description"].startswith("Effortlessly chic.")
    assert product["features"][0] == {"title": "Effortlessly chic", "description": product["features"][0]["description"]}
    assert len(product["features"]) == 5 and len(product["included_items"]) == 4
    assert product["highlights"][0]["name"] == "Windows AI" and product["highlights"][0]["levels"][0]["bullets"]
    groups = {g["group"]: g["items"] for g in product["specifications"]}
    assert len(groups) == 17 and groups["Key Specs"][1] == {
        "name": "Screen Size", "value": "15.6 inches",
        "definition": "Size of the screen, measured diagonally from corner to corner."}
    assert product["dimensions"] == {"height": {"value": 0.75, "unit": "inches"}, "width": {"value": 14.11, "unit": "inches"},
                                     "depth": {"value": 9.27, "unit": "inches"}, "weight": {"value": 4.28, "unit": "pounds"}}
    assert product["category"]["id"] == "pcmcat247400050000"
    assert [c["id"] for c in product["category_path"]] == ["cat00000", "abcat0500000", "abcat0502000",
                                                           "pcmcat138500050001", "pcmcat247400050000"]
    assert len(product["images"]) == 13 and all("/prescaled/" not in i["link"] for i in product["images"])
    assert product["images"][0]["view"] == "Front_Zoom" and product["image"] == product["images"][0]["link"]
    video = product["videos"][0]
    assert video["title"] == "Windows 11 Home" and video["duration_seconds"] == 111
    assert video["published_at"] == "2024-10-08T21:17:59Z" and video["link"].endswith(".mp4")
    assert video["captions"][0]["language"] == "en" and all(not t.isdigit() for t in video["tags"])
    assert product["price"]["current"] == 599.99 and product["price"]["regular"] == 999.99
    assert product["price"]["installments"] == [{"vendor": "zip", "type": "BNPL", "count": 8, "amount": 75, "service_fee": 0.5}]
    assert product["price"]["gift_skus"] == ["6549173", "6548823"]
    assert [o["condition"] for o in product["buying_options"]] == ["new", "open_box_excellent", "open_box_good", "open_box_fair"]
    assert product["rating"] == 4.7 and product["reviews_count"] == 434
    assert product["review_summary"].startswith("Customers have good things to say")
    assert product["customers_love"].startswith("Customers say") and "<" not in product["customers_love"]
    assert product["seller"]["type"] == "best_buy" and product["seller"]["name"] == "Best Buy"
    assert product["badges"][0]["name"] == "Core essentials PC" and product["badges"][0]["starts_at"] == "2026-09-18T22:30:00Z"
    assert {"name": "Series", "values": ["Dell DC15250"]} in product["attributes"]
    assert all(not a["name"].lower().startswith("demourl") for a in product["attributes"])
    assert product["logos"][0]["name"] == "Intel 13th Generation Core i7"
    assert product["proposition_65_warning"] == "Reproductive Harm" and product["open_box_check_level"] == 2
    assert product["manuals"] == [] and product["esrb"] is None and product["release_date"] is None
    keys_are_clean(product)


def test_product_sold_by_a_marketplace_seller_without_reviews():
    product = parsers.parse_product(load("products_two.json")[1])
    assert product["sku"] == ZENBOOK and product["upc"] == "199393420103"
    assert product["seller"] == {"type": "marketplace", "id": 6562, "name": "Velztorm", "rating": 4.28, "reviews_count": 517}
    assert product["rating"] is None and product["reviews_count"] == 0 and product["review_summary"] is None
    assert product["videos"] == [] and product["badges"] == [] and product["highlights"] is not None
    assert product["price"]["current"] == 1519.99 and product["price"]["installments"] == []
    assert product["buying_options"] == [{"condition": "new", "link": product["buying_options"][0]["link"]}]
    keys_are_clean(product)


def test_product_unknown():
    parts = load("products_unknown.json")[0]
    assert parsers.product_missing(parts) is True and parsers.parse_product(parts) is None
    assert parsers.product_missing(load("products_two.json")[0]) is False


def test_product_survives_missing_parts():
    parts = load("products_two.json")[0]
    only_main = {"PRODUCT": parts["PRODUCT"]}
    product = parsers.parse_product(only_main)
    assert product["sku"] == DELL and product["price"] is None and product["seller"] is None
    assert product["features"] == [] and product["rating"] == 4.7
    assert parsers.parse_product({}) is None and parsers.parse_product({"PRODUCT": {"data": None}}) is None
    assert parsers.product_missing({}) is False


# ---- products/offers -----------------------------------------------------------------

def test_offer_identity():
    identity = parsers.parse_offer_identity(load("offers_identity.json"))
    assert identity["sku"] == DELL and identity["bsin"] == "J3K4L6QW5C" and identity["condition"] == "new"
    assert identity["seller"]["type"] == "best_buy" and identity["open_box_codes"] == [2, 1, 0]
    assert len(identity["other_skus"]) == 14
    other = identity["other_skus"][0]
    assert other["sku"] == "12575317" and other["seller"]["name"] == "RYTEK" and other["seller"]["id"] == 2695
    assert other["link"].endswith("/sku/12575317")
    alone = parsers.parse_offer_identity(load("offers_identity_marketplace.json"))
    assert alone["sku"] == ZENBOOK and alone["other_skus"] == [] and alone["open_box_codes"] == []
    assert parsers.parse_offer_identity(load("offers_identity_unknown.json")) is None


def test_offers_from_prices():
    new, excellent, good, fair = load("prices_open_box.json")
    seller = {"type": "best_buy"}
    row = parsers.parse_offer(new, "6690109", seller, "new", "https://www.bestbuy.com/x")
    assert row == {"sku": "6690109", "condition": "new", "seller": seller, "price": 749.99, "regular_price": 849.99,
                   "currency": "USD", "savings": 100, "savings_percent": 11, "deal_label": None,
                   "link": "https://www.bestbuy.com/x"}
    row = parsers.parse_offer(excellent, "6690109", seller, None)
    assert row["condition"] == "open_box_excellent" and row["price"] == 663.99 and row["savings"] == 186
    assert row["link"].endswith("/sku/6690109/openbox?condition=excellent")
    assert parsers.parse_offer(good, "6690109", seller, None) is None        # this item has no "good" copy on sale
    assert parsers.parse_offer(fair, "6690109", seller, None)["price"] == 625.99


# ---- products/availability -----------------------------------------------------------

def test_availability():
    answer = parsers.parse_availability(load("fulfillment.json"), DELL, "10001")
    assert answer["sku"] == DELL and answer["zip"] == "10001"
    assert answer["status"] == "add_to_cart" and answer["status_text"] == "Add to Cart" and answer["is_purchasable"] is True
    assert answer["condition"] == "new"
    assert answer["shipping"]["is_available"] is True
    options = {o["code"]: o for o in answer["shipping"]["options"]}
    assert options["SAME_DAY"]["price"] == 9.99 and options["SAME_DAY"]["currency"] == "USD"
    assert options["SAME_DAY"]["arrives_by"] == "2026-11-19" and options["SAME_DAY"]["order_by"] == "2026-11-19T22:00:00Z"
    assert sum(1 for o in answer["shipping"]["options"] if o["is_default"]) == 1
    assert answer["delivery"] == {"is_available": False, "is_installation_available": False, "is_backordered": None}
    assert answer["pickup"]["store"] == {"id": "482", "name": "Chelsea (23rd and 6th)", "city": "New York",
                                         "zip": "10010", "link": "https://stores.bestbuy.com/482"}
    assert answer["pickup"]["is_available"] is True and answer["pickup"]["is_in_stock"] is True
    assert answer["pickup"]["ready_from"] == "2026-11-19"
    assert answer["nearby_stores_checked"] == 27 and len(answer["nearby_stores"]) == 27
    near = answer["nearby_stores"][0]
    assert near["id"] == "1531" and near["name"] == "Union Square" and near["distance_miles"] == 0.5
    assert near["is_in_stock"] is True
    assert "quantity" not in json.dumps(answer)       # the site prints 9999 for every stock quantity
    keys_are_clean(answer)


def test_availability_of_a_marketplace_item():
    answer = parsers.parse_availability(load("fulfillment_marketplace.json"), ZENBOOK, "10001")
    assert answer["is_purchasable"] is True and answer["shipping"]["is_available"] is True
    assert answer["shipping"]["options"][0]["name"] == "Next Day" and answer["shipping"]["options"][0]["price"] == 29.99
    assert answer["pickup"]["is_available"] is False and answer["nearby_stores"] == []


def test_availability_of_an_unknown_sku_is_not_available():
    answer = parsers.parse_availability(load("fulfillment_unknown.json"), "9999999999", "10001")
    assert answer["status"] == "not_available" and answer["is_purchasable"] is False
    assert parsers.parse_availability({}, "1", "10001")["status"] is None


# ---- products/reviews ----------------------------------------------------------------

def test_reviews():
    raw, identity = load("reviews.json")
    parsed = parsers.parse_reviews(raw)
    assert parsed["total"] == 434 and len(parsed["reviews"]) == 20 and len(parsed["photos"]) == 10
    review = parsed["reviews"][0]
    assert review["id"] == "d1197e05-2f36-3739-9276-3c6684a6f0c4" and review["rating"] == 5
    assert review["title"].startswith("Reliable daily driver") and review["author"] == "JustinT"
    assert review["text"].startswith("Spent some real time") and "\n" in review["text"]
    assert review["submitted_at"] == "2026-05-15T17:02:33Z" and review["days_owned"] == 20
    assert review["is_recommended"] is True and review["is_verified_purchaser"] is True and review["is_incentivized"] is True
    assert review["helpful_count"] == 3 and review["unhelpful_count"] == 0 and review["comments_count"] == 0
    assert review["images"] and review["badges"] == ["Incentivized", "Verified Purchaser"]
    assert review["source"] == "Best Buy" and review["is_syndicated"] is False
    assert parsed["photos"][0]["review_id"] == review["id"]
    assert parsers.parse_identity(identity) == {
        "sku": DELL, "name": identity["data"]["productBySkuId"]["name"]["short"],
        "link": identity["data"]["productBySkuId"]["url"]["skuSpecificUrl"], "rating": 4.7, "reviews_count": 434}
    keys_are_clean(parsed)


def test_reviews_other_pages():
    newest = parsers.parse_reviews(load("reviews_page_2_newest.json")[0])
    dates = [r["submitted_at"] for r in newest["reviews"]]
    assert len(dates) == 20 and dates == sorted(dates, reverse=True)
    low = parsers.parse_reviews(load("reviews_rating_1_2.json")[0])
    assert low["total"] == len(low["reviews"]) == 13 and {r["rating"] for r in low["reviews"]} == {1, 2}
    found = parsers.parse_reviews(load("reviews_search.json")[0])
    assert found["total"] == 43 and len(found["reviews"]) == 5
    past = parsers.parse_reviews(load("reviews_past_end.json")[0])
    assert past["total"] == 434 and past["reviews"] == []
    responses = [r for r in low["reviews"] + newest["reviews"] if r["responses"]]
    for review in responses:
        assert review["responses"][0]["text"]


def test_reviews_none_and_unknown():
    raw, identity = load("reviews_none.json")
    assert parsers.parse_reviews(raw) == {"total": 0, "reviews": [], "photos": []}
    assert parsers.parse_identity(identity)["sku"] == ZENBOOK and parsers.parse_identity(identity)["rating"] is None
    raw, identity = load("reviews_unknown.json")
    assert parsers.parse_reviews(raw) is None and parsers.parse_identity(identity) is None
    assert parsers.parse_identity(load("identity_unknown.json")) is None
    assert parsers.parse_identity(load("identity.json"))["reviews_count"] == 434


# ---- products/questions --------------------------------------------------------------

def test_questions():
    parsed = parsers.parse_questions(load("questions.json")[0])
    assert parsed["is_enabled"] is True
    assert parsed["counts"] == {"questions": 137, "answered_questions": 107, "answers": 142, "ai_questions": 7, "ai_answers": 7}
    assert parsed["summary"].startswith("This laptop features") and parsed["link"].endswith("/6668952")
    assert len(parsed["questions"]) == 10
    first = parsed["questions"][0]          # the site lists its AI-generated questions first, without dates
    assert first["is_ai_generated"] is True and first["asked_at"] is None and first["author"] is None
    assert first["question"].startswith("Does this laptop have") and first["answers_count"] == 1
    assert first["answers"][0]["text"] and first["answers"][0]["answered_at"] is None
    keys_are_clean(parsed)


def test_questions_page_2_has_customer_questions():
    parsed = parsers.parse_questions(load("questions_page_2.json")[0])
    question = parsed["questions"][0]
    assert question["question"] == "Does this computer has a ethernet connection" and question["author"] == "Claruce"
    assert question["asked_at"] == "2026-05-17T11:44:57Z" and question["answers_count"] == 2
    answer = question["answers"][0]
    assert answer["author"] == "YourDellTeam" and answer["answered_at"] == "2026-05-18T18:50:01Z"
    assert answer["helpful_count"] == 6 and answer["badges"] == ["Dell Representative"]


def test_questions_empty_and_unknown():
    assert parsers.parse_questions(load("questions_past_end.json")[0])["questions"] == []
    none = parsers.parse_questions(load("questions_none.json")[0])
    assert none["questions"] == [] and none["counts"]["questions"] == 0 and none["summary"] is None
    assert parsers.parse_questions(load("questions_unknown.json")[0]) is None


# ---- suggestions, similar ------------------------------------------------------------

def test_suggestions():
    parsed = parsers.parse_suggestions(load("suggestions.json"))
    assert parsed["corrected_query"] is None and len(parsed["suggestions"]) == 10
    assert parsed["suggestions"][0]["term"] == "laptop"
    assert parsed["suggestions"][0]["categories"] == [{"id": "pcmcat247400050000", "name": "Windows Laptops"}]
    assert parsed["suggestions"][0]["skus"][0] == DELL
    assert parsers.parse_suggestions(load("suggestions_none.json")) == {"corrected_query": None, "suggestions": []}
    assert parsers.parse_suggestions({}) == {"corrected_query": None, "suggestions": []}


def test_similar():
    parsed = parsers.parse_similar(load("similar.json"))
    assert parsed["product"]["sku"] == "6690109" and parsed["product"]["price"] == 749.99
    assert len(parsed["products"]) == 10
    row = parsed["products"][0]
    assert row["sku"] == "6690110" and row["price"] == 449.99 and row["currency"] == "USD"
    assert row["rating"] == 4.5 and row["reviews_count"] == 26
    assert row["link"].startswith("https://www.bestbuy.com/product/") and row["reviews_link"].endswith("/sku/6690110/reviews")
    assert row["specifications"][0]["group"] == "Key Specs" and row["description"]
    assert parsers.parse_similar(load("similar_unknown.json")) is None
    keys_are_clean(parsed)


# ---- categories ----------------------------------------------------------------------

def test_category():
    category = parsers.parse_category(load("category_computers.json"))
    assert category["id"] == "abcat0500000" and category["name"] == "Computers & Tablets"
    assert category["link"] == "https://www.bestbuy.com/site/electronics/computers-pcs/abcat0500000.c?id=abcat0500000"
    assert category["path"] == [{"id": "cat00000", "name": "Best Buy", "link": "https://www.bestbuy.com/"}]
    assert category["title"] == "Computers & Tablets - Best Buy" and "computers" in category["keywords"]
    assert category["started_at"] == "2007-08-09T05:00:00Z"
    assert category["subcategories_count"] == 15 == len(category["subcategories"])
    assert {"id": "abcat0502000", "name": "Laptops"}.items() <= next(
        c for c in category["subcategories"] if c["id"] == "abcat0502000").items()
    keys_are_clean(category)


def test_category_root_leaf_unknown():
    root = parsers.parse_category(load("category_root.json"))
    assert root["id"] == "cat00000" and root["path"] == []
    names = {c["name"] for c in root["subcategories"]}
    assert "Computers & Tablets" in names and "Appliances" in names
    # children named by their own id are ad slots of the home page
    assert not {"sv_display_ad_marquee", "homepage-pushdown", "leaderboardfooter"} & {c["id"] for c in root["subcategories"]}
    leaf = parsers.parse_category(load("category_leaf.json"))
    assert leaf["subcategories"] == [] and leaf["subcategories_count"] == 0 and len(leaf["path"]) == 5
    assert parsers.parse_category(load("category_unknown.json")) is None


# ---- stores --------------------------------------------------------------------------

def test_stores():
    stores = parsers.parse_stores(load("stores_10001.json"))
    assert len(stores) == 25 and [s["distance_miles"] for s in stores] == sorted(s["distance_miles"] for s in stores)
    store = stores[0]
    assert list(store)[:3] == ["id", "name", "distance_miles"]
    assert store["id"] == "482" and store["name"] == "Chelsea (23rd and 6th)" and store["internal_name"] == "23RD AND 6TH NY"
    assert store["link"] == "https://stores.bestbuy.com/482" and store["status"] == "Open" and store["type"] == "Big Box"
    assert store["phone"] == "212-366-1373"
    assert store["address"] == {"line1": "60 W 23rd St", "line2": None, "city": "New York", "state": "NY",
                                "zip": "10010", "country": "US"}
    assert store["latitude"] == 40.74264 and store["longitude"] == -73.9927 and store["timezone"] == "Eastern"
    assert "Apple Shop" in store["services"] and store["has_pickup"] is True
    assert store["opened_on"] == "2002-06-12" and store["closed_on"] is None
    assert store["hours"][0] == {"date": "2026-11-15", "opens": "10:00", "closes": "20:00", "is_closed": False,
                                 "is_open_24_hours": False}
    assert store["curbside_hours"][0]["is_closed"] is True and store["curbside_hours"][0]["opens"] is None
    keys_are_clean(stores)


def test_stores_unknown_zip_and_store():
    assert parsers.parse_stores(load("stores_unknown_zip.json")) is None
    store = parsers.parse_store(load("store_482.json")["data"]["storeById"])
    assert store["id"] == "482" and "distance_miles" not in store and len(store["hours"]) == 42
    assert parsers.parse_store(load("store_unknown.json")["data"]["storeById"]) is None
    assert parsers.parse_store({}) is None


# ---- sellers, BSIN -------------------------------------------------------------------

def test_seller():
    seller = parsers.parse_seller(load("seller_6562.json"))
    assert seller["id"] == 6562 and seller["name"] == "Velztorm" and seller["status"] == "open"
    assert seller["description"].startswith("Velztorm is your one-stop-shop")
    assert seller["rating"] == 4.28 and seller["reviews_count"] == 517
    assert seller["rating_distribution"] == [{"stars": 5, "count": 383}, {"stars": 4, "count": 36}, {"stars": 3, "count": 23},
                                             {"stars": 2, "count": 11}, {"stars": 1, "count": 64}]
    assert len(seller["recent_reviews"]) == 10
    assert seller["recent_reviews"][0] == {
        "id": "aa578317-ec86-42cb-a616-0a3f08e42d9c", "rating": 5, "title": "Great Product and Excellent Service",
        "text": seller["recent_reviews"][0]["text"], "submitted_at": "2026-11-18T21:40:50Z"}
    assert parsers.parse_seller(load("seller_unknown.json")) is None
    keys_are_clean(seller)


def test_bsin_skus():
    skus = parsers.bsin_skus(load("bsin.json"))
    assert skus[0] == DELL and len(skus) == 15 and len(set(skus)) == 15
    assert parsers.bsin_skus(load("bsin_unknown.json")) == []
    assert parsers.bsin_skus({}) == []


@pytest.mark.parametrize("parser, args", [
    (parsers.parse_listing, ({}, 1, 24)), (parsers.parse_listing, ({"data": {"search": None}}, 1, 24)),
    (parsers.parse_listing_category, ({},)), (parsers.parse_product, ({"PRODUCT": {"data": {"productBySkuId": {}}}},)),
    (parsers.parse_offer_identity, ({},)), (parsers.parse_offer, ({}, "1", None, "new")),
    (parsers.parse_availability, ({"data": {"fulfillmentOptions": None}}, "1", "10001")),
    (parsers.parse_reviews, ({},)), (parsers.parse_identity, ({},)), (parsers.parse_questions, ({},)),
    (parsers.parse_suggestions, (None,)), (parsers.parse_similar, ({},)), (parsers.parse_category, ({},)),
    (parsers.parse_stores, ({},)), (parsers.parse_store, (None,)), (parsers.parse_seller, ({},)),
])
def test_parsers_never_crash_on_an_empty_answer(parser, args):
    parser(*args)
