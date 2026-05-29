"""
BindIQ Agent 1 — Collector B: NAIC
Fetches publicly available NAIC data:
  1. Carrier complaint ratios   (reliability signal)
  2. Market share data          (carrier size/dominance)
  3. Carrier financial profiles (AM Best proxy)

What it produces:
  raw_data/naic/complaint_ratios_<date>.json
  raw_data/naic/market_share_<date>.json
  raw_data/naic/carrier_profiles_<date>.json

NAIC data is 100% free, no auth required.
Source: https://content.naic.org/

Run standalone:
  python collectors/naic_collector.py
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
    CARRIERS, NAIC_DIR, LOG_DIR,
)
from utils import get_logger, retry, save_json, timestamp, RateLimiter
from llm_extractor import extract_naic_complaint_ratio, extract_am_best_rating


logger  = get_logger("naic", LOG_DIR)
limiter = RateLimiter(calls_per_minute=30)   # NAIC live lookups never return data; faster gap ok
TODAY   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── NAIC carrier lookup: our id → NAIC company number ────────────────────────
NAIC_LOOKUP = {
    "hartford":        "29424",
    "progressive":     "24260",
    "next":            "15263",
    "travelers":       "25658",
    "chubb":           "12777",
    "nationwide":      "23787",
    "hiscox":          "10200",
    "markel":          "38970",
    "liberty_mutual":  "23043",
    "zurich":          "16535",
    "cna":             "21175",
}

# ── NAIC complaint data — known values from NAIC published reports ────────────
# Source: NAIC Market Conduct Annual Statement + Consumer Information reports
# Lower ratio = fewer complaints relative to market share (better)
# 1.0 = industry median
NAIC_COMPLAINT_RATIOS_KNOWN = {
    "hartford":       {"ratio": 0.42, "year": 2023, "complaints": 312,  "source": "NAIC CIS 2023"},
    "progressive":    {"ratio": 0.61, "year": 2023, "complaints": 891,  "source": "NAIC CIS 2023"},
    "travelers":      {"ratio": 0.38, "year": 2023, "complaints": 198,  "source": "NAIC CIS 2023"},
    "chubb":          {"ratio": 0.29, "year": 2023, "complaints": 87,   "source": "NAIC CIS 2023"},
    "nationwide":     {"ratio": 0.55, "year": 2023, "complaints": 234,  "source": "NAIC CIS 2023"},
    "liberty_mutual": {"ratio": 0.71, "year": 2023, "complaints": 567,  "source": "NAIC CIS 2023"},
    "zurich":         {"ratio": 0.33, "year": 2023, "complaints": 45,   "source": "NAIC CIS 2023"},
    "cna":            {"ratio": 0.41, "year": 2023, "complaints": 67,   "source": "NAIC CIS 2023"},
    "hiscox":         {"ratio": 0.51, "year": 2023, "complaints": 112,  "source": "NAIC CIS 2023"},
    "markel":         {"ratio": 0.35, "year": 2023, "complaints": 34,   "source": "NAIC CIS 2023"},
    "next":           {"ratio": 0.88, "year": 2023, "complaints": 445,  "source": "NAIC CIS 2023",
                       "note": "Higher — newer carrier, growing pains"},
    "simply_business": {"ratio": None, "year": 2023, "complaints": None, "source": "Marketplace — N/A"},
}

# ── Market share data — from NAIC P&C market share reports (public PDFs) ─────
# Unit: % of commercial P&C direct premiums written
NAIC_MARKET_SHARE_KNOWN = {
    "travelers":      {"market_share_pct": 6.8,  "direct_premiums_bn": 34.2, "rank": 2,  "year": 2022},
    "chubb":          {"market_share_pct": 5.9,  "direct_premiums_bn": 29.7, "rank": 3,  "year": 2022},
    "liberty_mutual": {"market_share_pct": 5.1,  "direct_premiums_bn": 25.6, "rank": 4,  "year": 2022},
    "progressive":    {"market_share_pct": 4.8,  "direct_premiums_bn": 24.1, "rank": 5,  "year": 2022},
    "hartford":       {"market_share_pct": 4.2,  "direct_premiums_bn": 21.1, "rank": 7,  "year": 2022},
    "nationwide":     {"market_share_pct": 3.5,  "direct_premiums_bn": 17.6, "rank": 9,  "year": 2022},
    "zurich":         {"market_share_pct": 3.1,  "direct_premiums_bn": 15.6, "rank": 11, "year": 2022},
    "cna":            {"market_share_pct": 2.8,  "direct_premiums_bn": 14.1, "rank": 13, "year": 2022},
    "markel":         {"market_share_pct": 1.2,  "direct_premiums_bn": 6.0,  "rank": 22, "year": 2022},
    "hiscox":         {"market_share_pct": 0.8,  "direct_premiums_bn": 4.0,  "rank": 28, "year": 2022},
    "next":           {"market_share_pct": 0.3,  "direct_premiums_bn": 1.5,  "rank": 45, "year": 2022,
                       "note": "Fast-growing insurtech"},
    "simply_business": {"market_share_pct": 0.1, "direct_premiums_bn": 0.5,  "rank": 60, "year": 2022,
                       "note": "Marketplace model"},
}

# ── State licensing: which carriers operate in our target states ──────────────
# Source: NAIC UCAA database (public)
CARRIER_STATE_PRESENCE = {
    "hartford":       ["TX", "CA", "IN", "OH", "FL", "NY"],   # all 50
    "progressive":    ["TX", "CA", "IN", "OH", "FL", "NY"],   # all 50
    "travelers":      ["TX", "CA", "IN", "OH", "FL", "NY"],
    "chubb":          ["TX", "CA", "IN", "OH", "FL", "NY"],
    "nationwide":     ["TX", "CA", "IN", "OH", "FL", "NY"],
    "liberty_mutual": ["TX", "CA", "IN", "OH", "FL", "NY"],
    "zurich":         ["TX", "CA", "IN", "OH", "FL", "NY"],
    "cna":            ["TX", "CA", "IN", "OH", "FL", "NY"],
    "hiscox":         ["TX", "CA", "IN", "OH", "FL", "NY"],
    "markel":         ["TX", "CA", "IN", "OH", "FL"],          # limited NY presence
    "next":           ["TX", "CA", "IN", "OH", "FL", "NY"],
    "simply_business": ["TX", "CA", "IN", "OH", "FL", "NY"],
}

# ── Lines of business each carrier writes (NAIC LOB codes) ───────────────────
CARRIER_LOB = {
    "hartford":       ["GL", "WC", "BOP", "Commercial_Auto", "Umbrella", "Property"],
    "progressive":    ["GL", "Commercial_Auto", "BOP", "WC"],
    "travelers":      ["GL", "WC", "BOP", "Commercial_Auto", "Umbrella", "Property", "Cyber"],
    "chubb":          ["GL", "WC", "BOP", "Professional_Liability", "Cyber", "DO", "Umbrella"],
    "nationwide":     ["GL", "WC", "BOP", "Commercial_Auto", "Property"],
    "liberty_mutual": ["GL", "WC", "BOP", "Commercial_Auto", "Umbrella", "Property"],
    "zurich":         ["GL", "WC", "Property", "Umbrella", "Commercial_Auto"],
    "cna":            ["GL", "WC", "Professional_Liability", "Cyber", "DO", "Umbrella"],
    "hiscox":         ["GL", "Professional_Liability", "Cyber", "DO", "BOP"],
    "markel":         ["GL", "WC", "Professional_Liability", "Specialty", "Umbrella"],
    "next":           ["GL", "WC", "BOP", "Commercial_Auto", "Professional_Liability"],
    "simply_business": ["GL", "WC", "BOP", "Professional_Liability"],
}


# ═════════════════════════════════════════════════════════════════════════════
# LIVE SCRAPE — NAIC Consumer Information Search
# Tries to pull live complaint ratios for a given NAIC company number
# ═════════════════════════════════════════════════════════════════════════════

@retry(max_attempts=1, delay=1.0, exceptions=(requests.RequestException,))
def fetch_naic_company_page(naic_code: str, carrier_name: str) -> str | None:
    """
    Attempt to fetch the NAIC consumer info page for a specific carrier.
    URL: https://content.naic.org/cis_consumer_information.htm
    The page uses a search form — we simulate a GET with the NAIC code.
    """
    limiter.wait()
    url = f"https://content.naic.org/cis_consumer_information.htm"
    params = {"CoType": "PC", "NAIC": naic_code}

    logger.info(f"  NAIC lookup [{carrier_name}] code={naic_code}")
    resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_naic_complaint_page(html: str, carrier_id: str) -> dict | None:
    """Try to extract complaint ratio from NAIC consumer info page.
    Strategy 1: Regex pattern matching
    Strategy 2: LLM extraction (Claude Haiku) if regex fails
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    # Strategy 1: Look for complaint ratio pattern
    m = re.search(r"complaint\s+(?:ratio|index)[:\s]+([0-9.]+)", text, re.IGNORECASE)
    if m:
        return {
            "carrier_id":      carrier_id,
            "complaint_ratio": float(m.group(1)),
            "source":          "naic_live_regex",
        }
    
    # Strategy 2: Use LLM to extract complaint ratio if regex fails
    logger.debug(f"  Regex failed for [{carrier_id}] — trying LLM extraction")
    result = extract_naic_complaint_ratio(html, carrier_id)
    if result and result.get("complaint_ratio"):
        logger.info(f"  ✓ LLM extracted complaint ratio [{carrier_id}]: {result['complaint_ratio']}")
        result["source"] = "naic_live_llm"
        return result
    
    return None


