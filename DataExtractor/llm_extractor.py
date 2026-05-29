"""
BindIQ — LLM Extraction Helper

Uses Claude Haiku to extract structured pricing and rating data from
web pages when regex strategies fail (RSC/React Server Components pages).

Model: claude-haiku-4-5
Cost: ~$0.001 per page extraction (very cheap)

Usage:
  Set ANTHROPIC_API_KEY environment variable before running.
  If not set, all functions return None/[] silently (graceful degradation).
"""

import json
import re
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from utils import get_logger
from config import LOG_DIR

logger = get_logger("llm_extractor", LOG_DIR)

HAIKU_MODEL    = "claude-haiku-4-5"
MAX_CHUNK_CHARS = 8000   # max text sent per LLM call

_client = None


def _get_client():
    """Lazy-init Anthropic client. Returns None if API key not set or package missing."""
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY not set — LLM extraction disabled")
        return None

    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key)
        logger.info(f"Claude Haiku client ready (model: {HAIKU_MODEL})")
        return _client
    except ImportError:
        logger.warning("anthropic package not installed — run: pip install anthropic>=0.40.0")
        return None


def extract_rsc_text(html: str) -> str:
    """
    Extract text payload from React Server Components (RSC) script tags.
    MoneyGeek state pages use RSC format: self.__next_f.push([1,"..."])
    The payload is a JSON-encoded string containing rendered page content.
    """
    rsc_pattern = re.compile(
        r'self\.__next_f\.push\(\[1\s*,\s*"((?:[^"\\]|\\.)*)"\]\)',
        re.DOTALL,
    )

    chunks = []
    for m in rsc_pattern.finditer(html):
        raw = m.group(1)
        try:
            # Unescape JSON string escapes to get the real text
            decoded = json.loads(f'"{raw}"')
            # Strip React wire-protocol tokens (0:, 1:[, $"...", etc.)
            clean = re.sub(r'\$[A-Za-z0-9]+', ' ', decoded)
            clean = re.sub(r'\d+:\s*["\[{]', ' ', clean)
            clean = re.sub(r'["{}[\]]', ' ', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()
            if len(clean) > 50:
                chunks.append(clean)
        except (json.JSONDecodeError, Exception):
            # On decode failure, keep raw with escape sequences cleaned up
            fallback = re.sub(r'\\[ntr]', ' ', raw)
            if len(fallback) > 50:
                chunks.append(fallback)

    return ' '.join(chunks)


def _price_relevant_chunks(text: str, window: int = 300) -> str:
    """
    Return text snippets within `window` chars of any dollar amount.
    Keeps LLM input small — only price-relevant context.
    """
    chunks = []
    for m in re.finditer(r'\$\s*[\d,]+', text):
        start = max(0, m.start() - window)
        end   = min(len(text), m.end() + window)
        chunks.append(text[start:end])

    combined = ' ... '.join(chunks[:25])  # cap at 25 snippets
    return combined[:MAX_CHUNK_CHARS]


def extract_carrier_prices(
    html: str,
    state: str,
    label: str,
    carrier_aliases: dict,
) -> list[dict]:
    """
    Use Claude Haiku to extract GL carrier pricing from a page.
    Works on RSC / React-rendered content where regex misses the data.

    Args:
        html:            Raw page HTML
        state:           State code e.g. "TX" or "national"
        label:           Page label e.g. "gl_texas" (for source tracking)
        carrier_aliases: {alias_name: carrier_id} dict from the collector

    Returns list of pricing dicts, or [] if LLM unavailable / nothing found.
    """
    client = _get_client()
    if not client:
        return []

    from bs4 import BeautifulSoup

    # DOM text + RSC script payloads
    soup     = BeautifulSoup(html, "lxml")
    dom_text = soup.get_text(" ", strip=True)
    rsc_text = extract_rsc_text(html)
    combined = f"{dom_text}\n\n--- RSC CONTENT ---\n{rsc_text}"

    price_context = _price_relevant_chunks(combined)
    if len(price_context) < 50:
        logger.debug(f"  LLM: no price-relevant text for [{label}]")
        return []

    carrier_list = ", ".join(sorted(set(carrier_aliases.values())))

    prompt = f"""Extract insurance carrier GL (general liability) pricing from this web page about {state} GL insurance.

Carriers to find: hartford, progressive, next, travelers, chubb, nationwide, hiscox, markel, simply_business, liberty_mutual, zurich, cna
Also recognize: "The Hartford", "NEXT Insurance", "ERGO NEXT", "biBERK", "Progressive Commercial", "Simply Business", "Liberty Mutual", "CNA Financial", "Zurich Commercial"

Rules:
- Only monthly GL premiums ($10–$2000/month range)
- If annual price, set is_annual=true (will be divided by 12)
- Ignore auto, home, health, workers comp prices
- Return ONLY valid JSON

JSON format:
{{"prices": [{{"carrier_name": "NEXT Insurance", "carrier_id": "next", "monthly_rate": 25.0, "is_annual": false}}]}}

If nothing found: {{"prices": []}}

TEXT:
{price_context}"""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            return []

        data    = json.loads(json_match.group(0))
        results = []
        seen    = set()

        for item in data.get("prices", []):
            name    = item.get("carrier_name", "")
            rate    = item.get("monthly_rate")
            cid     = item.get("carrier_id")

            if not rate:
                continue

            # If model didn't provide carrier_id, try alias lookup
            if not cid:
                name_lower = name.lower()
                for alias, aid in carrier_aliases.items():
                    if alias.lower() in name_lower or name_lower in alias.lower():
                        cid = aid
                        break

            if not cid:
                continue

            monthly = float(rate)
            if item.get("is_annual"):
                monthly = monthly / 12

            key = (cid, round(monthly))
            if key in seen or not (10 <= monthly <= 2000):
                continue
            seen.add(key)

            results.append({
                "carrier_id":       cid,
                "carrier_name_raw": name,
                "monthly_rate":     round(monthly, 2),
                "annual_rate":      round(monthly * 12, 2),
                "state":            state,
                "source_label":     label,
                "source":           "moneygeek_llm_haiku",
            })

        logger.info(f"  LLM extraction [{label}]: {len(results)} carrier prices")
        return results

    except Exception as e:
        logger.warning(f"  LLM extraction error [{label}]: {e}")
        return []


def extract_insurify_ratings(
    html: str,
    carrier_id: str,
    carrier_name: str,
) -> dict | None:
    """
    Use Claude Haiku to extract star ratings from an Insurify carrier page.

    Returns dict with rating fields, or None if unavailable / not found.
    """
    client = _get_client()
    if not client:
        return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)[:MAX_CHUNK_CHARS]

    prompt = f"""Extract insurance carrier review ratings for {carrier_name} from this Insurify page.

Find:
- overall_rating (1–5 scale)
- claims_rating (1–5)
- customer_service (1–5)
- price_satisfaction (1–5)
- review_count (integer)
- avg_annual_premium (dollars, integer)

Return ONLY JSON (use null for missing):
{{"overall_rating": 4.2, "claims_rating": 4.0, "customer_service": 4.1, "price_satisfaction": 3.8, "review_count": 1847, "avg_annual_premium": 996}}

If no ratings at all: {{"found": false}}

TEXT:
{text}"""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            return None

        data = json.loads(json_match.group(0))
        if data.get("found") is False:
            return None

        result = {}
        for key in ["overall_rating", "claims_rating", "customer_service", "price_satisfaction"]:
            val = data.get(key)
            if val is not None:
                fval = float(val)
                if 1.0 <= fval <= 5.0:
                    result[key] = fval

        if data.get("review_count"):
            result["review_count"] = int(data["review_count"])
        if data.get("avg_annual_premium"):
            result["avg_annual_premium"] = int(data["avg_annual_premium"])

        if result:
            logger.info(
                f"  LLM Insurify [{carrier_id}]: "
                f"rating={result.get('overall_rating')} reviews={result.get('review_count')}"
            )
            return result
        return None

    except Exception as e:
        logger.warning(f"  LLM Insurify error [{carrier_id}]: {e}")
        return None


