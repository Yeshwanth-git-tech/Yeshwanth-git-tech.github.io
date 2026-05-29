"""
BindIQ Agent 1 — Master Orchestrator
Runs all collectors in the correct sequence and merges output into
the 5 KG-ready tables that Agent 2 (KG Builder) will consume.

Collection order (fastest/most reliable first):
  1. NAIC         — structured static data, no scraping risk
  2. Carrier sites — Hartford/Progressive published averages
  3. Insurify      — carrier reputation + ratings
  4. MoneyGeek    — pricing benchmarks (JS-heavy, longest)
  5. Herald        — synthetic quotes (or live sandbox if key set)

Output (raw_data/output/):
  kg_table_1_carrier_identity.json
  kg_table_2_pricing_benchmarks.json
  kg_table_3_reliability.json
  kg_table_4_appetite.json
  kg_table_5_state_presence.json
  kg_master_<date>.json            ← all 5 tables in one file for Agent 2

Run:
  python run_all.py
  python run_all.py --skip-scraping    # use only static data (fast, no network)
  python run_all.py --collector naic   # run a single collector
"""

import argparse
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CARRIERS, INDUSTRIES, TARGET_STATES, OUT_DIR, LOG_DIR, PUBLISHED_BENCHMARKS
from utils import get_logger, save_json, load_json, timestamp

logger = get_logger("run_all", LOG_DIR)
TODAY  = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — RUN ALL COLLECTORS
# ═════════════════════════════════════════════════════════════════════════════

def run_collector(name: str, module_path: str) -> dict | None:
    """Import and run a collector module. Returns its output dict or None on failure."""
    logger.info(f"\n{'='*60}")
    logger.info(f"  Running collector: {name.upper()}")
    logger.info(f"{'='*60}")
    try:
        import importlib.util
        spec   = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.run()
    except Exception as e:
        logger.error(f"  COLLECTOR FAILED [{name}]: {e}")
        logger.debug(traceback.format_exc())
        return None


def collect_all(skip_scraping: bool = False, only: str | None = None) -> dict:
    """Run all collectors and return their raw outputs."""
    base = Path(__file__).parent

    collectors = [
        ("naic",     base / "naic_collector.py"),
        ("carriers", base / "carrier_collector.py"),
        ("insurify", base / "insurify_collector.py"),
        ("moneygeek",base / "moneygeek_collector.py"),
        ("herald",   base / "herald_collector.py"),
    ]

    # Skip scraping-heavy collectors if --skip-scraping flag set
    scraping_heavy = {"moneygeek", "carriers", "insurify"}

    raw = {}
    for name, path in collectors:
        if only and name != only:
            continue
        if skip_scraping and name in scraping_heavy:
            logger.info(f"  Skipping [{name}] (--skip-scraping mode)")
            continue
        result = run_collector(name, str(path))
        raw[name] = result

    return raw


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — BUILD THE 5 KG TABLES
# ═════════════════════════════════════════════════════════════════════════════

def build_table_1_identity(raw: dict) -> list[dict]:
    """
    TABLE 1: carrier_identity
    Who are the carriers? identity, ratings, size, legitimacy.
    Primary: config.py CARRIERS + NAIC market share
    """
    naic_data = raw.get("naic", {})
    naic_profiles = {
        p["carrier_id"]: p
        for p in naic_data.get("carriers", [])
    } if naic_data else {}

    rows = []
    for c in CARRIERS:
        cid  = c["id"]
        naic = naic_profiles.get(cid, {})

        rows.append({
            "carrier_id":         cid,
            "name":               c["name"],
            "am_best_rating":     c["am_best_rating"],
            "carrier_type":       c["type"],
            "founded_year":       c.get("founded_year"),
            "naic_code":          c.get("naic_code"),
            "market_rank":        naic.get("market_rank"),
            "market_share_pct":   naic.get("market_share_pct"),
            "direct_premiums_bn": naic.get("direct_premiums_bn"),
            "lines_of_business":  naic.get("lines_of_business", []),
            "core_strengths":     c.get("strengths", []),
            "data_year":          2023,
        })

    logger.info(f"  TABLE 1 (identity): {len(rows)} carriers")
    return rows


