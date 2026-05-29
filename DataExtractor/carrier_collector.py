"""
BindIQ Agent 1 — Collector C: Carrier Websites
Scrapes GL pricing info directly from carrier public pages.
Targets: Hartford, Progressive, NEXT, Hiscox, Nationwide

What it produces:
  raw_data/carriers/<carrier>_<date>.json
  raw_data/carriers/all_carriers_<date>.json

Run standalone:
  python collectors/carrier_collector.py
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, SCRAPE_DELAY,
    CARRIERS, CARR_DIR, LOG_DIR,
)
from utils import get_logger, retry, save_json, timestamp, RateLimiter
from llm_extractor import extract_carrier_gl_price


logger  = get_logger("carrier_websites", LOG_DIR)
limiter = RateLimiter(calls_per_minute=6)
TODAY   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Per-carrier scraping config ───────────────────────────────────────────────
# Each entry: what URL to hit, what CSS selectors or regex to look for
CARRIER_SCRAPE_CONFIG = {
    "hartford": {
        "url":   "https://www.thehartford.com/general-liability-insurance",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*(?:a year|per year|annually|/year)",
            r"average[^$]{0,50}\$\s*([\d,]+)",
            r"about\s+\$\s*([\d,]+)",
        ],
        "period": "annual",
    },
    "progressive": {
        "url":   "https://www.progressivecommercial.com/business-insurance/general-liability-insurance/general-liability-insurance-cost/",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)\s*per month",
            r"paid[^$]{0,60}\$\s*([\d,]+)\s*per month",
        ],
        "period": "monthly",
    },
    "next": {
        "url":   "https://www.nextinsurance.com/general-liability-insurance/",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"starting\s+at\s+\$\s*([\d,]+)",
            r"as\s+low\s+as\s+\$\s*([\d,]+)",
            r"average[^$]{0,50}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "hiscox": {
        # hiscox.com blocks scrapers with 403; use US-specific URL as fallback
        "url":   "https://www.hiscox.com/business-insurance/general-liability-insurance-cost",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"starting\s+at\s+\$\s*([\d,]+)",
            r"average[^$]{0,50}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "nationwide": {
        # URL updated — previous path returned 404
        "url":   "https://www.nationwide.com/small-business-insurance/general-liability/",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*(?:per month|a month|/month)",
            r"average[^$]{0,50}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "travelers": {
        "url":   "https://www.travelers.com/business-insurance/general-liability",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
            r"starting\s+at\s+\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "chubb": {
        "url":   "https://www.chubb.com/us-en/business-insurance/general-liability.html",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
            r"typical[^$]{0,60}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "liberty_mutual": {
        "url":   "https://business.libertymutual.com/insurance-products/general-liability/",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
            r"as\s+low\s+as\s+\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "markel": {
        "url":   "https://www.markel.com/insurance-products/specialty-insurance/contractors",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
            r"starting[^$]{0,40}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "zurich": {
        "url":   "https://www.zurichna.com/insurance/commercial/general-liability",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "cna": {
        "url":   "https://www.cna.com/web/guest/cna/business-insurance/general-liability",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
    "simply_business": {
        "url":   "https://www.simplybusiness.com/insurance/general-liability/",
        "price_patterns": [
            r"\$\s*([\d,]+)\s*per month",
            r"average[^$]{0,60}\$\s*([\d,]+)",
            r"as\s+low\s+as\s+\$\s*([\d,]+)",
            r"starting\s+at\s+\$\s*([\d,]+)",
        ],
        "period": "monthly",
    },
}

# ── Industry keywords they mention on their pages (for appetite detection) ────
INDUSTRY_APPETITE_KEYWORDS = {
    "food_service":         ["restaurant", "bakery", "food service", "catering", "food"],
    "construction":         ["contractor", "construction", "builder", "handyman"],
    "manufacturing":        ["manufacturer", "manufacturing", "fabrication"],
    "technology":           ["technology", "software", "IT services", "tech"],
    "healthcare":           ["medical", "healthcare", "clinic", "physician"],
    "logistics_transport":  ["trucking", "delivery", "transportation", "logistics"],
    "real_estate":          ["real estate", "property management", "landlord"],
    "professional_services":["consulting", "professional", "accountant", "lawyer"],
    "retail":               ["retail", "store", "shop", "merchandise"],
    "cleaning_services":    ["cleaning", "janitorial", "maid service"],
    "landscaping":          ["landscaping", "lawn", "grounds maintenance"],
    "food_manufacturing":   ["food processing", "food manufacturing", "food production"],
}


# ═════════════════════════════════════════════════════════════════════════════
# FETCH + PARSE
# ═════════════════════════════════════════════════════════════════════════════

@retry(max_attempts=MAX_RETRIES, delay=3.0, exceptions=(requests.RequestException,))
def fetch_carrier_page(url: str, carrier_id: str) -> str | None:
    limiter.wait()
    logger.info(f"  Fetching [{carrier_id}] → {url}")
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def extract_price(html: str, patterns: list[str], period: str) -> dict | None:
    """
    Try each regex pattern to find a price in the page text.
    Returns normalized monthly + annual rates.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(",", ""))
            if val < 5 or val > 100_000:
                continue   # sanity filter
            if period == "monthly":
                return {"monthly_rate": val, "annual_rate": round(val * 12, 2)}
            else:  # annual
                return {"monthly_rate": round(val / 12, 2), "annual_rate": val}
    return None


