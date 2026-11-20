"""Marshmallow request schemas for every /best-buy/* route.

ONE param per input (never a sibling `url` / `id` pair):

    product    a SKU (6668952), a BSIN (J3K4L6QW5C) OR a bestbuy.com
               product link
    products   up to 20 of those, comma-separated
    category   a category id (abcat0500000) OR a bestbuy.com category link
    store      a store number (482) OR a stores.bestbuy.com link
    seller     a marketplace seller id (6562)
    zip        a US ZIP code, five digits

The listing routes (products/search, categories/products, deals/list) share
page, limit, sort and the filters: brand, min_price / max_price,
min_rating, condition, on_sale, sold_by_best_buy and `filter`, which takes
any other facet a result page returns as comma-separated facet:value
pairs. The site serves at most 100 rows a page and 2,400 rows of a listing.
"""
from marshmallow import ValidationError, fields, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, Flag, LimitField, PageField, PositiveInt,
                           Price, QueryField, RefField, StrippedString)
from best_buy import refs
from scraper_utils.schema import max_length as _max_length

MAX_LIMIT = refs.MAX_PAGE_SIZE          # a listing page of 101 answers nothing
MAX_PAGE = refs.MAX_LISTING_PAGE        # page 101 answers nothing
MAX_REVIEWS_LIMIT = refs.MAX_PAGE_SIZE  # 101 fails upstream
MAX_REVIEWS_PAGE = 2000
MAX_QUESTIONS_LIMIT = refs.MAX_PAGE_SIZE    # 101 answers an empty list
MAX_QUESTIONS_PAGE = 500
MAX_FILTERS = 10
MAX_BRANDS = 10


class ProductRefField(RefField):
    resolver = staticmethod(refs.resolve_product)


class ProductsRefField(RefField):
    """'6668952, J3K4L6QW5C, <link>' -> the references, in order, repeats dropped."""
    resolver = staticmethod(refs.resolve_products)


class CategoryRefField(RefField):
    resolver = staticmethod(refs.resolve_category)


class StoreRefField(RefField):
    resolver = staticmethod(refs.resolve_store)


class SellerRefField(RefField):
    resolver = staticmethod(refs.resolve_seller)


class ZipField(RefField):
    """A US ZIP code, five digits (ZIP+4 is cut to five)."""
    resolver = staticmethod(refs.resolve_zip)


