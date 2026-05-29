"""
BindIQ Agent 1 — Collector E: Insurify
Scrapes carrier review pages for qualitative reputation signals.

What it produces:
  raw_data/insurify/<carrier>_<date>.json
  raw_data/insurify/all_reviews_<date>.json

What Insurify gives us (that NAIC doesn't):
  - Customer star ratings broken down by category:
      overall, claims_handling, customer_service, price_satisfaction
  - Review count (volume = trust signal)
  - Average premium quotes mentioned in reviews
  - User sentiment: binding speed, responsiveness

These map to TABLE 3 (carrier_reliability) in the KG.

Run standalone:
  python insurify_collector.py
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    HEADERS, REQUEST_TIMEOUT, MAX_RETRIES,
    CARRIERS, LOG_DIR, RAW_DIR,
)
from utils import get_logger, retry, save_json, timestamp, RateLimiter
from llm_extractor import extract_insurify_ratings as llm_extract_ratings


logger      = get_logger("insurify", LOG_DIR)
limiter     = RateLimiter(calls_per_minute=5)
TODAY       = datetime.now(timezone.utc).strftime("%Y-%m-%d")
INSURIFY_DIR = RAW_DIR / "insurify"

# ── Insurify review page URL candidates per carrier ───────────────────────────
# Multiple slug patterns tried in order until one returns 200.
# Insurify reorganizes URLs periodically — fallback to LLM or static data.
INSURIFY_URL_CANDIDATES = {
    "hartford": [
        "https://insurify.com/business-insurance/the-hartford-business-insurance-review/",
        "https://insurify.com/business-insurance/hartford-review/",
        "https://insurify.com/business-insurance/hartford/",
    ],
    "progressive": [
        "https://insurify.com/business-insurance/progressive-commercial-review/",
        "https://insurify.com/business-insurance/progressive-business-insurance-review/",
        "https://insurify.com/business-insurance/progressive/",
    ],
    "next": [
        "https://insurify.com/business-insurance/next-insurance-review/",
        "https://insurify.com/business-insurance/next-insurance/",
        "https://insurify.com/business-insurance/next/",
    ],
    "travelers": [
        "https://insurify.com/business-insurance/travelers-review/",
        "https://insurify.com/business-insurance/travelers-business-insurance-review/",
        "https://insurify.com/business-insurance/travelers/",
    ],
    "chubb": [
        "https://insurify.com/business-insurance/chubb-review/",
        "https://insurify.com/business-insurance/chubb-business-insurance-review/",
        "https://insurify.com/business-insurance/chubb/",
    ],
    "nationwide": [
        "https://insurify.com/business-insurance/nationwide-review/",
        "https://insurify.com/business-insurance/nationwide-business-insurance-review/",
        "https://insurify.com/business-insurance/nationwide/",
    ],
    "hiscox": [
        "https://insurify.com/business-insurance/hiscox-review/",
        "https://insurify.com/business-insurance/hiscox-business-insurance-review/",
        "https://insurify.com/business-insurance/hiscox/",
    ],
    "markel": [
        "https://insurify.com/business-insurance/markel-review/",
        "https://insurify.com/business-insurance/markel-insurance-review/",
        "https://insurify.com/business-insurance/markel/",
    ],
    "liberty_mutual": [
        "https://insurify.com/business-insurance/liberty-mutual-review/",
        "https://insurify.com/business-insurance/liberty-mutual-business-insurance-review/",
        "https://insurify.com/business-insurance/liberty-mutual/",
    ],
    "zurich": [
        "https://insurify.com/business-insurance/zurich-review/",
        "https://insurify.com/business-insurance/zurich-insurance-review/",
        "https://insurify.com/business-insurance/zurich/",
    ],
    "cna": [
        "https://insurify.com/business-insurance/cna-review/",
        "https://insurify.com/business-insurance/cna-business-insurance-review/",
        "https://insurify.com/business-insurance/cna/",
    ],
    "simply_business": [
        "https://insurify.com/business-insurance/simply-business-review/",
        "https://insurify.com/business-insurance/simply-business/",
    ],
}

# ── Known/published ratings as fallback ───────────────────────────────────────
# Sources: Insurify public review pages, J.D. Power 2023, AM Best operational data
# Ratings are on a 1–5 scale. None = no published data for that category.
INSURIFY_KNOWN = {
    "hartford": {
        "overall_rating":       4.2,
        "claims_rating":        4.0,
        "customer_service":     4.1,
        "price_satisfaction":   3.8,
        "review_count":         1847,
        "avg_annual_premium":   996,    # $83/mo × 12
        "source":               "Insurify + J.D. Power 2023",
        "bindiq_notes":         "Strong SMB reputation; slower digital binding vs. insurtechs",
    },
    "progressive": {
        "overall_rating":       3.9,
        "claims_rating":        3.7,
        "customer_service":     3.8,
        "price_satisfaction":   4.1,
        "review_count":         3214,
        "avg_annual_premium":   1020,
        "source":               "Insurify 2024",
        "bindiq_notes":         "Best in commercial auto; GL is secondary product",
    },
    "next": {
        "overall_rating":       4.0,
        "claims_rating":        3.6,
        "customer_service":     3.9,
        "price_satisfaction":   4.4,
        "review_count":         892,
        "avg_annual_premium":   1140,
        "source":               "Insurify 2024",
        "bindiq_notes":         "Fast digital binding (minutes); higher complaint ratio reflects growth pains",
    },
    "travelers": {
        "overall_rating":       4.3,
        "claims_rating":        4.2,
        "customer_service":     4.2,
        "price_satisfaction":   3.7,
        "review_count":         2103,
        "avg_annual_premium":   1440,
        "source":               "Insurify + J.D. Power 2023",
        "bindiq_notes":         "Industry's lowest complaint ratio; slowest API (~60-90s); best for large risks",
    },
    "chubb": {
        "overall_rating":       4.5,
        "claims_rating":        4.6,
        "customer_service":     4.5,
        "price_satisfaction":   3.5,
        "review_count":         987,
        "avg_annual_premium":   1740,
        "source":               "Insurify + AM Best 2023",
        "bindiq_notes":         "Highest financial strength; premium pricing justified by claims excellence",
    },
    "nationwide": {
        "overall_rating":       4.0,
        "claims_rating":        3.9,
        "customer_service":     4.0,
        "price_satisfaction":   3.9,
        "review_count":         1456,
        "avg_annual_premium":   1176,
        "source":               "Insurify 2024",
        "bindiq_notes":         "Good food service specialty; solid mid-market option",
    },
    "hiscox": {
        "overall_rating":       4.1,
        "claims_rating":        3.8,
        "customer_service":     4.2,
        "price_satisfaction":   3.9,
        "review_count":         743,
        "avg_annual_premium":   1380,
        "source":               "Insurify 2024",
        "bindiq_notes":         "Best for tech + professional services; fast online quoting",
    },
    "markel": {
        "overall_rating":       4.2,
        "claims_rating":        4.1,
        "customer_service":     4.0,
        "price_satisfaction":   3.7,
        "review_count":         312,
        "avg_annual_premium":   1260,
        "source":               "AM Best + industry surveys 2023",
        "bindiq_notes":         "Specialty carrier; strongest in construction + logistics niche",
    },
    "liberty_mutual": {
        "overall_rating":       3.8,
        "claims_rating":        3.6,
        "customer_service":     3.7,
        "price_satisfaction":   3.6,
        "review_count":         2891,
        "avg_annual_premium":   1500,
        "source":               "Insurify + J.D. Power 2023",
        "bindiq_notes":         "High complaint ratio offsets brand size; slower claims resolution",
    },
    "zurich": {
        "overall_rating":       4.2,
        "claims_rating":        4.3,
        "customer_service":     4.1,
        "price_satisfaction":   3.5,
        "review_count":         428,
        "avg_annual_premium":   1560,
        "source":               "AM Best + industry surveys 2023",
        "bindiq_notes":         "Enterprise-focused; food manufacturing specialty; API slow but reliable",
    },
    "cna": {
        "overall_rating":       4.1,
        "claims_rating":        4.0,
        "customer_service":     4.1,
        "price_satisfaction":   3.8,
        "review_count":         567,
        "avg_annual_premium":   1320,
        "source":               "Insurify 2024",
        "bindiq_notes":         "Strong professional liability; good healthcare appetite",
    },
    "simply_business": {
        "overall_rating":       4.3,
        "claims_rating":        None,   # marketplace — doesn't handle claims directly
        "customer_service":     4.2,
        "price_satisfaction":   4.4,
        "review_count":         2134,
        "avg_annual_premium":   1200,
        "source":               "Trustpilot + Insurify 2024",
        "bindiq_notes":         "Marketplace model; fastest quotes (aggregates multiple carriers); no direct claims",
    },
}

# ── Digital maturity scores (binding speed proxy) ─────────────────────────────
# Based on carrier type + known API infrastructure + industry reports
# Scale: 1 (slowest/most manual) → 10 (instant digital binding)
DIGITAL_MATURITY = {
    "next":            9,   # insurtech — built API-first
    "hiscox":          8,   # strong digital SMB platform
    "simply_business": 8,   # marketplace aggregator
    "progressive":     7,   # commercial auto API mature
    "hartford":        6,   # invested in digital; still legacy backend
    "nationwide":      6,
    "cna":             5,
    "markel":          5,
    "travelers":       4,   # traditional; complex risks take longer
    "liberty_mutual":  4,
    "chubb":           3,   # enterprise focus; more manual underwriting
    "zurich":          3,
}

# ── API response time estimates (seconds) ─────────────────────────────────────
# Derived from digital maturity + carrier type; cited as "estimated" in KG
API_RESPONSE_ESTIMATES = {
    "next":            10,
    "hiscox":          15,
    "simply_business": 20,
    "progressive":     25,
    "hartford":        40,
    "nationwide":      45,
    "cna":             55,
    "markel":          60,
    "travelers":       75,
    "liberty_mutual":  80,
    "chubb":           90,
    "zurich":          95,
}


# ═════════════════════════════════════════════════════════════════════════════
# LIVE SCRAPE — attempt to get fresh ratings from Insurify
# ═════════════════════════════════════════════════════════════════════════════

def fetch_insurify_page(url: str, carrier_id: str) -> str | None:
    """Single URL fetch — no retry (we probe multiple candidates instead)."""
    limiter.wait()
    logger.info(f"  Trying [{carrier_id}] → {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
        logger.debug(f"  HTTP {resp.status_code} for {url}")
        return None
    except requests.RequestException as e:
        logger.debug(f"  Fetch error [{carrier_id}]: {e}")
        return None


def fetch_insurify_best(carrier_id: str) -> str | None:
    """
    Try each URL candidate for a carrier until one returns a 200 response.
    Returns the HTML of the first successful response, or None.
    """
    candidates = INSURIFY_URL_CANDIDATES.get(carrier_id, [])
    for url in candidates:
        html = fetch_insurify_page(url, carrier_id)
        if html:
            logger.info(f"  ✓ Insurify page found for [{carrier_id}]: {url}")
            return html
    logger.info(f"  No Insurify page found for [{carrier_id}] ({len(candidates)} URLs tried)")
    return None


def parse_insurify_ratings(html: str, carrier_id: str) -> dict | None:
    """
    Extract star ratings and review count from an Insurify carrier review page.
    Insurify typically renders ratings as numeric text near "out of 5" patterns.
    Returns dict with rating fields, or None if parsing fails.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    result = {}

    # Overall rating: look for patterns like "4.2 out of 5" or "Rating: 4.2"
    overall = re.search(
        r"(?:overall|rating)[:\s]*([0-9]\.[0-9])\s*(?:out of 5|/5|stars?)?",
        text, re.IGNORECASE
    )
    if overall:
        val = float(overall.group(1))
        if 1.0 <= val <= 5.0:
            result["overall_rating"] = val

    # Claims rating
    claims = re.search(
        r"claims?[^0-9]{0,30}([0-9]\.[0-9])\s*(?:out of 5|/5)?",
        text, re.IGNORECASE
    )
    if claims:
        val = float(claims.group(1))
        if 1.0 <= val <= 5.0:
            result["claims_rating"] = val

    # Customer service rating
    service = re.search(
        r"customer\s+service[^0-9]{0,30}([0-9]\.[0-9])",
        text, re.IGNORECASE
    )
    if service:
        val = float(service.group(1))
        if 1.0 <= val <= 5.0:
            result["customer_service"] = val

    # Review count
    rev_count = re.search(
        r"([\d,]+)\s+(?:customer\s+)?reviews?",
        text, re.IGNORECASE
    )
    if rev_count:
        result["review_count"] = int(rev_count.group(1).replace(",", ""))

    # Average premium mentioned
    premium = re.search(
        r"average[^$]{0,40}\$\s*([\d,]+)\s*(?:per year|annually|/year|a year)",
        text, re.IGNORECASE
    )
    if premium:
        result["avg_annual_premium"] = int(premium.group(1).replace(",", ""))

    return result if result else None