def build_table_2_pricing(raw: dict) -> list[dict]:
    """
    TABLE 2: carrier_pricing_benchmarks
    What do they charge? pricing by industry × state × coverage limit.
    Primary: MoneyGeek scraped rates
    Supplement: carrier website published averages
    Supplement: Herald synthetic quotes for industry × state × limit combos
    """
    rows = []

    # ── MoneyGeek carrier rates (general, by state) ───────────────────────────
    mg_data  = raw.get("moneygeek", {})
    mg_rates = mg_data.get("carrier_rates", []) if mg_data else []
    for r in mg_rates:
        rows.append({
            "carrier_id":   r.get("carrier_id"),
            "industry":     "general",        # MoneyGeek state pages are cross-industry averages
            "state":        r.get("state", "national"),
            "gl_limit":     1_000_000,         # standard GL limit
            "monthly_avg":  r.get("monthly_rate"),
            "annual_avg":   r.get("annual_rate"),
            "source":       r.get("source", "moneygeek"),
            "source_label": r.get("source_label"),
            "confidence":   "high" if "live" in r.get("source", "") else "medium",
        })

    # ── MoneyGeek industry-level rows ─────────────────────────────────────────
    mg_industry = mg_data.get("industry_rates", []) if mg_data else []
    for r in mg_industry:
        rows.append({
            "carrier_id":   r.get("carrier_id"),
            "industry":     r.get("industry_keyword", "general"),
            "state":        r.get("state", "national"),
            "gl_limit":     1_000_000,
            "monthly_avg":  r.get("monthly_rate"),
            "annual_avg":   r.get("annual_rate"),
            "source":       r.get("source", "moneygeek_industry"),
            "confidence":   "high",
        })

    # ── Carrier website published averages (Hartford, Progressive, etc.) ──────
    carr_data = raw.get("carriers", {})
    if carr_data:
        for c in carr_data.get("carriers", []):
            pricing = c.get("gl_pricing")
            if pricing:
                rows.append({
                    "carrier_id":   c["carrier_id"],
                    "industry":     "general",
                    "state":        "national",
                    "gl_limit":     1_000_000,
                    "monthly_avg":  pricing.get("monthly_rate"),
                    "annual_avg":   pricing.get("annual_rate"),
                    "source":       "carrier_website",
                    "source_url":   c.get("url"),
                    "confidence":   "high",  # self-reported but citable
                })

    # ── Fallback: published benchmarks from config if no live data ────────────
    if not rows:
        logger.warning("  No live pricing data — using published benchmarks as fallback")
        for cid, bench in PUBLISHED_BENCHMARKS.items():
            rows.append({
                "carrier_id":  cid,
                "industry":    "general",
                "state":       "national",
                "gl_limit":    1_000_000,
                "monthly_avg": bench["gl_monthly_avg"],
                "annual_avg":  bench["gl_monthly_avg"] * 12,
                "source":      bench["source"],
                "confidence":  "medium",
            })

    # ── Herald synthetic quote pricing (industry × state × limit granular) ────
    herald_data = raw.get("herald", {})
    if herald_data:
        for qs in herald_data.get("quote_sets", []):
            profile = qs.get("profile", {})
            quotes  = qs.get("quotes", [])
            for q in quotes:
                rows.append({
                    "carrier_id":   q.get("carrier_id"),
                    "industry":     profile.get("industry_id"),
                    "state":        profile.get("state"),
                    "gl_limit":     profile.get("gl_limit"),
                    "monthly_avg":  q.get("monthly_premium"),
                    "annual_avg":   q.get("annual_premium"),
                    "source":       "herald_" + qs.get("source", "synthetic"),
                    "profile_label": profile.get("label"),
                    "confidence":   "high" if qs.get("source") == "herald_live" else "medium",
                    "multipliers":  q.get("multipliers_applied"),
                })

    # Filter out rows with no pricing
    rows = [r for r in rows if r.get("monthly_avg") or r.get("annual_avg")]
    logger.info(f"  TABLE 2 (pricing): {len(rows)} rows")
    return rows


