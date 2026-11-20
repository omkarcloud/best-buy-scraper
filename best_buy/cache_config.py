"""Cache TTL per /best-buy/* surface (cache.py, keyed on the validated
params). Prices and deal labels move during the day in a sale week, stock
moves by the hour; reviews, categories and stores move slowly.
"""
from datetime import timedelta

# products/search, categories/products, deals/list: rows carry prices and
# deal labels, which the site changes several times a day in a sale week
LISTING_CACHE = timedelta(minutes=30)
# the product record behind products/details: price, offers and rating
PRODUCT_CACHE = timedelta(minutes=30)
# products/bulk: the same records, asked several at a time
BULK_CACHE = timedelta(minutes=30)
# products/offers: open-box and marketplace prices come and go with single units
OFFERS_CACHE = timedelta(minutes=15)
# products/availability: store stock and delivery dates move through the day
AVAILABILITY_CACHE = timedelta(minutes=5)
# who a SKU is (name, link, rating): what availability, reviews and questions print above their rows
IDENTITY_CACHE = timedelta(hours=6)
# the SKUs behind a BSIN: an item's sellers change over days, its own SKU never
BSIN_CACHE = timedelta(days=1)
# products/reviews pages: a busy product gains a few reviews a day
REVIEWS_CACHE = timedelta(hours=3)
# products/questions pages: a few new questions a week
QUESTIONS_CACHE = timedelta(hours=6)
# products/suggestions: the suggestion index follows searches over days
SUGGESTIONS_CACHE = timedelta(hours=12)
# products/similar: the compare block is recomputed by the site about daily
SIMILAR_CACHE = timedelta(hours=6)
# categories/list: the tree moves with merchandising changes
CATEGORIES_CACHE = timedelta(hours=24)
# stores/search and stores/details: hours for the next weeks, services, phone
STORES_CACHE = timedelta(hours=12)
# the nearest store of a ZIP code (products/availability without `store`)
NEAREST_STORE_CACHE = timedelta(days=7)
# sellers/details: rating and the ten newest reviews
SELLER_CACHE = timedelta(hours=6)
