"""
BindIQ — Agent 1: Market Data Collector
Master Configuration
Carriers: 12  |  Industries: 12  |  States: 6
"""

import os
from pathlib import Path

# ── Load .env file (if present) ───────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)
except ImportError:
    pass  # python-dotenv not installed, will use os.environ directly

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
RAW_DIR   = BASE_DIR / "raw_data"
LOG_DIR   = RAW_DIR  / "logs"
MG_DIR    = RAW_DIR  / "moneygeek"
NAIC_DIR  = RAW_DIR  / "naic"
CARR_DIR  = RAW_DIR  / "carriers"
OUT_DIR   = RAW_DIR  / "output"

# ── Request Settings ──────────────────────────────────────────────────────────
SCRAPE_DELAY    = 2.5
REQUEST_TIMEOUT = 20
MAX_RETRIES     = 3
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Anthropic API (Claude Haiku — LLM extraction fallback) ───────────────────
# Set env var ANTHROPIC_API_KEY to enable LLM-powered data extraction.
# Used by llm_extractor.py when regex/table strategies find nothing.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Herald API ────────────────────────────────────────────────────────────────
# Set env var HERALD_API_KEY to use live sandbox. Falls back to synthetic quotes.
HERALD_API_KEY  = os.environ.get("HERALD_API_KEY", "")
HERALD_BASE_URL = "https://sandbox.heraldapi.com/v1"