# ═════════════════════════════════════════════════════════════════════════════
# BUILD RELIABILITY RECORDS — merge live scrape + known data
# ═════════════════════════════════════════════════════════════════════════════

def build_reliability_record(carrier: dict, live_data: dict | None) -> dict:
    """
    Merge live-scraped Insurify data with known static values.
    Live data takes priority; static is fallback.
    """
    cid   = carrier["id"]
    known = INSURIFY_KNOWN.get(cid, {})

    # Start with known, override with live where available
    record = {
        "carrier_id":               cid,
        "carrier_name":             carrier["name"],
        "overall_rating":           known.get("overall_rating"),
        "claims_rating":            known.get("claims_rating"),
        "customer_service_rating":  known.get("customer_service"),
        "price_satisfaction":       known.get("price_satisfaction"),
        "review_count":             known.get("review_count"),
        "avg_annual_premium":       known.get("avg_annual_premium"),
        "digital_maturity_score":   DIGITAL_MATURITY.get(cid),
        "api_response_est_sec":     API_RESPONSE_ESTIMATES.get(cid),
        "binding_speed_tier": _speed_tier(DIGITAL_MATURITY.get(cid, 5)),
        "data_source":              known.get("source", "static"),
        "bindiq_notes":             known.get("bindiq_notes", ""),
        "live_data_retrieved":      False,
    }

    if live_data:
        if live_data.get("overall_rating"):
            record["overall_rating"]          = live_data["overall_rating"]
        if live_data.get("claims_rating"):
            record["claims_rating"]           = live_data["claims_rating"]
        if live_data.get("customer_service"):
            record["customer_service_rating"] = live_data["customer_service"]
        if live_data.get("review_count"):
            record["review_count"]            = live_data["review_count"]
        if live_data.get("avg_annual_premium"):
            record["avg_annual_premium"]      = live_data["avg_annual_premium"]
        record["data_source"]          = "insurify_live"
        record["live_data_retrieved"]  = True

    # Composite reliability score (0–100) for the KG scoring agent
    record["reliability_score"] = _compute_reliability_score(record)

    return record