def build_table_3_reliability(raw: dict) -> list[dict]:
    """
    TABLE 3: carrier_reliability
    How reliable are they? complaints, binding speed, customer ratings.
    Primary: Insurify reviews
    Supplement: NAIC complaint ratios
    """
    # Load Insurify reliability records
    insurify_data = raw.get("insurify", {})
    insurify_map  = {
        r["carrier_id"]: r
        for r in (insurify_data.get("records", []) if insurify_data else [])
    }

    # Load NAIC complaint ratios
    naic_data     = raw.get("naic", {})
    naic_map      = {
        p["carrier_id"]: p
        for p in (naic_data.get("carriers", []) if naic_data else [])
    }

    rows = []
    for c in CARRIERS:
        cid    = c["id"]
        ins    = insurify_map.get(cid, {})
        naic   = naic_map.get(cid, {})

        # NAIC reliability score (from naic_collector computation)
        naic_reliability = naic.get("reliability_score")
        # Insurify reliability score
        ins_reliability  = ins.get("reliability_score")

        # Blend scores: 60% NAIC (authoritative), 40% Insurify (customer perception)
        if naic_reliability is not None and ins_reliability is not None:
            blended = round(naic_reliability * 0.6 + ins_reliability * 0.4, 1)
        elif naic_reliability is not None:
            blended = naic_reliability
        elif ins_reliability is not None:
            blended = ins_reliability
        else:
            blended = 50.0

        rows.append({
            "carrier_id":               cid,
            "carrier_name":             c["name"],
            "complaint_ratio":          naic.get("complaint_ratio"),
            "complaint_count":          naic.get("complaint_count"),
            "complaint_source":         naic.get("complaint_source"),
            "naic_reliability_score":   naic_reliability,
            "overall_customer_rating":  ins.get("overall_rating"),
            "claims_rating":            ins.get("claims_rating"),
            "customer_service_rating":  ins.get("customer_service_rating"),
            "price_satisfaction":       ins.get("price_satisfaction"),
            "review_count":             ins.get("review_count"),
            "digital_maturity_score":   ins.get("digital_maturity_score"),
            "api_response_est_sec":     ins.get("api_response_est_sec"),
            "binding_speed_tier":       ins.get("binding_speed_tier"),
            "blended_reliability_score": blended,
            "bindiq_notes":             ins.get("bindiq_notes", ""),
            "source_year":              2023,
        })

    logger.info(f"  TABLE 3 (reliability): {len(rows)} carriers")
    return rows


