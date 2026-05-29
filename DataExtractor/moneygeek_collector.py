"""
BindIQ Agent 1 — Collector A: MoneyGeek
Scrapes carrier GL pricing benchmarks by state + industry.

Parsing pipeline (4 strategies, in order):
  1. HTML <table> tag parsing      — works if MoneyGeek serves static tables
  2. Full-text carrier+price regex — catches inline pricing text
  3. __NEXT_DATA__ JSON extraction — MoneyGeek is Next.js; data lives in this tag
     (accessible via regular requests, no browser needed)
  4. Selenium headless Chrome      — last resort when JS must fully execute
  Fallback: published benchmarks   — always produces output even if all 4 fail

What it produces:
  raw_data/moneygeek/gl_<label>_<date>.html
  raw_data/moneygeek/parsed_rates_<date>.json
"""

import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    HEADERS, REQUEST_TIMEOUT, MAX_RETRIES,
    MONEYGEEK_URLS, PUBLISHED_BENCHMARKS,
    MG_DIR, LOG_DIR,
)
from utils import get_logger, retry, save_json, timestamp, RateLimiter
from llm_extractor import extract_carrier_prices


logger  = get_logger("moneygeek", LOG_DIR)
limiter = RateLimiter(calls_per_minute=8)
TODAY   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── State label → state code ──────────────────────────────────────────────────
STATE_MAP = {
    "NATIONAL":   "national",
    "TEXAS":      "TX",
    "CALIFORNIA": "CA",
    "INDIANA":    "IN",
    "OHIO":       "OH",
    "FLORIDA":    "FL",
    "YORK":       "NY",   # "gl_new_york" → last token "york"
}

# ── Carrier name aliases ──────────────────────────────────────────────────────
MG_ALIASES = {
    "The Hartford":           "hartford",
    "Hartford":               "hartford",
    "Progressive":            "progressive",
    "Progressive Commercial": "progressive",
    "NEXT Insurance":         "next",
    "NEXT":                   "next",
    "ERGO NEXT":              "next",     # alias used on some MoneyGeek state pages
    "Simply Business":        "simply_business",
    "biBERK":                 "simply_business",  # biBERK routes through Simply Business
    "Nationwide":             "nationwide",
    "Hiscox":                 "hiscox",
    "Travelers":              "travelers",
    "Chubb":                  "chubb",
    "Liberty Mutual":         "liberty_mutual",
    "Zurich":                 "zurich",
    "CNA":                    "cna",
    "Markel":                 "markel",
}

# ── Industry keyword → our industry_id ───────────────────────────────────────
INDUSTRY_KEYWORD_MAP = {
    "food":             "food_service",
    "restaurant":       "food_service",
    "bakery":           "food_service",
    "catering":         "food_service",
    "construction":     "construction",
    "contractor":       "construction",
    "manufactur":       "manufacturing",
    "tech":             "technology",
    "software":         "technology",
    "retail":           "retail",
    "store":            "retail",
    "cleaning":         "cleaning_services",
    "janitorial":       "cleaning_services",
    "landscap":         "landscaping",
    "lawn":             "landscaping",
    "healthcare":       "healthcare",
    "medical":          "healthcare",
    "clinic":           "healthcare",
    "logistic":         "logistics_transport",
    "transport":        "logistics_transport",
    "trucking":         "logistics_transport",
    "real estate":      "real_estate",
    "property":         "real_estate",
    "professional":     "professional_services",
    "consulting":       "professional_services",
    "food manufactur":  "food_manufacturing",
    "food process":     "food_manufacturing",
}

# ── Carrier name pattern for regex ───────────────────────────────────────────
CARRIER_PATTERN = re.compile(
    r"(The Hartford|Progressive(?:\s+Commercial)?|NEXT Insurance?|Simply Business|"
    r"Nationwide|Hiscox|Travelers|Chubb|Liberty Mutual|Zurich|CNA|Markel)",
    re.IGNORECASE,
)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_carrier(name: str) -> str | None:
    name_clean = name.strip()
    if name_clean in MG_ALIASES:
        return MG_ALIASES[name_clean]
    for alias, cid in MG_ALIASES.items():
        if alias.lower() in name_clean.lower():
            return cid
    return None