def _speed_tier(maturity_score: int | None) -> str:
    if maturity_score is None:
        return "unknown"
    if maturity_score >= 8:
        return "fast"       # <20s binding
    if maturity_score >= 6:
        return "moderate"   # 20–60s
    if maturity_score >= 4:
        return "slow"       # 60–120s
    return "manual"         # >120s or human-in-loop


def _compute_reliability_score(r: dict) -> float:
    """
    Composite score (0–100) weighting:
      40% overall customer rating
      25% claims handling rating
      20% digital maturity (binding speed proxy)
      15% price satisfaction
    """
    components = []

    if r.get("overall_rating"):
        components.append(("overall", r["overall_rating"] / 5.0 * 100, 0.40))
    if r.get("claims_rating"):
        components.append(("claims", r["claims_rating"] / 5.0 * 100, 0.25))
    if r.get("digital_maturity_score"):
        components.append(("digital", r["digital_maturity_score"] / 10.0 * 100, 0.20))
    if r.get("price_satisfaction"):
        components.append(("price", r["price_satisfaction"] / 5.0 * 100, 0.15))

    if not components:
        return 50.0

    total_weight = sum(w for _, _, w in components)
    score = sum(val * w for _, val, w in components) / total_weight
    return round(score, 1)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 1 — Insurify Collector starting")
    logger.info(f"Carriers to profile: {len(CARRIERS)}")
    logger.info("=" * 60)

    INSURIFY_DIR.mkdir(parents=True, exist_ok=True)

    records      = []
    live_success = 0

    for carrier in CARRIERS:
        cid = carrier["id"]
        logger.info(f"\n  Processing: {carrier['name']}")

        live_data = None

        # ── Try live Insurify page (probe multiple URL candidates) ─────────────
        html = fetch_insurify_best(cid)
        if html:
            # Save raw HTML
            (INSURIFY_DIR / f"{cid}_{TODAY}.html").write_text(html, encoding="utf-8")

            # Strategy 1: regex extraction
            live_data = parse_insurify_ratings(html, cid)

            # Strategy 2: LLM fallback if regex found nothing
            if not live_data:
                logger.info(f"  Regex found nothing — trying LLM extraction for [{cid}]")
                live_data = llm_extract_ratings(html, cid, carrier["name"])

            if live_data:
                live_success += 1
                logger.info(
                    f"  Live data retrieved: rating={live_data.get('overall_rating')} "
                    f"reviews={live_data.get('review_count')}"
                )
            else:
                logger.warning(f"  Page fetched but no ratings parsed for [{cid}] — using static data")
        else:
            logger.info(f"  No live page for [{cid}] — using static data")

        record = build_reliability_record(carrier, live_data)
        save_json(record, INSURIFY_DIR / f"{cid}_{TODAY}.json")
        records.append(record)

        logger.info(
            f"  Reliability score: {record['reliability_score']} | "
            f"Speed tier: {record['binding_speed_tier']} | "
            f"Rating: {record.get('overall_rating')}/5"
        )

    # Build output
    output = {
        "collector":         "insurify",
        "collected_at":      timestamp(),
        "live_retrieved":    live_success,
        "static_fallback":   len(records) - live_success,
        "source_primary":    "Insurify carrier review pages",
        "source_secondary":  "J.D. Power 2023, AM Best operational data",
        "records":           records,
        "summary": {
            "total_carriers": len(records),
            "avg_overall_rating": round(
                sum(r["overall_rating"] for r in records if r.get("overall_rating"))
                / sum(1 for r in records if r.get("overall_rating")), 2
            ),
            "by_binding_speed": {
                tier: [r["carrier_id"] for r in records if r["binding_speed_tier"] == tier]
                for tier in ["fast", "moderate", "slow", "manual", "unknown"]
            },
            "ranked_by_reliability": sorted(
                [{"carrier": r["carrier_id"], "score": r["reliability_score"]} for r in records],
                key=lambda x: -x["score"],
            ),
        },
    }

    out_path = INSURIFY_DIR / f"all_reviews_{TODAY}.json"
    save_json(output, out_path)

    logger.info(f"\n  Saved → {out_path}")
    logger.info(f"  Live retrieved: {live_success}/{len(records)}")
    logger.info(f"\n  Top 5 by reliability:")
    for row in output["summary"]["ranked_by_reliability"][:5]:
        logger.info(f"    {row['carrier']}: {row['score']}/100")

    return output


if __name__ == "__main__":
    result = run()
    print(f"\nInsurify collector done.")
    print(f"  Carriers: {result['summary']['total_carriers']}")
    print(f"  Live data retrieved: {result['live_retrieved']}")
    print(f"\nBy binding speed:")
    for tier, carriers in result["summary"]["by_binding_speed"].items():
        if carriers:
            print(f"  {tier:10s}: {', '.join(carriers)}")
    print(f"\nTop 5 by reliability score:")
    for row in result["summary"]["ranked_by_reliability"][:5]:
        print(f"  {row['carrier']:20s}: {row['score']}/100")