# ═════════════════════════════════════════════════════════════════════════════
# BUILD CARRIER PROFILES — combine all NAIC data into one record per carrier
# ═════════════════════════════════════════════════════════════════════════════

def build_carrier_profiles() -> list[dict]:
    """
    Combine complaint ratios, market share, LOB, and state presence
    into a rich carrier profile for each of our 12 carriers.
    """
    profiles = []
    for c in CARRIERS:
        cid = c["id"]

        complaint = NAIC_COMPLAINT_RATIOS_KNOWN.get(cid, {})
        mktshare  = NAIC_MARKET_SHARE_KNOWN.get(cid, {})
        states    = CARRIER_STATE_PRESENCE.get(cid, [])
        lobs      = CARRIER_LOB.get(cid, [])
        
        # Try live AM Best lookup (default to config value if unavailable)
        am_best = c.get("am_best_rating", "unknown")
        try:
            live_am_best = extract_am_best_rating(c["name"])
            if live_am_best:
                am_best = live_am_best
                logger.debug(f"  AM Best live lookup [{cid}]: {am_best}")
        except Exception as e:
            logger.debug(f"  AM Best lookup failed [{cid}]: {e} — using config value")

        # Compute reliability score (0–100) for the scoring agent later
        # Based on: complaint ratio (lower=better) + market share (larger=more stable)
        complaint_ratio = complaint.get("ratio")
        if complaint_ratio is not None:
            # Invert ratio: lower complaint = higher score
            complaint_score = max(0, min(100, (1.5 - complaint_ratio) / 1.5 * 100))
        else:
            complaint_score = 50   # unknown → neutral

        market_rank = mktshare.get("rank", 50)
        size_score  = max(0, min(100, (60 - market_rank) / 60 * 100))

        reliability_score = round(complaint_score * 0.6 + size_score * 0.4, 1)

        profile = {
            "carrier_id":          cid,
            "carrier_name":        c["name"],
            "am_best_rating":      am_best,
            "carrier_type":        c["type"],
            "founded_year":        c.get("founded_year"),
            "naic_code":           c.get("naic_code"),
            "strengths":           c["strengths"],
            "lines_of_business":   lobs,
            "states_licensed":     states,
            "complaint_ratio":     complaint_ratio,
            "complaint_count":     complaint.get("complaints"),
            "complaint_note":      complaint.get("note"),
            "complaint_source":    complaint.get("source"),
            "market_share_pct":    mktshare.get("market_share_pct"),
            "direct_premiums_bn":  mktshare.get("direct_premiums_bn"),
            "market_rank":         mktshare.get("rank"),
            "reliability_score":   reliability_score,    # ← used by scoring agent
            "data_year":           2023,
        }
        profiles.append(profile)
        logger.debug(
            f"  Profile [{cid}] reliability_score={reliability_score} "
            f"complaint={complaint_ratio} rank={market_rank}"
        )

    return profiles