def _extract_dollar(text: str) -> float | None:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", text)
    if m:
        val = float(m.group(1).replace(",", ""))
        return val if 10 < val < 5000 else None
    return None


def _make_row(cid: str, name_raw: str, monthly: float, state: str,
              label: str, source: str) -> dict:
    return {
        "carrier_id":       cid,
        "carrier_name_raw": name_raw,
        "monthly_rate":     round(monthly, 2),
        "annual_rate":      round(monthly * 12, 2),
        "state":            state,
        "source_label":     label,
        "source":           source,
    }


def _map_industry_keyword(kw: str) -> str:
    kw_lower = kw.lower()
    for key, iid in INDUSTRY_KEYWORD_MAP.items():
        if key in kw_lower:
            return iid
    return kw_lower


# ═════════════════════════════════════════════════════════════════════════════
# FETCH — Strategy 0: plain requests
# ═════════════════════════════════════════════════════════════════════════════

@retry(max_attempts=MAX_RETRIES, delay=3.0, exceptions=(requests.RequestException,))
def fetch_page(url: str, label: str) -> str | None:
    limiter.wait()
    logger.info(f"  Fetching [{label}] → {url}")
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    logger.debug(f"  Got {len(resp.text):,} chars")
    return resp.text


# ═════════════════════════════════════════════════════════════════════════════
# FETCH — Strategy 4: Selenium headless (last resort)
# ═════════════════════════════════════════════════════════════════════════════

