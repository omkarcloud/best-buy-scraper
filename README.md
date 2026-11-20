# 🚀 Best Buy Scraper

Best Buy Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Best Buy data for free.

## ✨ What Can I Get?

- 🛒 **Full details of 778K+ products** — price & deals, specs, highlights, images, videos, rating, AI review summary, UPC & model number
- ⭐ **Every review & customer question** — 12,300+ reviews on AirPods Pro 3 alone: ratings, text, photos, verified-purchase badges & staff answers
- 🏷️ **133K+ live deals** — on sale, clearance & member deals with savings, deal labels, open-box & marketplace offers
- 📍 **Stock & pickup for any US ZIP** — shipping & delivery dates, store pickup, stock at nearby stores, store hours & services

## 🛒 Example: A Full Best Buy Product

```json
{
  "sku": "6397846",
  "bsin": "JJGCQLKXL7",
  "name": "Apple - MacBook Air 13-inch Laptop - M5 chip with 10-core CPU and 8-core GPU - 16GB Memory - 512GB SSD - Midnight",
  "brand": "Apple",
  "model_number": "MDHE4LL/A",
  "upc": "195950690200",
  "link": "https://www.bestbuy.com/product/apple-macbook-air-13-inch-laptop-m5-chip-with-10-core-cpu-and-8-core-gpu-16gb-memory-512gb-ssd-midnight/JJGCQLKXL7/sku/6397846",
  "price": { "current": 1049, "regular": 1299, "savings": 250, "savings_percent": 19, "is_on_sale": true, "deal_label": "Black Friday Deal" },
  "rating": 4.9,
  "reviews_count": 2165,
  "customers_love_points": ["Runs fast and smooth without any lag", "Lightweight build makes it easy to carry", "Battery holds up through days of use"],
  "highlights": [{ "name": "Processor Model", "value": "Apple M5", "level": "Advanced" }],
  "specifications": [
    { "group": "Key Specs", "items": [{ "name": "Screen Size", "value": "13.6 inches" }, { "name": "Screen Resolution", "value": "2560 x 1664" }] }
  ],
  "included_items": ["13-inch MacBook Air", "USB-C to MagSafe 3 Cable (2 m)", "USB-C Power Adapter"],
  "dimensions": { "weight": { "value": 2.7, "unit": "pounds" } },
  "category": { "id": "pcmcat247400050001", "name": "MacBooks" },
  "seller": { "type": "best_buy", "name": "Best Buy" },
  "images": [{ "link": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/776b4db3-e2f4-4412-b45c-99cbef911bd6.jpg", "view": "Front_Zoom" }],
  "videos": [{ "title": "MacBook Air (M5) Overview", "duration_seconds": 53 }],
  "release_date": "2026-03-11"
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Best Buy Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/best-buy-scraper
cd best-buy-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

Run it on your own computer. Best Buy sits behind Akamai and blocks cloud servers and datacenter proxies, so don't set a proxy: your home or office connection is all it needs.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/products/details?product=6397846"
```

```json
{
  "sku": "6397846",
  "bsin": "JJGCQLKXL7",
  "name": "Apple - MacBook Air 13-inch Laptop - M5 chip with 10-core CPU and 8-core GPU - 16GB Memory - 512GB SSD - Midnight",
  "brand": "Apple",
  "model_number": "MDHE4LL/A",
  "upc": "195950690200",
  "condition": "new",
  "price": { "current": 1049, "regular": 1299, "savings": 250, "savings_percent": 19, "is_on_sale": true, "deal_label": "Black Friday Deal" },
  "rating": 4.9,
  "reviews_count": 2177,
  "customers_love_points": ["Runs fast and smooth without any lag", "Stays whisper quiet even under heavy loads"],
  "highlights": [{ "name": "Processor Model", "value": "Apple M5", "level": "Advanced" }],
  "specifications": [{ "group": "Key Specs", "items": [{ "name": "Screen Size", "value": "13.6 inches" }] }],
  "category": { "id": "pcmcat247400050001", "name": "MacBooks" },
  "seller": { "type": "best_buy", "name": "Best Buy" },
  "release_date": "2026-03-11"
}
```

All 15 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

15 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Product Details / Bulk | `/products/details`, `/products/bulk` | The whole product in one call, or up to 20 at once |
| Reviews / Questions | `/products/reviews`, `/products/questions` | Every review and Q&A with ratings, photos and staff answers |
| Offers | `/products/offers` | Every new, open-box and marketplace offer, cheapest first |
| Availability | `/products/availability` | Shipping dates, store pickup and stock at nearby stores |
| Similar Products | `/products/similar` | Comparable products with pros, cons and key specs |
| Autocomplete | `/products/suggestions` | Best Buy's own search suggestions as you type |
| Search Products | `/products/search` | 778K+ products with brand, price, rating and deal filters |
| Category Products / Categories | `/categories/products`, `/categories/list` | Every product of any department, plus the full category tree |
| Deals | `/deals/list` | 133K+ on-sale, clearance and member deals with savings |
| Stores | `/stores/search`, `/stores/details` | Stores near any ZIP with hours, phone and services |
| Seller Details | `/sellers/details` | Marketplace sellers' ratings, star breakdown and recent reviews |


## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-best-buy-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-best-buy-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-best-buy-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/products/details",
    params={"product": "6397846"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Best Buy Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/best-buy-scraper-github-whatsapp)

[![Ask Us by Email about Best Buy Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Best%20Buy%20Scraper%20API&body=I%20need%20help%20using%20the%20Best%20Buy%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/best-buy-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/best-buy-scraper)