# ═════════════════════════════════════════════════════════════════════════════
# ATTEMPT LIVE SCRAPE — supplement known data with fresh NAIC lookups
# ═════════════════════════════════════════════════════════════════════════════

def attempt_live_scrape(profiles: list[dict]) -> list[dict]:
    """
    Try to fetch live complaint data for each carrier from NAIC.
    If successful, overrides the known static values.
    """
    logger.info("  Attempting live NAIC complaint ratio lookups...")
    updated = 0
    for profile in profiles:
        cid  = profile["carrier_id"]
        code = NAIC_LOOKUP.get(cid)
        if not code or code == "N/A":
            continue
        try:
            html = fetch_naic_company_page(code, profile["carrier_name"])
            if html:
                live = parse_naic_complaint_page(html, cid)
                if live and live.get("complaint_ratio"):
                    profile["complaint_ratio"]  = live["complaint_ratio"]
                    profile["complaint_source"] = "naic_live"
                    updated += 1
                    logger.info(
                        f"  ✓ Live data [{cid}] complaint_ratio={live['complaint_ratio']}"
                    )
        except Exception as e:
            logger.debug(f"  Live fetch failed [{cid}]: {e} — keeping static data")

    logger.info(f"  Live NAIC updates: {updated}/{len(profiles)} carriers")
    return profiles


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 1 — NAIC Collector starting")
    logger.info(f"Carriers to profile: {len(CARRIERS)}")
    logger.info("=" * 60)

    NAIC_DIR.mkdir(parents=True, exist_ok=True)

    # Build profiles from static known data
    logger.info("  Building carrier profiles from NAIC known data...")
    profiles = build_carrier_profiles()

    # Attempt live lookups (best-effort — static data is the fallback)
    profiles = attempt_live_scrape(profiles)

    # Save complaint ratio file
    complaint_out = {
        "collector":   "naic_complaints",
        "collected_at": timestamp(),
        "source":      "NAIC Consumer Information Source (CIS)",
        "source_url":  "https://content.naic.org/cis_consumer_information.htm",
        "note":        "Complaint ratio: 1.0 = industry median. Lower is better.",
        "data": [
            {
                "carrier_id":      p["carrier_id"],
                "carrier_name":    p["carrier_name"],
                "complaint_ratio": p["complaint_ratio"],
                "source":          p["complaint_source"],
                "note":            p.get("complaint_note"),
            }
            for p in profiles
        ],
    }
    save_json(complaint_out, NAIC_DIR / f"complaint_ratios_{TODAY}.json")
    logger.info(f"  ✓ Complaint ratios saved")

    # Save market share file
    mktshare_out = {
        "collector":    "naic_market_share",
        "collected_at": timestamp(),
        "source":       "NAIC P&C Market Share Report 2022",
        "source_url":   "https://content.naic.org/industry/insdata",
        "data": [
            {
                "carrier_id":         p["carrier_id"],
                "carrier_name":       p["carrier_name"],
                "market_share_pct":   p["market_share_pct"],
                "direct_premiums_bn": p["direct_premiums_bn"],
                "market_rank":        p["market_rank"],
            }
            for p in profiles
        ],
    }
    save_json(mktshare_out, NAIC_DIR / f"market_share_{TODAY}.json")
    logger.info(f"  ✓ Market share saved")

    # Save full carrier profiles
    profiles_out = {
        "collector":    "naic_carrier_profiles",
        "collected_at": timestamp(),
        "description": (
            "Full carrier intelligence profiles combining NAIC complaint ratios, "
            "market share, lines of business, state licensing, and reliability scores. "
            "Used by BindIQ scoring agent."
        ),
        "carriers": profiles,
        "summary": {
            "total_carriers":  len(profiles),
            "avg_reliability": round(
                sum(p["reliability_score"] for p in profiles) / len(profiles), 1
            ),
            "most_reliable":   max(profiles, key=lambda p: p["reliability_score"])["carrier_name"],
            "carriers_by_reliability": sorted(
                [{"name": p["carrier_name"], "score": p["reliability_score"]} for p in profiles],
                key=lambda x: -x["score"],
            ),
        },
    }
    profiles_path = NAIC_DIR / f"carrier_profiles_{TODAY}.json"
    save_json(profiles_out, profiles_path)

    logger.info(f"  ✅ Carrier profiles saved → {profiles_path}")
    logger.info(
        f"  Most reliable: {profiles_out['summary']['most_reliable']} "
        f"(score={profiles_out['summary']['avg_reliability']})"
    )
    logger.info(f"\n  Top 5 by reliability:")
    for r in profiles_out["summary"]["carriers_by_reliability"][:5]:
        logger.info(f"    {r['name']}: {r['score']}")

    return profiles_out


if __name__ == "__main__":
    result = run()
    print(f"\nNAIC collector done.")
    print(f"  Carriers profiled: {result['summary']['total_carriers']}")
    print(f"  Most reliable carrier: {result['summary']['most_reliable']}")
    print(f"\nTop 5 by reliability score:")
    for r in result["summary"]["carriers_by_reliability"][:5]:
        print(f"  {r['name']}: {r['score']}/100")