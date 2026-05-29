"""
BindIQ — Agent 2: Knowledge Graph Builder
Connection settings loaded from environment variables / .env file
"""

import os
from pathlib import Path

# ── Load .env file ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)
except ImportError:
    pass

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
AGENT1_DIR  = BASE_DIR.parent / "DataExtractor" / "raw_data" / "output"
LOG_DIR     = BASE_DIR / "logs"

# ── Neo4j ─────────────────────────────────────────────────────────────────────
NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD",  "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE",  "plutus")

# ── ServiceNow CMDB ───────────────────────────────────────────────────────────
SNOW_INSTANCE  = os.environ.get("SNOW_INSTANCE",   "https://dev252187.service-now.com")
SNOW_USER      = os.environ.get("SNOW_USER",       "admin")
SNOW_PASSWORD  = os.environ.get("SNOW_PASSWORD",    "")

# ServiceNow table names (must be created in the instance)
SNOW_TABLE_CARRIERS  = "u_bindiq_carriers"
SNOW_TABLE_CUSTOMERS = "u_bindiq_customers"
SNOW_TABLE_POLICIES  = "u_bindiq_policies"

# ── Anthropic (Claude Haiku — explanation layer) ──────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Embeddings ────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # 384-dim, fast, good for short texts
EMBEDDING_DIM   = 384

# ── Hybrid Scoring Weights ────────────────────────────────────────────────────
WEIGHT_SEMANTIC = 0.30   # cosine similarity: customer desc ↔ carrier appetite
WEIGHT_GRAPH    = 0.40   # multi-hop graph path confidence
WEIGHT_RULES    = 0.30   # hard rules: state licensed, AM Best, revenue range

# Minimum AM Best rating accepted (anything below is disqualified)
MIN_AM_BEST = {"A++": 5, "A+": 4, "A": 3, "A-": 2, "B++": 1, "varies": 0}

# ── 12 Carriers (reference) ───────────────────────────────────────────────────
CARRIER_IDS = [
    "hartford", "progressive", "next", "travelers", "chubb",
    "nationwide", "hiscox", "markel", "simply_business",
    "liberty_mutual", "zurich", "cna",
]

# ── 12 Industries (reference) ─────────────────────────────────────────────────
INDUSTRY_IDS = [
    "food_service", "construction", "manufacturing", "technology",
    "healthcare", "logistics_transport", "real_estate",
    "professional_services", "retail", "cleaning_services",
    "landscaping", "food_manufacturing",
]

# ── 6 States (reference) ─────────────────────────────────────────────────────
TARGET_STATES = {"TX", "CA", "IN", "OH", "FL", "NY"}