def build_table_4_appetite(raw: dict) -> list[dict]:
    """
    TABLE 4: carrier_appetite
    What do they specialize in? industry appetite levels.
    Primary: config.py carrier strengths
    Supplement: carrier website industry mentions
    Supplement: MoneyGeek cheapest-carrier-per-industry
    """
    rows = []

    # Map carrier id → industries they mention on their website
    carr_data    = raw.get("carriers", {})
    carrier_pages = {}
    if carr_data:
        for c in carr_data.get("carriers", []):
            carrier_pages[c["carrier_id"]] = set(c.get("industry_appetite", []))

    # Map carrier id → industries where they're cheapest (from MoneyGeek)
    mg_data          = raw.get("moneygeek", {})
    moneygeek_cheapest = {}
    if mg_data:
        for r in mg_data.get("industry_rates", []):
            ind = r.get("industry_keyword")
            cid = r.get("carrier_id")
            if ind and cid:
                if cid not in moneygeek_cheapest:
                    moneygeek_cheapest[cid] = set()
                moneygeek_cheapest[cid].add(ind)

    for c in CARRIERS:
        cid       = c["id"]
        strengths = set(c.get("strengths", []))         # from config
        web_inds  = carrier_pages.get(cid, set())       # from website scrape
        mg_cheap  = moneygeek_cheapest.get(cid, set())  # cheapest on MoneyGeek

        for industry in INDUSTRIES:
            iid = industry["id"]

            # Determine appetite level
            is_strength    = iid in strengths
            is_web_mention = any(
                kw in iid or iid in kw
                for kw in web_inds
            )
            is_mg_cheapest = any(
                kw in iid or iid in kw
                for kw in mg_cheap
            )

            if is_strength:
                appetite = "strong"
            elif is_web_mention or is_mg_cheapest:
                appetite = "moderate"
            else:
                appetite = "neutral"

            # Numeric score for Agent 2 SPECIALIZES_IN relationship weights
            _APPETITE_SCORES = {"strong": 0.90, "moderate": 0.60, "neutral": 0.35}
            appetite_score = _APPETITE_SCORES[appetite]
            if is_strength:          # is_specialty bonus
                appetite_score = min(1.0, appetite_score + 0.05)

            evidence = []
            if is_strength:
                evidence.append("listed_as_strength_in_config")
            if is_web_mention:
                evidence.append("mentioned_on_carrier_website")
            if is_mg_cheapest:
                evidence.append("cheapest_on_moneygeek")

            rows.append({
                "carrier_id":    cid,
                "carrier_name":  c["name"],
                "industry_id":   iid,
                "industry_name": industry["name"],
                "appetite":      appetite,
                "appetite_score": appetite_score,
                "is_specialty":  is_strength,
                "evidence":      evidence,
                "carrier_type":  c["type"],
            })

    logger.info(f"  TABLE 4 (appetite): {len(rows)} rows ({len(CARRIERS)} carriers × {len(INDUSTRIES)} industries)")
    return rows


def build_table_5_state_presence(raw: dict) -> list[dict]:
    """
    TABLE 5: carrier_state_presence
    Where do they operate? state licensing, geographic appetite.
    Primary: NAIC licensing data (from naic_collector known data)
    """
    naic_data = raw.get("naic", {})
    naic_map  = {
        p["carrier_id"]: p
        for p in (naic_data.get("carriers", []) if naic_data else [])
    }

    rows = []
    for c in CARRIERS:
        cid          = c["id"]
        naic         = naic_map.get(cid, {})
        states_lic   = naic.get("states_licensed", list(TARGET_STATES.keys()))
        mkt_share    = naic.get("market_share_pct")
        market_rank  = naic.get("market_rank")

        for state_code, state_name in TARGET_STATES.items():
            is_licensed = state_code in states_lic

            rows.append({
                "carrier_id":            cid,
                "carrier_name":          c["name"],
                "state":                 state_code,
                "state_name":            state_name,
                "is_licensed":           is_licensed,
                "national_market_share": mkt_share,
                "national_market_rank":  market_rank,
                # State-level market share not publicly available per-carrier/per-state
                # — flagged as roadmap data source (SERFF/NAIC state-level filings)
                "state_market_share":    None,
                "source":                "NAIC UCAA database 2023",
            })

    logger.info(f"  TABLE 5 (state presence): {len(rows)} rows")
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — SAVE ALL TABLES
# ═════════════════════════════════════════════════════════════════════════════