def detect_industry_appetite(html: str) -> list[str]:
    """
    Detect which industries the carrier explicitly mentions as served.
    Returns list of our industry ids.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()

    matched = []
    for industry_id, keywords in INDUSTRY_APPETITE_KEYWORDS.items():
        if any(kw.lower() in text for kw in keywords):
            matched.append(industry_id)
    return matched


def extract_coverage_types(html: str) -> list[str]:
    """Detect what coverage types the carrier advertises on the page."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True).lower()

    coverage_map = {
        "GL":                   ["general liability"],
        "BOP":                  ["business owner", "bop"],
        "WC":                   ["workers comp", "workers' comp"],
        "Professional_Liability": ["professional liability", "errors and omissions", "e&o"],
        "Cyber":                ["cyber", "data breach"],
        "Commercial_Auto":      ["commercial auto", "business auto"],
        "Umbrella":             ["umbrella"],
        "Property":             ["commercial property", "business property"],
        "Product_Liability":    ["product liability"],
    }
    found = []
    for cov, keywords in coverage_map.items():
        if any(kw in text for kw in keywords):
            found.append(cov)
    return found


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 1 — Carrier Website Collector starting")
    logger.info(f"Carriers to scrape: {len(CARRIER_SCRAPE_CONFIG)}")
    logger.info("=" * 60)

    CARR_DIR.mkdir(parents=True, exist_ok=True)

    carrier_name_map = {c["id"]: c["name"] for c in CARRIERS}

    results = []
    for cid, cfg in CARRIER_SCRAPE_CONFIG.items():
        carrier_name = carrier_name_map.get(cid, cid)
        logger.info(f"\n  Processing carrier: {cid}")

        result = {
            "carrier_id":       cid,
            "url":              cfg["url"],
            "collected_at":     timestamp(),
            "gl_pricing":       None,
            "industry_appetite": [],
            "coverage_types":   [],
            "scrape_status":    "pending",
        }

        html = None
        try:
            html = fetch_carrier_page(cfg["url"], cid)
        except Exception as e:
            logger.warning(f"  ✗ Failed to fetch {cid}: {e}")
            result["scrape_status"] = f"failed: {str(e)[:80]}"

        if html:
            # Save raw HTML
            raw_path = CARR_DIR / f"{cid}_{TODAY}.html"
            raw_path.write_text(html, encoding="utf-8")

            # Extract pricing — regex first, LLM fallback if nothing found
            pricing = extract_price(html, cfg["price_patterns"], cfg["period"])
            if not pricing:
                logger.info(f"  Regex found no price for [{cid}] — trying LLM extraction")
                pricing = extract_carrier_gl_price(html, cid, carrier_name)

            if pricing:
                result["gl_pricing"]    = pricing
                result["scrape_status"] = "success"
                logger.info(
                    f"  ✓ [{cid}] GL: ${pricing['monthly_rate']}/mo "
                    f"(${pricing['annual_rate']}/yr)"
                )
            else:
                result["scrape_status"] = "no_price_found"
                logger.warning(f"  ⚠ [{cid}] No price found in page (regex + LLM both failed)")

            # Industry appetite
            result["industry_appetite"] = detect_industry_appetite(html)
            result["coverage_types"]    = extract_coverage_types(html)
            logger.debug(
                f"  Industries mentioned: {result['industry_appetite']}"
            )

        # Save per-carrier
        save_json(result, CARR_DIR / f"{cid}_{TODAY}.json")
        results.append(result)

    # Summary
    successful = [r for r in results if r["scrape_status"] == "success"]
    output = {
        "collector":     "carrier_websites",
        "collected_at":  timestamp(),
        "carriers":      results,
        "summary": {
            "total":      len(results),
            "successful": len(successful),
            "failed":     len(results) - len(successful),
            "pricing_found": [
                {
                    "carrier_id":    r["carrier_id"],
                    "monthly_rate":  r["gl_pricing"]["monthly_rate"],
                    "annual_rate":   r["gl_pricing"]["annual_rate"],
                }
                for r in successful if r.get("gl_pricing")
            ],
        },
    }

    out_path = CARR_DIR / f"all_carriers_{TODAY}.json"
    save_json(output, out_path)
    logger.info(f"\n  ✅ All carrier data saved → {out_path}")
    logger.info(
        f"  Successful: {output['summary']['successful']}/{output['summary']['total']}"
    )

    return output


if __name__ == "__main__":
    result = run()
    print(f"\nCarrier website collector done.")
    print(f"  Scraped: {result['summary']['successful']}/{result['summary']['total']}")
    print("\nPricing found:")
    for p in result["summary"]["pricing_found"]:
        print(f"  {p['carrier_id']}: ${p['monthly_rate']}/mo (${p['annual_rate']}/yr)")