class DefaultedChoice(ChoiceField):
    """A choice whose empty value means its default, as a missing one does
    (StrippedString hands back None for `?sort=`)."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None and isinstance(self.load_default, str):
            return self.load_default
        return value


class FacetFilterField(fields.Field):
    """'facet:value, facet:value' -> [(facet, value)] over the facets a
    result page returns (a facet's `facet` and a value's `value`). A facet
    that has a param of its own is refused here, so one filter is never
    given twice."""

    def __init__(self, max_items=MAX_FILTERS, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.max_items = max_items

    def _deserialize(self, value, attr, data, **kwargs):
        pairs = []
        for raw in str(value).split(","):
            item = raw.strip()
            if not item:
                continue
            name, sep, val = item.partition(":")
            name, val = name.strip(), val.strip()
            if not sep or not name or not val:
                raise ValidationError(f"'{item}' must be facet:value (e.g. colorcat_facet:Black).")
            if name in refs.NAMED_FACETS:
                raise ValidationError(f"'{name}' has a param of its own: use {refs.NAMED_FACETS[name]}.")
            if (name, val) not in pairs:
                pairs.append((name, val))
        if not pairs:
            return None
        if len(pairs) > self.max_items:
            raise ValidationError(f"At most {self.max_items} refinements.")
        return pairs


class RatingsField(CommaListField):
    """'1,2' -> [1, 2]: the star ratings to keep."""

    def __init__(self, **kwargs):
        super().__init__(allowed=["1", "2", "3", "4", "5"], max_items=5, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        return sorted(int(i) for i in items) if items else None


def _product_field():
    return ProductRefField(metadata={"description": "SKU (6668952), BSIN (J3K4L6QW5C) or a bestbuy.com product link"})


# ---- listings ------------------------------------------------------------------------

class _ListingSchema(BaseSchema):
    page = PageField(max_page=MAX_PAGE, metadata={"description": "page number, from 1; the site serves the first "
                                                                 "2,400 results of a listing"})
    limit = LimitField(default=24, max_size=MAX_LIMIT, metadata={"description": "products per page, up to 100"})
    sort = ChoiceField(dict(refs.SORTS), metadata={
        "description": "best_match, best_selling, price_low_to_high, price_high_to_low, customer_rating, "
                       "best_discount or new_arrivals (default: best_match for a search, best_selling for a category)"})
    brand = CommaListField(upper=False, max_items=MAX_BRANDS, metadata={
        "description": "one or more brands as the site writes them, comma-separated (HP, Dell)"})
    min_price = Price(metadata={"description": "lowest price in US dollars"})
    max_price = Price(metadata={"description": "highest price in US dollars"})
    min_rating = ChoiceField(dict(refs.MIN_RATINGS), metadata={
        "description": "lowest customer rating: 1, 2, 3, 4, 5 or top_rated"})
    condition = ChoiceField(dict(refs.CONDITIONS), metadata={"description": "new, open_box or refurbished"})
    on_sale = Flag(metadata={"description": "true: only products on sale"})
    sold_by_best_buy = Flag(metadata={"description": "true: only products sold and shipped by Best Buy itself"})
    filter = FacetFilterField(metadata={
        "description": "any other facet a result page returns, as comma-separated facet:value pairs "
                       "(colorcat_facet:Black, features_facet:Touch Screen)"})

    @validates_schema
    def _price_range(self, data, **kwargs):
        low, high = data.get("min_price"), data.get("max_price")
        if low is not None and high is not None and low > high:
            raise ValidationError("min_price is higher than max_price.", "min_price")


class ProductsSearchSchema(_ListingSchema):
    query = QueryField(metadata={"description": "search keywords, a model number or a UPC"})
    category = CategoryRefField(required=False, load_default=None, metadata={
        "description": "search inside this category: a category id or a bestbuy.com category link"})


class CategoriesProductsSchema(_ListingSchema):
    category = CategoryRefField(metadata={
        "description": "category id (abcat0500000) or a bestbuy.com category link; its subcategories are included"})
    query = QueryField(required=False, load_default=None, validate=_max_length(200),
                       metadata={"description": "keywords within the category"})


class DealsListSchema(_ListingSchema):
    type = DefaultedChoice(dict(refs.DEAL_TYPES), load_default=refs.DEAL_TYPES["on_sale"], metadata={
        "description": "on_sale (default), clearance or member_deals"})
    category = CategoryRefField(required=False, load_default=None, metadata={
        "description": "only deals in this category: a category id or link; without it, the whole catalog"})

    class Meta(BaseSchema.Meta):
        exclude = ("on_sale",)


# ---- products ------------------------------------------------------------------------

class ProductsDetailsSchema(BaseSchema):
    product = _product_field()


class ProductsBulkSchema(BaseSchema):
    products = ProductsRefField(metadata={
        "description": f"up to {refs.MAX_BULK_PRODUCTS} SKUs, BSINs or product links, comma-separated"})


class ProductsOffersSchema(BaseSchema):
    product = _product_field()


class ProductsAvailabilitySchema(BaseSchema):
    product = _product_field()
    zip = ZipField(required=False, load_default=None, metadata={
        "description": "US ZIP code for shipping, delivery and the nearest stores (default 10001, New York)"})
    store = StoreRefField(required=False, load_default=None, metadata={
        "description": "store number (482) or a stores.bestbuy.com link for pickup; default: the store nearest the ZIP code"})


class ProductsReviewsSchema(BaseSchema):
    product = _product_field()
    page = PageField(max_page=MAX_REVIEWS_PAGE, metadata={"description": "page number, from 1"})
    limit = LimitField(default=20, max_size=MAX_REVIEWS_LIMIT, metadata={"description": "reviews per page, up to 100"})
    sort = DefaultedChoice(dict(refs.REVIEW_SORTS), load_default=refs.REVIEW_SORTS["best_match"], metadata={
        "description": "best_match (default), newest, oldest, most_helpful, highest_rated or lowest_rated"})
    rating = RatingsField(metadata={"description": "only reviews with these star ratings, comma-separated (1,2)"})
    query = StrippedString(load_default=None, validate=_max_length(100),
                           metadata={"description": "only reviews that mention this text"})


class ProductsQuestionsSchema(BaseSchema):
    product = _product_field()
    page = PageField(max_page=MAX_QUESTIONS_PAGE, metadata={"description": "page number, from 1"})
    limit = LimitField(default=10, max_size=MAX_QUESTIONS_LIMIT, metadata={"description": "questions per page, up to 100"})
    query = StrippedString(load_default=None, validate=_max_length(100),
                           metadata={"description": "only questions that mention this text"})


class ProductsSuggestionsSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={"description": "the start of a search term"})
    limit = LimitField(default=10, max_size=refs.MAX_SUGGESTIONS,
                       metadata={"description": "suggestions to return, up to 20"})


class ProductsSimilarSchema(BaseSchema):
    product = _product_field()
    limit = LimitField(default=10, max_size=refs.MAX_SIMILAR,
                       metadata={"description": "comparable products to return, up to 25 (the site may have fewer)"})


# ---- categories ----------------------------------------------------------------------

class CategoriesListSchema(BaseSchema):
    category = CategoryRefField(required=False, load_default=None, metadata={
        "description": "a category id or link: answers that category with its subcategories; without it, the departments"})


# ---- stores --------------------------------------------------------------------------

class StoresSearchSchema(BaseSchema):
    zip = ZipField(metadata={"description": "US ZIP code: the stores come nearest first, up to 25"})
    radius = PositiveInt(max_value=refs.MAX_STORE_RADIUS, load_default=refs.DEFAULT_STORE_RADIUS, metadata={
        "description": f"search radius in miles (default {refs.DEFAULT_STORE_RADIUS}, up to {refs.MAX_STORE_RADIUS})"})


class StoresDetailsSchema(BaseSchema):
    store = StoreRefField(metadata={"description": "store number (482) or a stores.bestbuy.com link"})


# ---- sellers -------------------------------------------------------------------------

class SellersDetailsSchema(BaseSchema):
    seller = SellerRefField(metadata={"description": "marketplace seller id: the seller.id of a product (6562)"})