# ═════════════════════════════════════════════════════════════════════════════
# 12 CARRIERS
# ═════════════════════════════════════════════════════════════════════════════
CARRIERS = [
    {
        "id": "hartford", "name": "The Hartford",
        "am_best_rating": "A+", "type": "traditional",
        "founded_year": 1810,
        "strengths": ["manufacturing", "construction", "professional_services"],
        "naic_code": "29424",
        "website_gl": "https://www.thehartford.com/general-liability-insurance",
    },
    {
        "id": "progressive", "name": "Progressive Commercial",
        "am_best_rating": "A+", "type": "traditional",
        "founded_year": 1937,
        "strengths": ["commercial_auto", "logistics_transport"],
        "naic_code": "24260",
        "website_gl": "https://www.progressivecommercial.com/business-insurance/general-liability-insurance/general-liability-insurance-cost/",
    },
    {
        "id": "next", "name": "NEXT Insurance",
        "am_best_rating": "A-", "type": "insurtech",
        "founded_year": 2016,
        "strengths": ["cleaning_services", "landscaping", "food_service", "retail"],
        "naic_code": "15263",
        "website_gl": "https://www.nextinsurance.com/general-liability-insurance/",
    },
    {
        "id": "travelers", "name": "Travelers",
        "am_best_rating": "A++", "type": "traditional",
        "founded_year": 1853,
        "strengths": ["construction", "manufacturing", "real_estate"],
        "naic_code": "25658",
        "website_gl": "https://www.travelers.com/business-insurance/general-liability",
    },
    {
        "id": "chubb", "name": "Chubb",
        "am_best_rating": "A++", "type": "traditional",
        "founded_year": 1882,
        "strengths": ["technology", "professional_services", "healthcare"],
        "naic_code": "12777",
        "website_gl": "https://www.chubb.com/us-en/business-insurance/general-liability.html",
    },
    {
        "id": "nationwide", "name": "Nationwide",
        "am_best_rating": "A+", "type": "traditional",
        "founded_year": 1926,
        "strengths": ["food_service", "retail", "agriculture"],
        "naic_code": "23787",
        "website_gl": "https://www.nationwide.com/business/insurance/general-liability/",
    },
    {
        "id": "hiscox", "name": "Hiscox",
        "am_best_rating": "A", "type": "specialty",
        "founded_year": 1901,
        "strengths": ["technology", "professional_services", "consulting", "media"],
        "naic_code": "10200",
        "website_gl": "https://www.hiscox.com/small-business-insurance/general-liability-insurance",
    },
    {
        "id": "markel", "name": "Markel",
        "am_best_rating": "A", "type": "specialty",
        "founded_year": 1930,
        "strengths": ["construction", "manufacturing", "logistics_transport"],
        "naic_code": "38970",
        "website_gl": "https://www.markel.com/insurance-products/specialty-insurance/contractors",
    },
    {
        "id": "simply_business", "name": "Simply Business",
        "am_best_rating": "varies", "type": "marketplace",
        "founded_year": 2005,
        "strengths": ["cleaning_services", "landscaping", "food_service"],
        "naic_code": "N/A",
        "website_gl": "https://www.simplybusiness.com/",
    },
    {
        "id": "liberty_mutual", "name": "Liberty Mutual",
        "am_best_rating": "A", "type": "traditional",
        "founded_year": 1912,
        "strengths": ["construction", "manufacturing", "real_estate"],
        "naic_code": "23043",
        "website_gl": "https://business.libertymutual.com/insurance-products/general-liability/",
    },
    {
        "id": "zurich", "name": "Zurich Commercial",
        "am_best_rating": "A+", "type": "traditional",
        "founded_year": 1872,
        "strengths": ["manufacturing", "construction", "food_manufacturing"],
        "naic_code": "16535",
        "website_gl": "https://www.zurichna.com/insurance/commercial/general-liability",
    },
    {
        "id": "cna", "name": "CNA Financial",
        "am_best_rating": "A", "type": "traditional",
        "founded_year": 1897,
        "strengths": ["professional_services", "healthcare", "technology"],
        "naic_code": "21175",
        "website_gl": "https://www.cna.com/web/guest/cna/business-insurance/general-liability",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
# 12 INDUSTRIES
# Each has a "competition_story" — the high-stakes scenario for your BindIQ demo
# ═════════════════════════════════════════════════════════════════════════════
INDUSTRIES = [
    {
        "id": "food_service", "name": "Food Service & Restaurants",
        "naics_codes": ["722511", "722513", "722514"],
        "risk_level": "medium",
        "key_coverages": ["GL", "product_liability", "liquor_liability"],
        "competition_story": "Maria's Bakery wins $500K Whole Foods contract — needs $2M GL in 48 hours",
    },
    {
        "id": "construction", "name": "Construction & Contractors",
        "naics_codes": ["236220", "238110", "238210"],
        "risk_level": "high",
        "key_coverages": ["GL", "workers_comp", "builders_risk", "umbrella"],
        "competition_story": "GC wins $3M office buildout — AIA contract requires $2M GL + $5M umbrella before breaking ground",
    },
    {
        "id": "manufacturing", "name": "Light Manufacturing",
        "naics_codes": ["332000", "333000", "339000"],
        "risk_level": "high",
        "key_coverages": ["GL", "product_liability", "property", "equipment_breakdown"],
        "competition_story": "Machine shop wins Tesla supplier contract — needs $5M product liability in 5 days or loses the deal",
    },
    {
        "id": "technology", "name": "Technology & IT Services",
        "naics_codes": ["541511", "541512", "541519"],
        "risk_level": "medium",
        "key_coverages": ["tech_eo", "cyber", "GL", "professional_liability"],
        "competition_story": "SaaS startup lands Fortune 500 client — requires $2M cyber + $1M E&O within 48 hours of contract signing",
    },
    {
        "id": "healthcare", "name": "Healthcare & Medical Offices",
        "naics_codes": ["621111", "621210", "621310"],
        "risk_level": "very_high",
        "key_coverages": ["malpractice", "GL", "cyber", "workers_comp"],
        "competition_story": "Medical practice joins hospital network — credentialing requires proof of $1M/$3M malpractice in 5 days",
    },
    {
        "id": "logistics_transport", "name": "Logistics & Transportation",
        "naics_codes": ["484110", "484121", "493110"],
        "risk_level": "high",
        "key_coverages": ["commercial_auto", "cargo", "GL", "workers_comp"],
        "competition_story": "Delivery company wins Amazon DSP contract — needs full commercial auto + cargo stack in 3 days",
    },
    {
        "id": "real_estate", "name": "Real Estate & Property Management",
        "naics_codes": ["531110", "531120", "531210"],
        "risk_level": "medium",
        "key_coverages": ["GL", "property", "umbrella", "eo_real_estate"],
        "competition_story": "PM company signs 50-unit commercial lease portfolio — each property needs certificate within 30 days",
    },
    {
        "id": "professional_services", "name": "Professional Services",
        "naics_codes": ["541211", "541310", "541611"],
        "risk_level": "medium",
        "key_coverages": ["professional_liability", "eo", "GL", "cyber", "do"],
        "competition_story": "Boutique consulting firm lands Deloitte subcontract — requires $2M E&O + $1M D&O within a week",
    },
    {
        "id": "retail", "name": "Retail & Consumer Goods",
        "naics_codes": ["441110", "452210", "453910"],
        "risk_level": "medium",
        "key_coverages": ["GL", "product_liability", "property", "crime"],
        "competition_story": "Consumer brand gets shelf space at Target — vendor agreement requires $2M product liability before first shipment",
    },
    {
        "id": "cleaning_services", "name": "Commercial Cleaning & Janitorial",
        "naics_codes": ["561720", "561710"],
        "risk_level": "medium",
        "key_coverages": ["GL", "workers_comp", "bonding", "commercial_auto"],
        "competition_story": "Cleaning company wins Google campus contract — needs $1M GL + $500K bonding in 24 hours",
    },
    {
        "id": "landscaping", "name": "Landscaping & Grounds Maintenance",
        "naics_codes": ["561730"],
        "risk_level": "medium",
        "key_coverages": ["GL", "workers_comp", "commercial_auto", "equipment"],
        "competition_story": "Landscaper wins city parks contract — municipality requires $2M GL + WC certificate before first day",
    },
    {
        "id": "food_manufacturing", "name": "Food Manufacturing & Processing",
        "naics_codes": ["311811", "311821", "311991"],
        "risk_level": "high",
        "key_coverages": ["product_liability", "GL", "property", "recall_insurance"],
        "competition_story": "Food manufacturer wins Walmart supplier deal — requires $5M product liability + recall coverage in 5 days",
    },
]

# ── States ────────────────────────────────────────────────────────────────────
TARGET_STATES = {
    "TX": "Texas",
    "CA": "California",
    "IN": "Indiana",
    "OH": "Ohio",
    "FL": "Florida",
    "NY": "New York",
}

# ── MoneyGeek URLs ────────────────────────────────────────────────────────────
MONEYGEEK_URLS = {
    "gl_national":    "https://www.moneygeek.com/insurance/business/cheap-general-liability-insurance/",
    # State URLs updated 2026-03 — MoneyGeek reorganized paths to /general-liability/{state}/best/
    "gl_texas":       "https://www.moneygeek.com/insurance/business/general-liability/texas/best/",
    "gl_california":  "https://www.moneygeek.com/insurance/business/general-liability/california/best/",
    "gl_indiana":     "https://www.moneygeek.com/insurance/business/general-liability/indiana/best/",
    "gl_ohio":        "https://www.moneygeek.com/insurance/business/general-liability/ohio/best/",
    "gl_florida":     "https://www.moneygeek.com/insurance/business/general-liability/florida/best/",
    "gl_new_york":    "https://www.moneygeek.com/insurance/business/general-liability/new-york/best/",
}

# ── NAIC URLs ─────────────────────────────────────────────────────────────────
NAIC_COMPLAINT_URL    = "https://content.naic.org/consumer_info/complaint_ratio_report.htm"
NAIC_MARKET_SHARE_URL = "https://content.naic.org/industry/insdata"

# ── Published Benchmarks (fallback when scraping blocked) ────────────────────
# Source: MoneyGeek, Insurify, carrier public disclosures
PUBLISHED_BENCHMARKS = {
    "hartford":       {"gl_monthly_avg": 83,  "source": "MoneyGeek 2026"},
    "progressive":    {"gl_monthly_avg": 85,  "source": "Progressive 2024 disclosure"},
    "next":           {"gl_monthly_avg": 95,  "source": "MoneyGeek 2026"},
    "simply_business":{"gl_monthly_avg": 100, "source": "MoneyGeek 2026"},
    "nationwide":     {"gl_monthly_avg": 98,  "source": "MoneyGeek 2026"},
    "hiscox":         {"gl_monthly_avg": 115, "source": "Hiscox SMB study"},
    "travelers":      {"gl_monthly_avg": 120, "source": "Industry benchmark"},
    "chubb":          {"gl_monthly_avg": 145, "source": "Industry benchmark"},
    "liberty_mutual": {"gl_monthly_avg": 125, "source": "Industry benchmark"},
    "zurich":         {"gl_monthly_avg": 130, "source": "Industry benchmark"},
    "cna":            {"gl_monthly_avg": 110, "source": "Industry benchmark"},
    "markel":         {"gl_monthly_avg": 105, "source": "Industry benchmark"},
}