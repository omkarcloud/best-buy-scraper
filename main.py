"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from best_buy import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # the full product (a SKU, a BSIN or any bestbuy.com product link): price and deal, specs,
    # highlights, images, videos, rating and the AI review summary
    save("products_details.json", run(schemas.ProductsDetailsSchema, api.products_details, product='6397846'))

    # 20 reviews a page: rating, title, text, author, date, photos and verified-purchase badges
    save("products_reviews.json", run(schemas.ProductsReviewsSchema, api.products_reviews, product='6397846'))

    # every way to buy it: new, open-box, refurbished and other sellers' offers, cheapest first
    save("products_offers.json", run(schemas.ProductsOffersSchema, api.products_offers, product='6397846'))