def extract_carrier_gl_price(
    html: str,
    carrier_id: str,
    carrier_name: str,
) -> dict | None:
    """
    Use Claude Haiku to extract GL pricing from a carrier's own website.
    Fallback when regex patterns find nothing on a successfully-fetched page.

    Returns {monthly_rate, annual_rate} or None.
    """
    client = _get_client()
    if not client:
        return None

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    price_context = _price_relevant_chunks(text, window=200)
    if len(price_context) < 30:
        return None

    prompt = f"""Extract the general liability (GL) insurance price from this {carrier_name} website.

Look for: monthly premium, annual premium, average cost, starting price, typical cost.
Ignore: auto, home, health, workers comp prices.

Return ONLY JSON:
{{"monthly_rate": 85.0, "annual_rate": 1020.0}}

If only annual found, compute monthly = annual / 12.
If only monthly found, compute annual = monthly * 12.
Valid ranges: monthly $10–$500, annual $120–$6000.
If no GL price: {{"found": false}}

TEXT:
{price_context}"""

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            return None

        data = json.loads(json_match.group(0))
        if data.get("found") is False:
            return None

        monthly = data.get("monthly_rate")
        annual  = data.get("annual_rate")

        if monthly and 10 <= float(monthly) <= 500:
            m = round(float(monthly), 2)
            a = round(float(annual), 2) if annual else round(m * 12, 2)
            logger.info(f"  LLM carrier [{carrier_id}]: ${m}/mo (${a}/yr)")
            return {"monthly_rate": m, "annual_rate": a}

        if annual and 120 <= float(annual) <= 6000:
            a = round(float(annual), 2)
            m = round(a / 12, 2)
            logger.info(f"  LLM carrier [{carrier_id}]: ${m}/mo (${a}/yr)")
            return {"monthly_rate": m, "annual_rate": a}

        return None

    except Exception as e:
        logger.warning(f"  LLM carrier error [{carrier_id}]: {e}")
        return None