def fetch_with_selenium(url: str, label: str) -> str | None:
    """
    Launch headless Chrome, wait for JS to render, return full page source.
    Falls back gracefully if Selenium or ChromeDriver not installed.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        logger.warning("  Selenium/webdriver-manager not installed — skipping browser fallback")
        return None

    logger.info(f"  Selenium fallback [{label}]...")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"user-agent={HEADERS['User-Agent']}")

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=opts)
        driver.get(url)

        # Wait up to 20s for a table or price element to appear
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table, [class*='price'], [class*='cost'], [class*='carrier']")
                )
            )
        except Exception:
            pass  # timeout is ok — take whatever rendered

        time.sleep(2)   # let React hydrate remaining components
        html = driver.page_source
        logger.info(f"  Selenium rendered {len(html):,} chars")
        return html
    except Exception as e:
        logger.warning(f"  Selenium error [{label}]: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# PARSE — Strategy 1: HTML <table> tags
# ═════════════════════════════════════════════════════════════════════════════

def parse_html_tables(html: str, label: str, state: str) -> list[dict]:
    soup    = BeautifulSoup(html, "lxml")
    results = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            for i, cell in enumerate(cells):
                cid = _normalize_carrier(cell)
                if not cid:
                    continue
                for j in range(i + 1, min(i + 5, len(cells))):
                    price = _extract_dollar(cells[j])
                    if price and 10 < price < 2000:
                        results.append(_make_row(cid, cell, price, state, label, "moneygeek_html_table"))
                        break

    logger.debug(f"  Strategy 1 (HTML tables): {len(results)} rows")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# PARSE — Strategy 2: full-text carrier+price regex
# ═════════════════════════════════════════════════════════════════════════════

def parse_text_patterns(html: str, label: str, state: str) -> list[dict]:
    soup    = BeautifulSoup(html, "lxml")
    text    = soup.get_text(" ", strip=True)
    results = []

    pattern = re.compile(
        r"(The Hartford|Progressive(?:\s+Commercial)?|NEXT Insurance?|Simply Business|"
        r"Nationwide|Hiscox|Travelers|Chubb|Liberty Mutual|Zurich|CNA|Markel)"
        r"[^$\n]{0,80}\$\s*([\d,]+)",
        re.IGNORECASE,
    )
    seen = set()
    for m in pattern.finditer(text):
        cid   = _normalize_carrier(m.group(1))
        price = float(m.group(2).replace(",", ""))
        key   = (cid, round(price))
        if cid and 10 < price < 2000 and key not in seen:
            seen.add(key)
            results.append(_make_row(cid, m.group(1), price, state, label, "moneygeek_text_pattern"))

    logger.debug(f"  Strategy 2 (text patterns): {len(results)} rows")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# PARSE — Strategy 3: Next.js __NEXT_DATA__ embedded JSON
# MoneyGeek is built on Next.js — all page data is embedded in this script tag
# and is accessible via plain requests (no browser execution needed).
# ═════════════════════════════════════════════════════════════════════════════

def extract_next_data_json(html: str) -> dict | None:
    """Pull the __NEXT_DATA__ JSON blob from the page."""
    soup = BeautifulSoup(html, "lxml")
    tag  = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag or not tag.string:
        # Also try application/json script tags
        for script in soup.find_all("script", {"type": "application/json"}):
            if script.string and "carrier" in script.string.lower():
                tag = script
                break
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except (json.JSONDecodeError, TypeError):
        return None


def parse_next_data(html: str, label: str, state: str) -> list[dict]:
    """
    Extract pricing from the Next.js __NEXT_DATA__ JSON.
    Strategy: flatten the entire JSON to text and run carrier+number patterns.
    This is more reliable than navigating an unknown schema.
    """
    data = extract_next_data_json(html)
    if not data:
        logger.debug(f"  Strategy 3 (__NEXT_DATA__): no JSON found")
        return []

    # Flatten JSON to string and search for carrier + adjacent numbers
    json_text = json.dumps(data)
    results   = []
    seen      = set()

    # Pattern: carrier name followed within 100 chars by a 2-4 digit number
    # that looks like a monthly premium (10-2000 range)
    pattern = re.compile(
        r"(The Hartford|Progressive(?:[^\"]{0,15}Commercial)?|NEXT Insurance?|Simply Business|"
        r"Nationwide|Hiscox|Travelers|Chubb|Liberty Mutual|Zurich|CNA|Markel)"
        r'[^"}{]{0,100}?(?:"|:|\s)(\d{2,4}(?:\.\d{1,2})?)',
        re.IGNORECASE,
    )
    for m in pattern.finditer(json_text):
        cid   = _normalize_carrier(m.group(1))
        price = float(m.group(2))
        key   = (cid, round(price))
        if cid and 10 < price < 2000 and key not in seen:
            seen.add(key)
            results.append(_make_row(cid, m.group(1), price, state, label, "moneygeek_next_data"))

    logger.debug(f"  Strategy 3 (__NEXT_DATA__): {len(results)} rows")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# PARSE — Industry rates (cheapest-by-industry sections)
# Applied to whatever HTML we have (static or Selenium-rendered)
# ═════════════════════════════════════════════════════════════════════════════

def parse_industry_rates(html: str, label: str, state: str) -> list[dict]:
    soup    = BeautifulSoup(html, "lxml")
    text    = soup.get_text(" ", strip=True)
    results = []
    seen    = set()

    industry_pattern = re.compile(
        r"(food|restaurant|bakery|construction|manufactur|tech|retail|"
        r"cleaning|landscap|healthcare|medical|logistic|transport|real estate|"
        r"professional|consulting|food manufactur)"
        r"[^$\n]{0,150}\$\s*([\d,]+)",
        re.IGNORECASE,
    )

    for m in industry_pattern.finditer(text):
        price   = float(m.group(2).replace(",", ""))
        snippet = m.group(0)
        cm      = CARRIER_PATTERN.search(snippet)
        if not cm or not (10 < price < 5000):
            continue
        cid = _normalize_carrier(cm.group(1))
        iid = _map_industry_keyword(m.group(1))
        key = (cid, iid, round(price))
        if cid and key not in seen:
            seen.add(key)
            results.append({
                "industry_id":       iid,
                "industry_keyword":  m.group(1).lower(),
                "carrier_id":        cid,
                "carrier_name_raw":  cm.group(1),
                "monthly_rate":      round(price, 2),
                "annual_rate":       round(price * 12, 2),
                "state":             state,
                "source_label":      label,
                "source":            "moneygeek_industry_section",
            })

    logger.debug(f"  Industry rates [{label}]: {len(results)} rows")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# FALLBACK — published benchmarks when all strategies yield nothing
# ═════════════════════════════════════════════════════════════════════════════

def build_benchmark_fallback(state: str = "national") -> list[dict]:
    rows = []
    for cid, bench in PUBLISHED_BENCHMARKS.items():
        rows.append({
            "carrier_id":   cid,
            "monthly_rate": bench["gl_monthly_avg"],
            "annual_rate":  bench["gl_monthly_avg"] * 12,
            "state":        state,
            "source_label": "published_benchmark",
            "source":       bench["source"],
            "note":         "Static fallback — live scrape unavailable for this state",
        })
    return rows


# ═════════════════════════════════════════════════════════════════════════════
# DEDUPLICATE
# ═════════════════════════════════════════════════════════════════════════════

def deduplicate(rows: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    for r in rows:
        key = (r.get("carrier_id"), r.get("state", "national"))
        buckets[key].append(r)

    merged = []
    source_priority = [
        "moneygeek_html_table",
        "moneygeek_next_data",
        "moneygeek_text_pattern",
        "moneygeek_llm_haiku",
        "moneygeek_industry_section",
    ]

    for _, group in buckets.items():
        # Keep live sources over benchmarks
        live = [r for r in group if "benchmark" not in r.get("source", "")]
        pool = live if live else group

        # Pick highest-priority source row as the base
        def source_rank(r):
            src = r.get("source", "")
            for i, s in enumerate(source_priority):
                if s in src:
                    return i
            return len(source_priority)

        pool.sort(key=source_rank)
        avg  = round(sum(r["monthly_rate"] for r in pool) / len(pool), 2)
        best = pool[0].copy()
        best["monthly_rate"] = avg
        best["annual_rate"]  = round(avg * 12, 2)
        best["merged_from"]  = len(group)
        merged.append(best)

    return merged


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def _run_page(label: str, url: str) -> tuple[list[dict], list[dict], str | None]:
    """
    Attempt all parsing strategies for a single MoneyGeek page.
    Returns (carrier_rates, industry_rates, raw_html_path | None)
    """
    state = STATE_MAP.get(label.split("_")[-1].upper(), label.split("_")[-1].upper())

    # ── Fetch HTML ─────────────────────────────────────────────────────────────
    html = None
    try:
        html = fetch_page(url, label)
    except Exception as e:
        logger.warning(f"  Static fetch failed [{label}]: {e}")

    html_path = None
    if html:
        raw_path = MG_DIR / f"{label}_{TODAY}.html"
        raw_path.write_text(html, encoding="utf-8")
        html_path = str(raw_path)

    # ── Strategy 1: HTML tables ────────────────────────────────────────────────
    rates    = parse_html_tables(html, label, state) if html else []
    industry = parse_industry_rates(html, label, state) if html else []

    # ── Strategy 2: text patterns ──────────────────────────────────────────────
    if not rates and html:
        rates = parse_text_patterns(html, label, state)

    # ── Strategy 3: __NEXT_DATA__ JSON ────────────────────────────────────────
    if not rates and html:
        rates = parse_next_data(html, label, state)
        if rates:
            logger.info(f"  Strategy 3 (__NEXT_DATA__) succeeded for [{label}]")

    # ── Strategy 3.5: LLM (Claude Haiku) — handles RSC/React state pages ─────
    # MoneyGeek state pages use React Server Components where data lives in
    # self.__next_f.push([1,"..."]) script tags, invisible to regex/BS4 get_text.
    # Claude Haiku extracts RSC text + DOM text and returns structured JSON.
    if not rates and html:
        logger.info(f"  Strategy 3.5 (LLM Haiku) for [{label}]...")
        rates = extract_carrier_prices(html, state, label, MG_ALIASES)
        if rates:
            logger.info(f"  Strategy 3.5 (LLM) succeeded for [{label}]: {len(rates)} rates")

    # ── Strategy 4: Selenium ───────────────────────────────────────────────────
    if not rates:
        logger.info(f"  Strategies 1-3 yielded no data — trying Selenium for [{label}]")
        selenium_html = fetch_with_selenium(url, label)
        if selenium_html:
            sel_path = MG_DIR / f"{label}_{TODAY}_selenium.html"
            sel_path.write_text(selenium_html, encoding="utf-8")

            rates = parse_html_tables(selenium_html, label, state)
            if not rates:
                rates = parse_text_patterns(selenium_html, label, state)
            if not rates:
                rates = parse_next_data(selenium_html, label, state)
            if not industry:
                industry = parse_industry_rates(selenium_html, label, state)

            if rates:
                logger.info(f"  Selenium succeeded for [{label}]: {len(rates)} rates")

    # ── Fallback ───────────────────────────────────────────────────────────────
    if not rates:
        logger.warning(f"  All strategies failed for [{label}] — using published benchmarks")
        rates = build_benchmark_fallback(state)

    logger.info(f"  [{label}] final: {len(rates)} carrier rates, {len(industry)} industry rows")
    return rates, industry, html_path


def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 1 — MoneyGeek Collector starting")
    logger.info(f"Pages: {len(MONEYGEEK_URLS)} | Strategies: HTML table → text regex → __NEXT_DATA__ → LLM Haiku → Selenium")
    logger.info("=" * 60)

    MG_DIR.mkdir(parents=True, exist_ok=True)

    all_rates    = []
    all_industry = []
    raw_pages    = {}

    for label, url in MONEYGEEK_URLS.items():
        rates, industry, html_path = _run_page(label, url)
        all_rates.extend(rates)
        all_industry.extend(industry)
        if html_path:
            raw_pages[label] = html_path

    # Final fallback: if zero live data across all pages
    live_count = sum(1 for r in all_rates if "benchmark" not in r.get("source", ""))
    if live_count == 0:
        logger.warning("  No live data at all — using full national benchmark set")
        all_rates = build_benchmark_fallback("national")

    deduped = deduplicate(all_rates)

    output = {
        "collector":      "moneygeek",
        "collected_at":   timestamp(),
        "pages_attempted": list(MONEYGEEK_URLS.keys()),
        "raw_pages":      raw_pages,
        "carrier_rates":  deduped,
        "industry_rates": all_industry,
        "summary": {
            "total_carrier_rate_rows": len(deduped),
            "total_industry_rows":     len(all_industry),
            "live_rows":               live_count,
            "benchmark_rows":          len(deduped) - live_count,
            "states_covered":          sorted({r.get("state") for r in deduped}),
            "carriers_found":          sorted({r.get("carrier_id") for r in deduped if r.get("carrier_id")}),
        },
    }

    out_path = MG_DIR / f"parsed_rates_{TODAY}.json"
    save_json(output, out_path)

    logger.info(f"\n  Saved → {out_path}")
    logger.info(f"  Live rows: {live_count} | Benchmark rows: {output['summary']['benchmark_rows']}")
    logger.info(f"  States: {output['summary']['states_covered']}")
    logger.info(f"  Carriers: {output['summary']['carriers_found']}")

    return output


if __name__ == "__main__":
    result = run()
    print(f"\nMoneyGeek collector done.")
    print(f"  Carrier rate rows: {result['summary']['total_carrier_rate_rows']}")
    print(f"  Industry rows:     {result['summary']['total_industry_rows']}")
    print(f"  Live data rows:    {result['summary']['live_rows']}")
    print(f"  States covered:    {result['summary']['states_covered']}")
    print(f"  Carriers found:    {result['summary']['carriers_found']}")