def save_kg_tables(tables: dict) -> Path:
    """Save each KG table as a standalone JSON + a combined master file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    file_map = {
        "carrier_identity":        "kg_table_1_carrier_identity.json",
        "pricing_benchmarks":      "kg_table_2_pricing_benchmarks.json",
        "reliability":             "kg_table_3_reliability.json",
        "appetite":                "kg_table_4_appetite.json",
        "state_presence":          "kg_table_5_state_presence.json",
    }

    for key, filename in file_map.items():
        path = OUT_DIR / filename
        save_json({
            "table":        key,
            "generated_at": timestamp(),
            "row_count":    len(tables[key]),
            "data":         tables[key],
        }, path)
        logger.info(f"  Saved {filename} ({len(tables[key])} rows)")

    # Master combined file for Agent 2
    master = {
        "agent":         "Agent 1 — Market Data Collector",
        "generated_at":  timestamp(),
        "description": (
            "Five KG-ready data tables for BindIQ Agent 2 (KG Builder). "
            "Covers 12 carriers × 12 industries × 6 states. "
            "Data sources: NAIC, Insurify, MoneyGeek, carrier websites, Herald."
        ),
        "tables": {
            key: {
                "row_count": len(tables[key]),
                "data":      tables[key],
            }
            for key in file_map
        },
        "coverage": {
            "carriers":   [c["id"] for c in CARRIERS],
            "industries": [i["id"] for i in INDUSTRIES],
            "states":     list(TARGET_STATES.keys()),
        },
        "data_gaps": [
            "State-level market share per carrier (needs NAIC state filings)",
            "Real-time API binding latency (estimated from digital maturity proxy)",
            "Actual quote-to-bind success rates (no public source)",
            "SERFF rate filing trends (flagged as roadmap source)",
        ],
    }

    master_path = OUT_DIR / f"kg_master_{TODAY}.json"
    save_json(master, master_path)
    logger.info(f"\n  Master file: {master_path}")
    return master_path


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="BindIQ Agent 1 — Run all collectors")
    parser.add_argument(
        "--skip-scraping", action="store_true",
        help="Use only static/known data (no HTTP requests). Fast mode for testing."
    )
    parser.add_argument(
        "--collector", type=str, default=None,
        choices=["naic", "carriers", "insurify", "moneygeek", "herald"],
        help="Run only a single collector (for debugging)."
    )
    args = parser.parse_args()

    logger.info("\n" + "=" * 60)
    logger.info("  BindIQ Agent 1 — Master Data Collector")
    logger.info(f"  Mode: {'skip-scraping' if args.skip_scraping else 'full'}")
    logger.info(f"  Date: {TODAY}")
    logger.info("=" * 60)

    # ── Phase 1: Collect ──────────────────────────────────────────────────────
    raw = collect_all(
        skip_scraping=args.skip_scraping,
        only=args.collector,
    )

    if args.collector:
        logger.info(f"\nSingle collector mode — skipping table merge.")
        print(f"\nDone. Check raw_data/{args.collector}/ for output.")
        return

    # ── Phase 2: Build KG tables ──────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("  Building KG Tables")
    logger.info("=" * 60)

    tables = {
        "carrier_identity":   build_table_1_identity(raw),
        "pricing_benchmarks": build_table_2_pricing(raw),
        "reliability":        build_table_3_reliability(raw),
        "appetite":           build_table_4_appetite(raw),
        "state_presence":     build_table_5_state_presence(raw),
    }

    # ── Phase 3: Save ─────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("  Saving KG Tables")
    logger.info("=" * 60)

    master_path = save_kg_tables(tables)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  BindIQ Agent 1 — COMPLETE")
    print("=" * 60)
    print(f"\n  KG Tables built:")
    print(f"    TABLE 1  carrier_identity:      {len(tables['carrier_identity'])} carriers")
    print(f"    TABLE 2  pricing_benchmarks:    {len(tables['pricing_benchmarks'])} rows")
    print(f"    TABLE 3  reliability:           {len(tables['reliability'])} carriers")
    print(f"    TABLE 4  appetite:              {len(tables['appetite'])} rows  ({len(CARRIERS)}×{len(INDUSTRIES)})")
    print(f"    TABLE 5  state_presence:        {len(tables['state_presence'])} rows ({len(CARRIERS)}×{len(TARGET_STATES)})")
    print(f"\n  Master output: {master_path}")
    print(f"\n  Agent 2 can now build the knowledge graph.")


if __name__ == "__main__":
    main()