# ═════════════════════════════════════════════════════════════════════════════
# NAIC COMPLAINT RATIO EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_naic_complaint_ratio(html: str, carrier_id: str) -> dict | None:
    """
    Extract complaint ratio from NAIC Consumer Information Source page using LLM.
    Returns: {"complaint_ratio": float, "complaints_count": int}
    """
    client = _get_client()
    if not client:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:4000]  # Cap at 4000 chars

        if not text or "complaint" not in text.lower():
            return None

        prompt = f"""Extract the complaint ratio from this NAIC insurance page.
Look for:
- "complaint ratio" or "complaint index" number (like 0.42, 1.2, etc.)
- Total complaint count or number of complaints filed

Return as JSON:
{{"complaint_ratio": 0.42, "complaints_count": 312}}

If not found, return empty: {{}}

Page text:
{text[:2000]}"""

        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_response = response.content[0].text.strip()
        result = json.loads(raw_response)

        if result.get("complaint_ratio") and 0 <= float(result.get("complaint_ratio", 0)) <= 3:
            logger.debug(f"  NAIC LLM [{carrier_id}] ratio={result['complaint_ratio']}")
            return {"complaint_ratio": float(result["complaint_ratio"])}

    except Exception as e:
        logger.debug(f"  NAIC LLM error [{carrier_id}]: {e}")

    return None

# ═════════════════════════════════════════════════════════════════════════════
# INSURIFY RATINGS EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_insurify_ratings(html: str, carrier_id: str, carrier_name: str = "") -> dict | None:
    """
    Extract Insurify customer ratings from carrier review page using LLM.
    Called when regex extraction fails on the page.
    
    Returns: {
        "overall_rating": 4.2,
        "claims_rating": 4.0,
        "customer_service_rating": 4.1,  (NOTE: insurify_collector may use "customer_service")
        "price_satisfaction": 3.8,
        "review_count": 1847
    }
    """
    client = _get_client()
    if not client:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:6000]

        if not text or len(text) < 500:
            return None

        prompt = f"""Extract customer ratings from this Insurify review page.
Look for star ratings (out of 5) for:
- Overall rating (main/overall)
- Claims handling rating
- Customer service rating
- Price/value satisfaction rating
- Total number of reviews

Return as JSON:
{{
  "overall_rating": 4.2,
  "claims_rating": 4.0,
  "customer_service_rating": 4.1,
  "price_satisfaction": 3.8,
  "review_count": 1847
}}

If ratings not found, return empty: {{}}

Page text:
{text[:3500]}"""

        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_response = response.content[0].text.strip()
        result = json.loads(raw_response)

        # Validate ratings are in reasonable range
        if result and result.get("overall_rating"):
            for key in ["overall_rating", "claims_rating", "customer_service_rating", "price_satisfaction"]:
                if key in result and result[key]:
                    result[key] = float(result[key])
                    if not (3.0 <= result[key] <= 5.0):
                        return None  # Out of range
            
            logger.debug(f"  Insurify LLM [{carrier_id}] overall={result.get('overall_rating')}")
            return result

    except Exception as e:
        logger.debug(f"  Insurify LLM error [{carrier_id}]: {e}")

    return None

# ═════════════════════════════════════════════════════════════════════════════
# AM BEST RATING EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def extract_am_best_rating(carrier_name: str) -> str | None:
    """
    Fetch AM Best rating for a carrier from ambest.com using LLM extraction.
    Returns: rating string like "A+", "A", "A-", "B++", etc.
    """
    client = _get_client()
    if not client:
        return None

    try:
        # Try to fetch AM Best page (best-effort)
        url = f"https://www.ambest.com/ratings/search"
        params = {"q": carrier_name, "entity_type": "company"}
        
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, params=params, timeout=10)
        resp.raise_for_status()
        html = resp.text[:5000]

        if not html or len(html) < 500:
            return None

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)[:3000]

        prompt = f"""Extract the AM Best Financial Strength Rating for this insurance company.
Look for ratings like: A++, A+, A, A-, B++, B+, B, B-, C++, C+, C, C-, D, E, F
Usually appears as "Secure Ratings" or "Financial Strength Rating".

Return ONLY the rating code, nothing else. Examples: A+, A-, B++, etc.
If not found, return: unknown

Company text:
{text}"""

        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )

        rating = response.content[0].text.strip()

        # Validate rating format
        if rating and rating in ["A++", "A+", "A", "A-", "B++", "B+", "B", "B-", "C++", "C+", "C", "C-", "D", "E", "F"]:
            logger.info(f"  AM Best live lookup [{carrier_name}]: {rating}")
            return rating

    except Exception as e:
        logger.debug(f"  AM Best lookup error [{carrier_name}]: {e}")

    return None