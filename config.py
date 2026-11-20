"""Configuration for the Best Buy Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT             port the API listens on (default 8000)
    BEST_BUY_PROXY   proxy URL for every request (default: none, requests go out
                     from your own connection). Best Buy sits behind Akamai Bot
                     Manager and blocks cloud servers and datacenter proxies:
                     run the scraper from your own home or office connection
                     and leave this unset.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

BEST_BUY_PROXY = os.environ.get("BEST_BUY_PROXY") or None


# ---- what the best_buy module reads: defaults for a direct run ----
BEST_BUY_BLOCK_HOLD_S = 900
BEST_BUY_DEFAULT_ZIP = '10001'
BEST_BUY_EXIT = BEST_BUY_PROXY      # the exit every session leaves through: your proxy, or direct
BEST_BUY_FALLBACK_EXITS = None
MAX_RETRIES = 3
RETRY_BACKOFF = 2
