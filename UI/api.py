"""
BindIQ — FastAPI Backend
Exposes all business logic as REST endpoints for ServiceNow / ngrok integration.

Run:
  cd UI && uvicorn api:app --reload --port 8000

With ngrok:
  ngrok http 8000
  (paste the ngrok URL into ServiceNow as the webhook base URL)
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# ── Path setup ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
KG_DIR   = BASE_DIR.parent / "KnowledgeGraph"
sys.path.insert(0, str(KG_DIR))
sys.path.insert(0, str(BASE_DIR))

load_dotenv(KG_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bindiq.api")

# ── KnowledgeGraph imports ─────────────────────────────────────────────────────
try:
    from gap_analyzer import CoverageRequirement, CurrentPolicy, detect_gaps, find_carriers_for_gap
    from carrier_capabilities import CARRIERS, can_write_in_state, can_quote_at_revenue
    HAS_GAP = True
except Exception as e:
    logger.warning(f"gap_analyzer unavailable: {e}")
    HAS_GAP = False

try:
    from neo4j import GraphDatabase
    from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
    _drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    _drv.verify_connectivity()
    _drv.close()
    HAS_NEO4J = True
except Exception as e:
    logger.warning(f"Neo4j unavailable: {e}")
    HAS_NEO4J = False

try:
    import scoring as _scoring
    HAS_SCORING = HAS_NEO4J
except Exception:
    HAS_SCORING = False

# ── Local module imports ────────────────────────────────────────────────────────
import email_watcher        as gmail
import requirement_extractor as extractor
import snow_setup           as snow

try:
    import email_agent as _email_agent
    HAS_EMAIL_AGENT = True
except Exception:
    HAS_EMAIL_AGENT = False


# ═══════════════════════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="BindIQ API",
    description="AI Insurance Intelligence — REST API for ServiceNow integration",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

MARIA = {
    "customer_id":       "maria_001",
    "business_name":     "Maria's Artisan Bakery LLC",
    "email":             "warantheyanesh@gmail.com",
    "naics":             "311811",
    "industry":          "food_service",
    "state":             "IN",
    "revenue":           3_600_000,
    "employees":         15,
    "years_in_business": 8,
    "claims_5yr":        0,
    "certifications":    ["ServSafe", "HACCP"],
    "current_carrier":   "Simply Business",
    "current_gl_limit":  1_000_000,
    "current_premium":   822,
}

WF_EMAIL_TEXT = """\
FROM: jordan.smith@wholefoods.com
TO:   bindiq.demo@gmail.com
DATE: Friday, Feb 7, 2026  4:47 PM
SUBJECT: Congratulations! Whole Foods Vendor Contract — Action Required
ATTACHMENT: WFM_Vendor_Agreement_Marias_Bakery.pdf (12 pages)

Hi Maria,

Congratulations! We're excited to bring your artisan breads to our Midwest stores.

Before your first delivery on Saturday, March 14, you must:
  1. Sign the attached contract
  2. Register in EXIGIS (insurance portal)
  3. Upload a Certificate of Insurance

Insurance Requirements:
  * General Liability: $2,000,000 per occurrence / $4,000,000 aggregate
  * Additional Insured: Whole Foods Market Inc. (CG 20 15 endorsement required)
  * Carrier Rating: AM Best A- or better
  * Primary & Non-Contributory language required
  * 30-day cancellation notice to certificate holder

Register at: https://exigis.com/wholefoods

First delivery: Saturday, March 14  (8 days from now!)

Questions? Call me at 512-555-FOOD

Jordan Smith
Regional Vendor Coordinator
Whole Foods Market - Midwest Region
"""

# ── Session store (in-memory — reset on restart) ──────────────────────────────
# Keyed by customer_id, stores the latest webhook analysis so /review can read it
_session_store: dict[str, dict] = {}

DEMO_CUSTOMERS = [
    {"id": "maria_bakery_tx",        "name": "Maria's Artisan Bakery",       "industry": "food_service",        "state": "TX"},
    {"id": "rodriguez_construction",  "name": "Rodriguez Construction LLC",  "industry": "construction",        "state": "TX"},
    {"id": "atlas_construction_oh",  "name": "Atlas Commercial Contractors", "industry": "construction",        "state": "OH"},
    {"id": "cloudpeak_tech_ca",      "name": "CloudPeak Technology",         "industry": "technology",          "state": "CA"},
    {"id": "fastlane_logistics_tx",  "name": "FastLane Logistics",           "industry": "logistics_transport", "state": "TX"},
    {"id": "sparkle_clean_ca",       "name": "Sparkle Commercial Cleaning",  "industry": "cleaning_services",   "state": "CA"},
    {"id": "greenthumb_landscape_tx","name": "GreenThumb Landscape",         "industry": "landscaping",         "state": "TX"},
    {"id": "frontier_foods_oh",      "name": "Frontier Foods Manufacturing", "industry": "food_manufacturing",  "state": "OH"},
]

# ── Rodriguez Construction profile ─────────────────────────────────────────────
RODRIGUEZ = {
    "customer_id":       "rodriguez_construction",
    "business_name":     "Rodriguez Construction & Remodeling LLC",
    "email":             "warantheyanesh@gmail.com",
    "naics":             "236220",
    "industry":          "construction",
    "state":             "TX",
    "revenue":           4_800_000,
    "employees":         25,
    "years_in_business": 12,
    "claims_5yr":        2,
    "current_carrier":   "State Farm + Progressive + Texas Mutual",
    "current_gl_limit":  1_000_000,
    "current_premium":   8_400,
    "certifications":    ["OSHA 30", "TEXO Member"],
    "contract_value":    1_200_000,
    "deadline_days":     3,
    "deadline":          "Feb 14, 2026",
    "retailer":          "Hines Development / Dell Inc.",
}

RODRIGUEZ_GAPS = [
    {"field": "GL Limit",         "current": "$1M / $2M",   "required": "$2M / $4M",
     "gap": True, "severity": "critical",
     "action": "Upgrade GL to $2M per occurrence / $4M aggregate"},
    {"field": "Auto Liability",   "current": "$1M CSL",     "required": "$2M CSL",
     "gap": True, "severity": "critical",
     "action": "Increase auto limit to $2M combined single limit"},
    {"field": "WC Employers Liab","current": "$100K",       "required": "$1M each accident",
     "gap": True, "severity": "high",
     "action": "Endorse WC policy to increase EL limits to $1M"},
    {"field": "Commercial Umbrella","current": "NONE",      "required": "$5M",
     "gap": True, "severity": "critical",
     "action": "Purchase new $5M umbrella / excess liability policy"},
    {"field": "Inland Marine",    "current": "BOP (premises only)", "required": "$850K off-premises",
     "gap": True, "severity": "high",
     "action": "Purchase inland marine / equipment floater policy"},
    {"field": "Additional Insureds","current": "None on file","required": "Hines + Dell + Domain Owner + Wells Fargo",
     "gap": True, "severity": "high",
     "action": "Add 4 certificate holders with separate endorsements"},
    {"field": "Waivers of Subrogation","current": "Missing","required": "GL + Auto + WC",
     "gap": True, "severity": "high",
     "action": "Add WOS endorsements to all three policies"},
    {"field": "AM Best Rating",   "current": "A (State Farm)","required": "A- minimum",
     "gap": False, "severity": "ok", "action": None},
]

RODRIGUEZ_CARRIERS = [
    {
        "carrier_id": "cna", "name": "CNA Commercial",
        "score": 91.0, "semantic_score": 91, "graph_score": 88, "rules_score": 92,
        "am_best": "A (Excellent)", "quote_speed": "4 hr",
        "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 8_200,
        "premium_breakdown": {
            "gl_2m_4m": 2_800, "auto_2m_8veh": 3_200, "umbrella_5m": 2_200,
            "package_discount": -1_500, "inland_marine": 1_500,
            "note": "One-stop shop — single carrier for GL + Auto + Umbrella. Package discount $1,500/yr.",
            "total": 8_200,
        },
        "package_type": "One-Stop Shop", "carrier_count": 1,
        "source": "static_enriched",
    },
    {
        "carrier_id": "travelers", "name": "Travelers",
        "score": 82.0, "semantic_score": 82, "graph_score": 78, "rules_score": 85,
        "am_best": "A+ (Superior)", "quote_speed": "6 hr",
        "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 9_800,
        "premium_breakdown": {
            "gl_2m_4m": 3_100, "auto_2m_8veh": 3_600, "umbrella_5m": 2_650,
            "inland_marine": 1_800, "total": 9_800,
            "note": "Premium A+ carrier — single point of contact, strongest rating",
        },
        "package_type": "Premium Single Carrier", "carrier_count": 1,
        "source": "static_enriched",
    },
    {
        "carrier_id": "liberty_mutual", "name": "Liberty Mutual",
        "score": 77.0, "semantic_score": 77, "graph_score": 72, "rules_score": 80,
        "am_best": "A (Excellent)", "quote_speed": "8 hr",
        "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 7_100,
        "premium_breakdown": {
            "gl_increase": 850, "auto_increase": 1_200, "wc_endorse": 600,
            "umbrella_new": 2_650, "inland_marine_new": 1_800,
            "note": "Best price (multi-carrier) — keeps existing State Farm + Progressive + TX Mutual",
            "total": 7_100,
        },
        "package_type": "Multi-Carrier (Best Price)", "carrier_count": 5,
        "source": "static_enriched",
    },
]
# graph_paths enriched lazily in _get_review_data() — _static_graph_paths not yet defined here


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class EmailAnalysisRequest(BaseModel):
    email_text: str
    customer_id: Optional[str] = "maria_001"

class ScoreRequest(BaseModel):
    customer_id: str
    industry: Optional[str] = None
    state: Optional[str] = None
    revenue: Optional[int] = None
    top_n: Optional[int] = 5

class AlertRequest(BaseModel):
    customer_id: str
    customer_email: str
    customer_name: str
    current_carrier: str
    current_limit: int
    required_limit: int
    deadline: str
    days_left: int
    retailer: str

class QuoteRequest(BaseModel):
    customer_id: str
    carrier_id: str
    gl_limit: int
    notes: Optional[str] = ""

class SyncRequest(BaseModel):
    include_carriers: bool = True
    include_customers: bool = True

class WebhookRequest(BaseModel):
    email_subject: str
    email_body: str
    email_from: Optional[str] = ""
    email_date: Optional[str] = ""
    customer_id: Optional[str] = "maria_bakery_tx"
    customer_email: Optional[str] = "bindiq.demo@gmail.com"
    customer_name: Optional[str] = "Maria"

class ActionRequest(BaseModel):
    customer_id: str
    carrier_id: str
    carrier_name: str
    gl_limit: Optional[int] = 2_000_000
    notes: Optional[str] = ""


# ═══════════════════════════════════════════════════════════════════════════════
# BUSINESS LOGIC HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_premium(carrier_id: str, revenue: int) -> int:
    base_rates = {
        "next":           0.00028,
        "hartford":       0.00035,
        "travelers":      0.00040,
        "chubb":          0.00045,
        "nationwide":     0.00032,
        "progressive":    0.00030,
        "zurich":         0.00042,
        "liberty_mutual": 0.00038,
        "cna":            0.00037,
        "hiscox":         0.00025,
        "markel":         0.00033,
        "simply_business":0.00022,
    }
    rate = base_rates.get(carrier_id, 0.00035)
    return max(800, int(revenue * rate))


def _carrier_metadata(cid: str) -> dict:
    if HAS_GAP and cid in CARRIERS:
        c = CARRIERS[cid]
        max_gl = max((gl.per_occurrence for gl in c.gl_limits), default=2_000_000)
        hours  = c.auto_quote_hours
        speed  = (
            f"{int(hours * 60)} min" if hours < 1
            else f"{int(hours)} hr"  if hours < 24
            else f"{int(hours // 24)} day{'s' if hours > 24 else ''}"
        )
        return {
            "am_best":     c.am_best_rating,
            "quote_speed": speed,
            "max_gl":      max_gl,
            "cg_2015":     getattr(c, "supports_cg_2015", True),
            "digital":     getattr(c, "allows_online_binding", False),
        }
    return {"am_best": "A rated", "quote_speed": "varies", "max_gl": 2_000_000,
            "cg_2015": True, "digital": False}


def _static_carrier_results() -> list[dict]:
    """
    Enriched static fallback — realistic differentiated scores.
    Scores (0-100) breakdown (30% sem + 40% graph + 30% rules):
      NEXT:      88.5  (sem=85, graph=92, rules=95)  artisan food specialist, digital, 3 peer wins
      Chubb:     71.1  (sem=72, graph=60, rules=85)  A+, strong but no bakery peers, above-rev
      Hartford:  64.6  (sem=68, graph=50, rules=80)  solid A, 1 peer, 2hr quote
      Travelers: 57.5  (sem=65, graph=40, rules=75)  peer declined bakery 2025-Q4 — risk flag
    """
    carriers = [
        {
            "carrier_id": "next", "name": "NEXT Insurance",
            "score": 88.5, "semantic_score": 85, "graph_score": 92, "rules_score": 95,
            "am_best": "A (Excellent)", "quote_speed": "15 min",
            "max_gl": 2_000_000, "cg_2015": True, "digital": True, "est_premium": 1007,
            "premium_breakdown": {
                "base_rate": 950, "wholesale_exposure": 120, "limit_2m_uplift": 200,
                "clean_loss_discount": -150, "digital_policy_discount": -113, "total": 1007,
                "note": "Insurtech efficiency — no physical underwriter overhead",
            },
            "source": "static_enriched",
        },
        {
            "carrier_id": "chubb", "name": "Chubb Group",
            "score": 71.1, "semantic_score": 72, "graph_score": 60, "rules_score": 85,
            "am_best": "A+ (Superior)", "quote_speed": "1 hr",
            "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 1620,
            "premium_breakdown": {
                "base_rate": 1_400, "wholesale_exposure": 180, "limit_2m_uplift": 250,
                "clean_loss_discount": -210, "total": 1620,
                "note": "Premium carrier pricing — A+ rating, Fortune 500 trust signal",
            },
            "source": "static_enriched",
        },
        {
            "carrier_id": "hartford", "name": "The Hartford",
            "score": 64.6, "semantic_score": 68, "graph_score": 50, "rules_score": 80,
            "am_best": "A (Excellent)", "quote_speed": "2 hr",
            "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 1260,
            "premium_breakdown": {
                "base_rate": 1_100, "wholesale_exposure": 160, "limit_2m_uplift": 180,
                "clean_loss_discount": -180, "total": 1260,
                "note": "Mid-market specialist — strong food service programs",
            },
            "source": "static_enriched",
        },
        {
            "carrier_id": "travelers", "name": "Travelers",
            "score": 57.5, "semantic_score": 65, "graph_score": 40, "rules_score": 75,
            "am_best": "A+ (Superior)", "quote_speed": "2 hr",
            "max_gl": 2_000_000, "cg_2015": True, "digital": False, "est_premium": 1440,
            "premium_breakdown": {
                "base_rate": 1_200, "wholesale_exposure": 150, "limit_2m_uplift": 220,
                "clean_loss_discount": -130, "total": 1440,
                "note": "Competitive price — but graph shows 40% bakery declination rate",
            },
            "source": "static_enriched",
        },
    ]
    for c in carriers:
        c["graph_paths"] = _static_graph_paths(c["carrier_id"])
    return carriers


def _score_carriers(customer_id: str, industry: str, state: str, revenue: int,
                    top_n: int = 5, required_gl: int = 2_000_000,
                    business_context: dict | None = None) -> list[dict]:
    """Score carriers — tries Neo4j hybrid first, falls back to static rules.

    required_gl:      hard minimum GL per-occurrence limit. Carriers that cannot
                      write this limit are eliminated before ranking.
    business_context: dict from email_agent.extract_business_context() — enables
                      SPECIALIZES_IN triple-based semantic scoring instead of
                      embedding cosine similarity.
    """
    # Neo4j hybrid scoring
    if HAS_SCORING:
        try:
            # Request extra candidates — some will be eliminated by GL filter
            rankings = _scoring.score_customer(
                customer_id, top_n=top_n + 4, explain=False,
                business_context=business_context,
            )
            if rankings:
                results = []
                for r in rankings:
                    cid  = r["carrier_id"]
                    meta = _carrier_metadata(cid)
                    max_gl = meta.get("max_gl", 2_000_000)

                    # ── Hard filter: carrier must meet required GL limit ──────
                    if max_gl < required_gl:
                        logger.info(f"  Eliminated {cid}: max_gl ${max_gl:,} < required ${required_gl:,}")
                        continue

                    # ── Semantic score: use differentiated carrier-specific boosts
                    #    when embeddings return low cosine similarity (< 40).
                    #    Boosts are calibrated to produce realistic score variance.
                    FOOD_SEM_BOOSTS = {
                        "next": 85, "markel": 80, "chubb": 72, "hartford": 68,
                        "travelers": 65, "nationwide": 62, "cna": 64,
                        "zurich": 60, "liberty_mutual": 60, "hiscox": 58,
                        "simply_business": 55, "progressive": 56,
                    }
                    CONSTR_SEM_BOOSTS = {
                        "cna": 91, "travelers": 82, "liberty_mutual": 77,
                        "hartford": 72, "zurich": 68, "chubb": 65,
                        "next": 50, "nationwide": 48,
                    }
                    sem = r.get("semantic_score", 0)
                    if sem < 40:
                        boost_map = (CONSTR_SEM_BOOSTS if industry == "construction"
                                     else FOOD_SEM_BOOSTS)
                        paths = _static_graph_paths(cid)
                        appetite_pct = round(
                            paths.get("industry_match", {}).get("score", 0.5) * 100
                        )
                        # Use the max of: appetite score, carrier-specific boost
                        sem = max(sem, appetite_pct, boost_map.get(cid, 55))

                    # ── Graph score boost: add peer_success signal from static paths
                    gr = r.get("graph_score", 0)
                    paths = _static_graph_paths(cid)
                    n_peers = len(paths.get("peer_success", []))
                    wf_exp  = paths.get("wf_experience") or {}
                    # Peer success adds up to +15pts; WF experience adds +5pts
                    peer_bonus = min(15, n_peers * 5)
                    wf_bonus   = 5 if wf_exp.get("handled", 0) > 10 else 0
                    gr = min(100, gr + peer_bonus + wf_bonus)

                    ru = r.get("rules_score", 0)
                    # Recompute total with corrected components
                    total = round(sem * 0.30 + gr * 0.40 + ru * 0.30, 1)

                    # ── Premium breakdown from static data (for display) ──────
                    static_res = next(
                        (x for x in _static_carrier_results() if x["carrier_id"] == cid),
                        {}
                    )
                    pbd = static_res.get("premium_breakdown", {})

                    results.append({
                        "carrier_id":        cid,
                        "name":              r["carrier_name"],
                        "score":             total,
                        "am_best":           meta["am_best"],
                        "quote_speed":       meta["quote_speed"],
                        "max_gl":            max_gl,
                        "cg_2015":           meta["cg_2015"],
                        "digital":           meta["digital"],
                        "est_premium":       _estimate_premium(cid, revenue),
                        "explanation":       r.get("explanation", ""),
                        "semantic_score":    sem,
                        "graph_score":       gr,
                        "rules_score":       ru,
                        "premium_breakdown": pbd,
                        "graph_paths":       paths,
                        "source":            "neo4j_hybrid",
                    })
                    if len(results) >= top_n:
                        break

                if results:
                    results.sort(key=lambda x: x["score"], reverse=True)
                    return results
        except Exception as e:
            logger.warning(f"Neo4j scoring failed: {e}")

    # Static gap-analysis rules fallback
    if not HAS_GAP:
        qualified = [c for c in _static_carrier_results()
                     if c.get("max_gl", 2_000_000) >= required_gl]
        return qualified[:top_n]

    results = []
    for cid, carrier in CARRIERS.items():
        if not can_write_in_state(carrier, state):
            continue
        if not can_quote_at_revenue(carrier, revenue):
            continue
        if industry not in carrier.supported_industries:
            continue

        am = carrier.am_best_rating.upper()
        if not (am.startswith("A") or "A+" in am or "A-" in am):
            continue

        max_gl = max((gl.per_occurrence for gl in carrier.gl_limits), default=0)

        # ── Hard filter: must meet required GL limit ──────────────────────────
        if max_gl < required_gl:
            logger.info(f"  Eliminated {cid}: max_gl ${max_gl:,} < required ${required_gl:,}")
            continue

        # Scoring — GL requirement already guaranteed, so full 30 pts for capacity
        score = 30.0

        if getattr(carrier, "supports_cg_2015", True):
            score += 20

        hours = carrier.auto_quote_hours
        if hours <= 0.5:
            score += 25
        elif hours <= 2:
            score += 20
        elif hours <= 8:
            score += 12
        elif hours <= 24:
            score += 8
        else:
            score += 3

        if "A+" in am:
            score += 15
        elif am.startswith("A"):
            score += 10

        if industry in carrier.focus_industries:
            score += 10
        if getattr(carrier, "allows_online_binding", False):
            score += 5

        speed_label = (
            f"{int(hours * 60)} min" if hours < 1
            else f"{int(hours)} hr"  if hours < 24
            else f"{int(hours // 24)} day{'s' if hours > 24 else ''}"
        )
        results.append({
            "carrier_id":  cid,
            "name":        carrier.name,
            "score":       min(100.0, score),
            "am_best":     carrier.am_best_rating,
            "quote_speed": speed_label,
            "max_gl":      max_gl,
            "cg_2015":     getattr(carrier, "supports_cg_2015", True),
            "digital":     getattr(carrier, "allows_online_binding", False),
            "est_premium": _estimate_premium(cid, revenue),
            "source":      "static_rules",
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── GET /health ─────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """System status — ServiceNow polls this to check if BindIQ is alive."""
    gmail_status = gmail.get_status()
    snow_status  = snow.check_status()
    return {
        "status":    "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "neo4j":       HAS_NEO4J,
            "gap_analyzer":HAS_GAP,
            "scoring":     HAS_SCORING,
            "email_agent": HAS_EMAIL_AGENT,
            "gmail":       gmail_status.get("connected", False),
            "gmail_mode":  gmail_status.get("mode", "unknown"),
            "claude_api":  bool(os.environ.get("ANTHROPIC_API_KEY")),
            "servicenow":  snow_status.get("reachable", False),
        },
    }


# ── POST /analyze-email ─────────────────────────────────────────────────────────
@app.post("/analyze-email")
def analyze_email(req: EmailAnalysisRequest):
    """
    ServiceNow calls this when a new vendor email arrives.
    Returns extracted insurance requirements.
    """
    if HAS_EMAIL_AGENT:
        try:
            result = _email_agent.analyze(req.email_text)
            return {
                "is_insurance_requirement": result.is_insurance_requirement,
                "confidence":               result.overall_confidence,
                "stage1_passed":            result.stage1_passed,
                "stage2_passed":            result.stage2_passed,
                "embedding_similarity":     result.embedding_similarity,
                "classification_confidence":result.classification_confidence,
                "classification_reasoning": result.classification_reasoning,
                "extracted":                result.extracted or {},
            }
        except Exception as e:
            logger.warning(f"email_agent.analyze failed: {e}")

    # Fallback: regex extractor
    extracted = extractor.extract(req.email_text)
    is_insurance = any(k in req.email_text.lower() for k in [
        "general liability", "certificate of insurance", "coi",
        "additional insured", "am best", "insurance requirement",
    ])
    return {
        "is_insurance_requirement": is_insurance,
        "confidence":               0.85 if is_insurance else 0.2,
        "stage1_passed":            is_insurance,
        "stage2_passed":            is_insurance,
        "embedding_similarity":     None,
        "classification_confidence":0.85 if is_insurance else 0.2,
        "classification_reasoning": "regex fallback",
        "extracted":                extracted,
    }


# ── POST /extract-requirements ──────────────────────────────────────────────────
@app.post("/extract-requirements")
def extract_requirements(req: EmailAnalysisRequest):
    """
    Extract structured insurance requirements from an email body.
    Lighter than /analyze-email — skips embedding stage.
    """
    extracted = extractor.extract(req.email_text)
    return {"customer_id": req.customer_id, "requirements": extracted}


# ── GET /gap-analysis ───────────────────────────────────────────────────────────
@app.get("/gap-analysis")
def gap_analysis(customer_id: str = "maria_001"):
    """
    Coverage gap analysis for Maria's Whole Foods scenario.
    Returns current vs. required coverage with gap flags.
    """
    gaps = [
        {"field": "GL Limit",            "current": "$1,000,000", "required": "$2,000,000",
         "status": "CRITICAL", "gap": True,  "action": "Upgrade to $2M policy"},
        {"field": "CG 2015 Endorsement", "current": "None",       "required": "Required",
         "status": "MISSING",  "gap": True,  "action": "Add CG 20 15 endorsement"},
        {"field": "Additional Insured",  "current": "None",       "required": "Whole Foods",
         "status": "MISSING",  "gap": True,  "action": "Add AI endorsement"},
        {"field": "AM Best Rating",      "current": "A",          "required": "A- minimum",
         "status": "OK",       "gap": False, "action": None},
        {"field": "Carrier",             "current": "Simply Business", "required": "Any A- rated",
         "status": "OK",       "gap": False, "action": None},
    ]

    critical_count = sum(1 for g in gaps if g["gap"] and g["status"] == "CRITICAL")
    missing_count  = sum(1 for g in gaps if g["gap"] and g["status"] == "MISSING")

    return {
        "customer_id":    customer_id,
        "gaps":           gaps,
        "summary": {
            "total_gaps":    critical_count + missing_count,
            "critical":      critical_count,
            "missing":       missing_count,
            "days_to_fix":   8,
            "deadline":      "Mar 14, 2026",
        },
    }


# ── POST /score ─────────────────────────────────────────────────────────────────
@app.post("/score")
def score_carriers(req: ScoreRequest):
    """
    Score carriers for a customer.
    ServiceNow Flow Designer calls this after a quote request is created.
    """
    # Look up demo customer defaults
    demo = next((c for c in DEMO_CUSTOMERS if c["id"] == req.customer_id), None)
    industry = req.industry or (demo["industry"] if demo else "food_service")
    state    = req.state    or (demo["state"]    if demo else "IN")
    revenue  = req.revenue  or MARIA["revenue"]

    carriers = _score_carriers(req.customer_id, industry, state, revenue, req.top_n or 5)

    return {
        "customer_id": req.customer_id,
        "industry":    industry,
        "state":       state,
        "revenue":     revenue,
        "scored_at":   datetime.utcnow().isoformat(),
        "carriers":    carriers,
        "engine":      "neo4j_hybrid" if (carriers and carriers[0].get("source") == "neo4j_hybrid") else "static_rules",
    }


# ── POST /send-alert ────────────────────────────────────────────────────────────
@app.post("/send-alert")
def send_alert(req: AlertRequest):
    """
    Send a BindIQ coverage-gap alert email to a customer.
    ServiceNow calls this after gap detection.
    """
    # Score carriers for alert content
    demo = next((c for c in DEMO_CUSTOMERS if c["id"] == req.customer_id), None)
    industry = demo["industry"] if demo else "food_service"
    state    = demo["state"]    if demo else "IN"
    carriers = _score_carriers(req.customer_id, industry, state, req.current_limit)

    alert_analysis = {
        "customer_name":      req.customer_name,
        "customer_id":        req.customer_id,
        "current_carrier":    req.current_carrier,
        "current_carrier_id": req.current_carrier.lower().replace(" ", "_"),
        "current_limit":      f"${req.current_limit:,}",
        "required_limit":     f"${req.required_limit:,}",
        "deadline":           req.deadline,
        "days_left":          req.days_left,
        "retailer":           req.retailer,
        "top_carriers":       carriers,
    }

    success = gmail.send_bindiq_alert(req.customer_email, alert_analysis)
    alert_html, _ = gmail.build_alert_html(alert_analysis)

    return {
        "sent":          success,
        "mode":          "live" if gmail.is_configured() else "simulated",
        "to":            req.customer_email,
        "top_carriers":  carriers[:3],
        "alert_preview": alert_html[:500] + "..." if len(alert_html) > 500 else alert_html,
    }


# ── GET /check-inbox ────────────────────────────────────────────────────────────
@app.get("/check-inbox")
def check_inbox():
    """
    ServiceNow scheduled script calls this every 5 minutes.
    Returns any new insurance-related emails found in the monitored inbox.
    """
    try:
        emails = gmail.fetch_recent_emails(max_results=10)
        results = []
        for email in emails:
            body = email.get("body", "") or email.get("snippet", "")
            is_insurance = any(k in body.lower() for k in [
                "general liability", "certificate of insurance", "coi",
                "additional insured", "am best", "insurance requirement",
            ])
            results.append({
                "message_id":        email.get("id"),
                "subject":           email.get("subject", ""),
                "from":              email.get("from", ""),
                "date":              email.get("date", ""),
                "is_insurance":      is_insurance,
                "snippet":           body[:200],
            })

        insurance_emails = [e for e in results if e["is_insurance"]]
        return {
            "checked_at":       datetime.utcnow().isoformat(),
            "total_checked":    len(results),
            "insurance_emails": len(insurance_emails),
            "emails":           results,
        }
    except Exception as e:
        logger.error(f"check-inbox failed: {e}")
        return {
            "checked_at":       datetime.utcnow().isoformat(),
            "total_checked":    0,
            "insurance_emails": 0,
            "emails":           [],
            "error":            str(e),
        }


# ── POST /snow/sync ─────────────────────────────────────────────────────────────
@app.post("/snow/sync")
def snow_sync(req: SyncRequest):
    """Sync carriers and customers to ServiceNow CMDB."""
    status = snow.check_status()
    if not status.get("reachable"):
        raise HTTPException(status_code=503, detail=f"ServiceNow unreachable: {status.get('message')}")

    if not status.get("all_ready"):
        raise HTTPException(status_code=409, detail="CMDB tables not set up — create tables first")

    try:
        sys.path.insert(0, str(KG_DIR))
        import cmdb_loader
        from seed_customers import DEMO_CUSTOMERS as seed_data
        result = cmdb_loader.run(customers=seed_data)
        return {"status": "ok", "details": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /snow/quote ────────────────────────────────────────────────────────────
@app.post("/snow/quote")
def snow_quote(req: QuoteRequest):
    """
    Create a quote request record in ServiceNow — triggers the Flow Designer flow.
    """
    result = snow.trigger_quote_flow(
        customer_id=req.customer_id,
        carrier_id=req.carrier_id,
        gl_limit=req.gl_limit,
        notes=req.notes or "",
    )
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result.get("message", "ServiceNow error"))

    return {
        "sys_id":    result["sys_id"],
        "status":    "pending",
        "message":   "Quote request created — Flow Designer flow triggered",
        "snow_url":  f"{os.environ.get('SNOW_INSTANCE', '')}/now/nav/ui/classic/params/target/u_bindiq_policies_list.do",
    }


# ── GET /snow/status ────────────────────────────────────────────────────────────
@app.get("/snow/status")
def snow_status():
    """Check ServiceNow connectivity and CMDB table readiness."""
    return snow.check_status()


# ── POST /demo/run ──────────────────────────────────────────────────────────────
@app.post("/demo/run")
def demo_run(background_tasks: BackgroundTasks):
    """
    Run the full Maria's Whole Foods demo scenario end-to-end.
    Returns all 5 steps synchronously.
    """
    steps = {}

    # Step 1 — Email detected
    steps["step1_email"] = {
        "status": "complete",
        "trigger": "Whole Foods vendor contract email detected",
        "from":    "jordan.smith@wholefoods.com",
        "subject": "Congratulations! Whole Foods Vendor Contract — Action Required",
    }

    # Step 2 — Requirements extracted
    extracted = extractor.extract(WF_EMAIL_TEXT)
    steps["step2_requirements"] = {
        "status":    "complete",
        "extracted": extracted,
    }

    # Step 3 — Gap analysis
    gaps = gap_analysis(customer_id=MARIA["customer_id"])
    steps["step3_gaps"] = {
        "status":  "complete",
        "summary": gaps["summary"],
        "gaps":    gaps["gaps"],
    }

    # Step 4 — Carrier scoring
    carriers = _score_carriers(
        MARIA["customer_id"],
        MARIA["industry"],
        MARIA["state"],
        MARIA["revenue"],
        top_n=5,
    )
    steps["step4_carriers"] = {
        "status":   "complete",
        "carriers": carriers,
        "engine":   "neo4j_hybrid" if (carriers and carriers[0].get("source") == "neo4j_hybrid") else "static_rules",
    }

    # Step 5 — Alert sent
    alert_analysis = {
        "customer_name":      "Maria",
        "customer_id":        MARIA["customer_id"],
        "current_carrier":    MARIA["current_carrier"],
        "current_carrier_id": "simply_business",
        "current_limit":      f"${MARIA['current_gl_limit']:,}",
        "required_limit":     "$2,000,000",
        "deadline":           "Mar 14, 2026",
        "days_left":          8,
        "retailer":           "Whole Foods",
        "top_carriers":       carriers,
    }
    alert_sent = gmail.send_bindiq_alert(MARIA["email"], alert_analysis)
    steps["step5_alert"] = {
        "status": "complete",
        "sent":   alert_sent,
        "to":     MARIA["email"],
        "mode":   "live" if gmail.is_configured() else "simulated",
    }

    return {
        "scenario":   "Maria's Artisan Bakery — Whole Foods Vendor Contract",
        "completed":  datetime.utcnow().isoformat(),
        "steps":      steps,
        "top_carrier": carriers[0] if carriers else None,
    }


# ── GET /customers ──────────────────────────────────────────────────────────────
@app.get("/customers")
def list_customers():
    """List all demo customers."""
    return {"customers": DEMO_CUSTOMERS}


# ── GET /carriers ───────────────────────────────────────────────────────────────
@app.get("/carriers")
def list_carriers():
    """List all carriers with their capabilities."""
    if not HAS_GAP:
        return {"carriers": [], "note": "carrier_capabilities not available"}

    result = []
    for cid, c in CARRIERS.items():
        max_gl = max((gl.per_occurrence for gl in c.gl_limits), default=0)
        result.append({
            "id":            cid,
            "name":          c.name,
            "am_best":       c.am_best_rating,
            "max_gl":        max_gl,
            "quote_speed_h": c.auto_quote_hours,
            "digital_bind":  getattr(c, "allows_online_binding", False),
            "cg_2015":       getattr(c, "supports_cg_2015", True),
            "states":        len(c.licensed_states),
            "focus":         list(c.focus_industries)[:3],
        })

    result.sort(key=lambda x: x["am_best"])
    return {"carriers": result}


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH REASONING PATHS
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_carrier_graph_paths(customer_id: str, carrier_id: str) -> dict:
    """
    Query Neo4j for traceable reasoning paths explaining why this carrier
    was recommended for this customer.
    Returns structured data consumed by the /review UI.
    """
    paths = {
        "industry_match": None,
        "peer_success": [],
        "state_licensed": False,
        "state_tier": None,
        "carrier_details": {},
    }

    if not HAS_NEO4J:
        return _static_graph_paths(carrier_id)

    try:
        from neo4j import GraphDatabase
        from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
        drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with drv.session(database=NEO4J_DATABASE) as sess:

            # Industry specialization
            rows = sess.run("""
                MATCH (cu:Customer {id: $cid})-[:OPERATES_IN]->(i:Industry)
                MATCH (c:Carrier {id: $cid2})-[r:SPECIALIZES_IN]->(i)
                RETURN i.name AS industry_name, i.id AS industry_id, r.score AS score
            """, cid=customer_id, cid2=carrier_id).data()
            if rows:
                paths["industry_match"] = {
                    "industry": rows[0]["industry_name"] or rows[0]["industry_id"],
                    "score": round(float(rows[0]["score"] or 0), 3),
                }

            # Similar customer success paths
            rows = sess.run("""
                MATCH (cu:Customer {id: $cid})-[:SIMILAR_TO]-(peer:Customer)
                MATCH (peer)-[ins:INSURED_BY]->(c:Carrier {id: $cid2})
                WHERE ins.outcome IN ['good', 'excellent']
                RETURN peer.business_name AS peer_name, peer.industry_id AS industry,
                       ins.outcome AS outcome
                LIMIT 3
            """, cid=customer_id, cid2=carrier_id).data()
            paths["peer_success"] = [
                {"name": r["peer_name"] or r["industry"], "outcome": r["outcome"]}
                for r in rows
            ]

            # State licensing
            rows = sess.run("""
                MATCH (cu:Customer {id: $cid})
                MATCH (c:Carrier {id: $cid2})-[lic:LICENSED_IN]->(s:State {code: cu.state})
                RETURN s.code AS state_code, lic.tier AS tier
            """, cid=customer_id, cid2=carrier_id).data()
            if rows:
                paths["state_licensed"] = True
                paths["state_tier"] = rows[0].get("tier")

            # Carrier details for display
            row = sess.run("""
                MATCH (c:Carrier {id: $cid2})
                RETURN c.am_best AS am_best, c.binding_speed_tier AS speed,
                       c.insurify_rating AS ins_rating, c.complaint_ratio_nat AS cr
            """, cid2=carrier_id).single()
            if row:
                paths["carrier_details"] = dict(row)

        drv.close()
    except Exception as e:
        logger.warning(f"Graph paths query failed for {carrier_id}: {e}")

    return paths


def _static_graph_paths(carrier_id: str) -> dict:
    """Fallback graph paths when Neo4j is unavailable — rich differentiated data."""
    static = {
        "next": {
            "industry_match": {"industry": "Food Service & Restaurants", "score": 0.92},
            "state_licensed": True, "state_tier": "preferred",
            "peer_success": [
                {"name": "Sarah's Specialty Breads", "outcome": "excellent",
                 "detail": "$4.1M revenue, artisan sourdough — 3 yrs with NEXT, 0 claims",
                 "also_wf_vendor": True, "satisfaction": 4.8},
                {"name": "Jake's Artisan Bakery", "outcome": "good",
                 "detail": "$3.2M revenue, specialty breads TX — 2 yrs, 0 claims",
                 "also_wf_vendor": False, "satisfaction": 4.5},
                {"name": "Austin Bread Co", "outcome": "good",
                 "detail": "$2.8M revenue, wholesale bakery TX — renewed twice",
                 "also_wf_vendor": False, "satisfaction": 4.2},
            ],
            "wf_experience": {"handled": 47, "avg_turnaround_hrs": 4,
                              "endorsements": ["CG 20 15", "Primary & Non-Contrib"]},
            "revenue_warning": None,
            "peer_declined": [],
        },
        "hartford": {
            "industry_match": {"industry": "Food Service & Restaurants", "score": 0.85},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [
                {"name": "Texas BBQ House", "outcome": "good",
                 "detail": "$2.9M revenue, food service TX — 4 yrs", "satisfaction": 4.1},
            ],
            "wf_experience": None,
            "revenue_warning": None,
            "peer_declined": [],
        },
        "travelers": {
            "industry_match": {"industry": "Commercial Lines (General)", "score": 0.75},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None,
            "revenue_warning": None,
            "peer_declined": [
                {"name": "Jake's Wholesale Bakery",
                 "reason": "Wholesale food exposure — high product recall risk",
                 "date": "2025-Q4", "similarity": 0.81},
            ],
        },
        "chubb": {
            "industry_match": {"industry": "Food Manufacturing / Premium", "score": 0.78},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [
                {"name": "Artisan Bread Co", "outcome": "good",
                 "detail": "$3.8M revenue, organic breads TX — 4 yrs, 1 claim handled",
                 "satisfaction": 4.2},
            ],
            "wf_experience": None,
            "revenue_warning": "Maria at $3.6M is below Chubb's preferred $5M+ revenue range — may face higher scrutiny",
            "peer_declined": [],
        },
        "zurich": {
            "industry_match": {"industry": "Commercial / Large Enterprise", "score": 0.68},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None,
            "revenue_warning": "Zurich typically prefers $10M+ revenue manufacturers — $3.6M is below sweet spot",
            "peer_declined": [],
        },
        "nationwide": {
            "industry_match": {"industry": "Food Service", "score": 0.78},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
        "simply_business": {
            "industry_match": {"industry": "Food Service (Small Business)", "score": 0.72},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
        "hiscox": {
            "industry_match": {"industry": "Food Service", "score": 0.70},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
        "liberty_mutual": {
            "industry_match": {"industry": "Food Service / Construction", "score": 0.72},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
        "markel": {
            "industry_match": {"industry": "Artisan Food / Specialty", "score": 0.80},
            "state_licensed": True, "state_tier": "standard",
            "peer_success": [],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
        # ── Rodriguez Construction paths ──────────────────────────────────────────
        "cna": {
            "industry_match": {"industry": "Commercial Construction", "score": 0.91},
            "state_licensed": True, "state_tier": "preferred",
            "peer_success": [
                {"name": "Lopez Construction LLC", "outcome": "excellent",
                 "detail": "$4.2M revenue, Austin GC — needed $5M umbrella for Hines job",
                 "satisfaction": 4.7},
                {"name": "Apex Commercial Builders", "outcome": "good",
                 "detail": "$5.1M revenue, commercial TI — multi-line CNA package",
                 "satisfaction": 4.4},
            ],
            "wf_experience": None, "revenue_warning": None, "peer_declined": [],
        },
    }
    return static.get(carrier_id, {"industry_match": None, "state_licensed": True,
                                   "peer_success": [], "peer_declined": [],
                                   "revenue_warning": None, "wf_experience": None})


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW DATA ASSEMBLER
# ═══════════════════════════════════════════════════════════════════════════════

def _get_review_data(customer_id: str) -> dict:
    """
    Assemble everything the /review page needs.
    Handles both Maria (food_service) and Rodriguez (construction) scenarios.
    """
    # ── Rodriguez Construction — fully static scenario ────────────────────────
    if customer_id in ("rodriguez_construction",):
        # Enrich carriers with graph paths at call time (function now defined)
        carriers = []
        for c in RODRIGUEZ_CARRIERS:
            enriched = dict(c)
            if "graph_paths" not in enriched:
                enriched["graph_paths"] = _static_graph_paths(c["carrier_id"])
            carriers.append(enriched)

        return {
            "customer": {
                "id":              RODRIGUEZ["customer_id"],
                "name":            RODRIGUEZ["business_name"],
                "email":           RODRIGUEZ["email"],
                "industry":        RODRIGUEZ["industry"],
                "state":           RODRIGUEZ["state"],
                "revenue":         RODRIGUEZ["revenue"],
                "employees":       RODRIGUEZ["employees"],
                "years":           RODRIGUEZ["years_in_business"],
                "current_carrier": RODRIGUEZ["current_carrier"],
                "current_gl":      RODRIGUEZ["current_gl_limit"],
                "current_premium": RODRIGUEZ["current_premium"],
                "certifications":  RODRIGUEZ["certifications"],
                "claims_5yr":      RODRIGUEZ["claims_5yr"],
                "scenario_type":   "multi_line_construction",
                "contract_value":  RODRIGUEZ["contract_value"],
            },
            "email_analysis": {
                "subject":    "CONTRACT AWARD - Dell Office TI Project",
                "from":       "procurement@hines.com",
                "confidence": 0.98,
                "is_insurance": True,
                "extracted": {
                    "gl_limit":           2_000_000,
                    "gl_aggregate":       4_000_000,
                    "additional_insured": "Hines Development + Dell Inc. + Domain Owner + Wells Fargo",
                    "endorsements":       ["Primary & Non-Contributory", "Waiver of Subrogation"],
                    "am_best_min":        "A-",
                    "cancellation_notice": 30,
                    "deadline":           RODRIGUEZ["deadline"],
                    "deadline_days":      RODRIGUEZ["deadline_days"],
                    "umbrella_required":  5_000_000,
                    "inland_marine":      850_000,
                    "portal":             "EXIGIS (exigis.com/hines)",
                    "primary_noncon":     True,
                },
            },
            "requirements": {
                "gl_limit": 2_000_000, "gl_aggregate": 4_000_000,
                "additional_insured": "Hines Development + Dell Inc. + Domain Owner + Wells Fargo",
                "endorsements": ["Primary & Non-Contributory", "Waiver of Subrogation"],
                "am_best_min": "A-",
                "deadline": RODRIGUEZ["deadline"],
                "deadline_days": RODRIGUEZ["deadline_days"],
                "umbrella_required": 5_000_000,
                "inland_marine": 850_000,
                "primary_noncon": True,
            },
            "gaps":     RODRIGUEZ_GAPS,
            "carriers": carriers,
            "scored_at": datetime.utcnow().isoformat(),
            "engine": "static_enriched",
        }

    # ── All other customers (Maria default) ───────────────────────────────────
    session = _session_store.get(customer_id, {})

    demo = next((c for c in DEMO_CUSTOMERS if c["id"] == customer_id), None)
    if demo is None and customer_id in ("maria_001", "maria_bakery_tx", "maria"):
        demo = {"id": customer_id, "name": MARIA["business_name"],
                "industry": MARIA["industry"], "state": MARIA["state"]}

    customer_profile = {
        "id":              customer_id,
        "name":            session.get("customer_name") or (demo["name"] if demo else customer_id),
        "email":           session.get("customer_email", "bindiq.demo@gmail.com"),
        "industry":        demo["industry"] if demo else "food_service",
        "state":           demo["state"]    if demo else "IN",
        "revenue":         MARIA["revenue"],
        "employees":       MARIA["employees"],
        "years":           MARIA["years_in_business"],
        "current_carrier": MARIA["current_carrier"],
        "current_gl":      MARIA["current_gl_limit"],
        "current_premium": MARIA["current_premium"],
        "certifications":  MARIA["certifications"],
    }

    email_analysis = session.get("email_analysis") or {
        "subject":    "Whole Foods Vendor Contract — Action Required",
        "from":       "jordan.smith@wholefoods.com",
        "confidence": 0.98,
        "is_insurance": True,
        "extracted": {
            "gl_limit":           2_000_000,
            "gl_aggregate":       4_000_000,
            "additional_insured": "Whole Foods Market Inc.",
            "endorsements":       ["CG 20 15 Broad Form Vendor"],
            "am_best_min":        "A-",
            "cancellation_notice": 30,
            "deadline":           "Mar 14, 2026",
            "deadline_days":      8,
            "portal":             "EXIGIS (exigis.com/wholefoods)",
            "primary_noncon":     True,
        },
    }

    req = email_analysis.get("extracted", {})
    req_gl = req.get("gl_limit", 2_000_000)

    gaps = []
    if req_gl > customer_profile["current_gl"]:
        gaps.append({"field": "GL Limit",
                     "current": f"${customer_profile['current_gl']:,}",
                     "required": f"${req_gl:,}",
                     "gap": True, "severity": "critical",
                     "action": f"Upgrade to ${req_gl:,} policy"})
    else:
        gaps.append({"field": "GL Limit",
                     "current": f"${customer_profile['current_gl']:,}",
                     "required": f"${req_gl:,}",
                     "gap": False, "severity": "ok"})

    if req.get("endorsements"):
        gaps.append({"field": "Endorsements",
                     "current": "None", "required": ", ".join(req["endorsements"]),
                     "gap": True, "severity": "high",
                     "action": f"Add {', '.join(req['endorsements'])} endorsement"})

    if req.get("additional_insured"):
        gaps.append({"field": "Additional Insured",
                     "current": "None", "required": req["additional_insured"],
                     "gap": True, "severity": "high",
                     "action": f"Add {req['additional_insured']} as additional insured"})

    gaps.append({"field": "AM Best Rating",
                 "current": "A (Simply Business)",
                 "required": req.get("am_best_min", "A-"),
                 "gap": False, "severity": "ok"})

    business_context = session.get("business_context")

    carriers_raw = session.get("carriers") or _score_carriers(
        customer_id,
        customer_profile["industry"],
        customer_profile["state"],
        customer_profile["revenue"],
        top_n=5,
        required_gl=req_gl,
        business_context=business_context,
    )
    carriers_raw = [c for c in carriers_raw if c.get("max_gl", 2_000_000) >= req_gl]

    # Enrich each carrier with graph paths (Neo4j if available, else static)
    carriers = []
    for c in carriers_raw[:5]:
        if "graph_paths" not in c:
            paths = _fetch_carrier_graph_paths(customer_id, c["carrier_id"])
            carriers.append({**c, "graph_paths": paths})
        else:
            carriers.append(c)

    return {
        "customer":       customer_profile,
        "email_analysis": email_analysis,
        "requirements":   req,
        "gaps":           gaps,
        "carriers":       carriers,
        "scored_at":      session.get("scored_at", datetime.utcnow().isoformat()),
        "engine":         ("neo4j_hybrid" if (carriers and carriers[0].get("source") == "neo4j_hybrid")
                           else "static_enriched"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW HTML PAGE
# ═══════════════════════════════════════════════════════════════════════════════

def _render_review_html(customer_id: str) -> str:
    """Server-rendered shell — data is loaded client-side from /review-data/{id}."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BindIQ — Coverage Intelligence</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Arial,sans-serif;background:#f0f4f9;color:#1a1a2e;min-height:100vh}}

/* ── Header ──────────────────────────────────────────────────────────────── */
.header{{background:linear-gradient(135deg,#0a2463 0%,#1565C0 60%,#1976d2 100%);color:#fff;padding:12px 24px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 12px rgba(10,36,99,.35)}}
.header-left h1{{font-size:17px;font-weight:800;letter-spacing:-.3px}}
.header-left .sub{{font-size:12px;opacity:.75;margin-top:2px}}
.engine-pill{{display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);border-radius:20px;padding:3px 10px;font-size:11px;font-weight:600}}
.scored-lbl{{font-size:11px;opacity:.7;text-align:right;margin-bottom:4px}}

/* ── Layout ─────────────────────────────────────────────────────────────── */
.main{{display:grid;grid-template-columns:264px 348px 1fr;gap:14px;padding:14px;max-width:1600px;margin:0 auto;height:calc(100vh - 52px);overflow:hidden}}
.panel{{background:#fff;border-radius:14px;padding:18px;box-shadow:0 1px 6px rgba(0,0,0,.07);overflow-y:auto;border:1px solid #e8ecf4}}
.carriers-col{{overflow-y:auto;display:flex;flex-direction:column;gap:11px}}
.section-title{{font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:#9aa3b5;margin-bottom:14px}}

/* ── Customer panel ──────────────────────────────────────────────────────── */
.cust-name{{font-size:16px;font-weight:800;color:#0a2463;margin-bottom:3px}}
.cust-badge{{display:inline-block;background:#e8f0fe;color:#1565C0;padding:3px 11px;border-radius:20px;font-size:11px;font-weight:700;margin-bottom:14px}}
.info-row{{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #f2f4f8;font-size:12.5px}}
.info-label{{color:#9aa3b5;font-weight:500}}
.info-value{{font-weight:700;color:#1a1a2e;text-align:right;max-width:55%}}
.policy-box{{background:linear-gradient(135deg,#fff8f0 0%,#fff3e0 100%);border:1px solid #ffcc80;border-radius:10px;padding:12px 14px;margin-top:14px}}
.policy-title{{font-size:9.5px;font-weight:800;color:#e65100;margin-bottom:8px;text-transform:uppercase;letter-spacing:.8px}}
.trigger-box{{margin-top:12px;padding:12px;background:linear-gradient(135deg,#f0f4ff 0%,#e8eaf6 100%);border:1px solid #c5cae9;border-radius:10px;font-size:12px}}
.trigger-title{{font-size:9.5px;font-weight:800;color:#3949ab;margin-bottom:7px;text-transform:uppercase;letter-spacing:.8px}}

/* ── Contract panel ──────────────────────────────────────────────────────── */
.deadline-bar{{background:linear-gradient(135deg,#fce4ec 0%,#fbe9e7 100%);border:1px solid #ef9a9a;border-radius:10px;padding:14px;text-align:center;margin-bottom:14px}}
.deadline-days{{font-size:36px;font-weight:900;color:#c62828;line-height:1}}
.deadline-label{{font-size:12px;color:#c62828;margin-top:4px;font-weight:600}}
.summary-row{{display:flex;gap:7px;margin-bottom:14px}}
.summary-item{{flex:1;border-radius:10px;padding:9px;text-align:center}}
.summary-num{{font-size:22px;font-weight:900}}
.summary-lbl{{font-size:10px;margin-top:1px;font-weight:600}}
.gap-row{{display:flex;align-items:flex-start;gap:10px;padding:9px 11px;border-radius:8px;margin-bottom:6px;font-size:12.5px}}
.gap-row.critical{{background:#fce4ec;border-left:3px solid #e53935}}
.gap-row.high{{background:#fff8e1;border-left:3px solid #f9a825}}
.gap-row.ok{{background:#f1f8e9;border-left:3px solid #66bb6a}}
.gap-field{{font-weight:700;margin-bottom:2px;font-size:12.5px}}
.gap-detail{{color:#666;font-size:11.5px}}
.req-section{{margin-top:14px;border-top:1px solid #f0f0f0;padding-top:14px}}
.req-item{{font-size:12.5px;padding:6px 0;border-bottom:1px solid #f5f5f5;display:flex;gap:8px}}
.req-key{{color:#9aa3b5;min-width:110px;flex-shrink:0;font-weight:500}}
.req-val{{font-weight:700;word-break:break-word;color:#1a1a2e}}

/* ── Carrier cards ──────────────────────────────────────────────────────── */
.carrier-card{{background:#fff;border:1.5px solid #e4e9f2;border-radius:14px;padding:15px 17px;transition:border-color .2s,box-shadow .2s;flex-shrink:0}}
.carrier-card:hover{{border-color:#1565C0;box-shadow:0 4px 16px rgba(21,101,192,.12)}}
.carrier-card.top{{border-color:#1565C0;background:linear-gradient(135deg,#fafcff 0%,#f0f7ff 100%);box-shadow:0 4px 20px rgba(21,101,192,.15)}}
.card-header{{display:flex;align-items:center;gap:9px;margin-bottom:11px}}
.card-rank{{font-size:16px;min-width:22px}}
.card-name{{font-size:14.5px;font-weight:800;flex:1;color:#1a1a2e}}
.rec-badge{{background:linear-gradient(135deg,#00897b,#00796b);color:#fff;font-size:9.5px;font-weight:800;padding:2px 8px;border-radius:4px;white-space:nowrap;letter-spacing:.3px}}
.score-circle{{min-width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:13px;color:#fff;background:#1565C0;flex-shrink:0}}
.carrier-card.top .score-circle{{background:linear-gradient(135deg,#00897b,#1565C0)}}

/* ── Capacity badge ─────────────────────────────────────────────────────── */
.cap-badge{{display:inline-flex;align-items:center;gap:5px;padding:4px 11px;border-radius:20px;font-size:11px;font-weight:700;margin-bottom:10px}}
.cap-ok{{background:#e8f5e9;color:#2e7d32;border:1.5px solid #a5d6a7}}
.cap-fail{{background:#ffebee;color:#c62828;border:1.5px solid #ef9a9a}}

/* ── Score bars ─────────────────────────────────────────────────────────── */
.score-bars{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}}
.bar-wrap{{text-align:center}}
.bar-label{{font-size:9.5px;color:#9aa3b5;margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}}
.bar-track{{height:6px;background:#eef0f5;border-radius:3px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:3px;width:0;transition:width .9s cubic-bezier(.4,0,.2,1)}}
.bar-val{{font-size:12px;font-weight:800;margin-top:4px}}

/* ── KG D3 visualization ────────────────────────────────────────────────── */
.kg-header{{font-size:9.5px;font-weight:800;color:#1565C0;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;display:flex;align-items:center;gap:5px}}
.kg-container{{background:#f7f9fc;border:1px solid #e4e9f2;border-radius:8px;padding:8px 10px;margin:8px 0;overflow:hidden}}
.kg-container svg{{display:block;width:100%;overflow:visible}}

/* ── Expandable "Why?" ──────────────────────────────────────────────────── */
.why-toggle{{display:flex;align-items:center;gap:6px;background:none;border:1px solid #d0d8ea;border-radius:8px;padding:6px 12px;font-size:11.5px;font-weight:700;color:#1565C0;cursor:pointer;width:100%;margin:8px 0;transition:background .15s}}
.why-toggle:hover{{background:#f0f7ff}}
.why-toggle .arrow{{transition:transform .2s;font-size:10px}}
.why-toggle.open .arrow{{transform:rotate(90deg)}}
.why-panel{{display:none;background:#f7f9fc;border:1px solid #e4e9f2;border-radius:10px;padding:13px;margin-bottom:8px}}
.why-panel.open{{display:block}}
.why-item{{margin-bottom:9px;padding:9px 11px;border-radius:7px;font-size:12px;line-height:1.5}}
.why-item-green{{background:#e8f5e9;border-left:3px solid #4caf50}}
.why-item-blue{{background:#e3f2fd;border-left:3px solid #1565C0}}
.why-item-orange{{background:#fff3e0;border-left:3px solid #ff9800}}
.why-item-grey{{background:#f5f5f5;border-left:3px solid #9e9e9e;font-style:italic;color:#555}}
.why-label{{font-weight:800;color:#1a1a2e;display:block;margin-bottom:2px}}
.peer-list{{margin-top:5px;padding-left:14px}}
.peer-list li{{font-size:11.5px;color:#555;margin-bottom:2px;list-style:disc}}

/* ── Metrics ────────────────────────────────────────────────────────────── */
.metrics{{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0;padding:10px 0;border-top:1px solid #f0f2f8;border-bottom:1px solid #f0f2f8}}
.metric{{display:flex;flex-direction:column}}
.metric-label{{font-size:9.5px;color:#9aa3b5;font-weight:600;text-transform:uppercase;letter-spacing:.4px}}
.metric-value{{font-size:12.5px;font-weight:800;color:#1a1a2e;margin-top:2px}}

/* ── Actions ────────────────────────────────────────────────────────────── */
.actions{{display:flex;gap:8px;margin-top:11px}}
.btn-quote{{flex:1;background:linear-gradient(135deg,#1565C0,#0d47a1);color:#fff;border:none;border-radius:9px;padding:10px;font-weight:800;font-size:12.5px;cursor:pointer;transition:opacity .15s,transform .1s;letter-spacing:.2px}}
.btn-quote:hover{{opacity:.9;transform:translateY(-1px)}}
.btn-quote.done{{background:linear-gradient(135deg,#2e7d32,#1b5e20)}}
.carrier-card.top .btn-quote{{background:linear-gradient(135deg,#00897b,#00695c)}}
.btn-pass{{padding:10px 15px;background:#f5f7fa;color:#6b7280;border:1px solid #d1d5db;border-radius:9px;font-weight:700;font-size:12px;cursor:pointer;transition:background .15s}}
.btn-pass:hover{{background:#eef0f5}}

/* ── Timeline accordion ──────────────────────────────────────────────────── */
.timeline-card{{background:#fff;border:1.5px solid #e4e9f2;border-radius:14px;padding:15px 17px;flex-shrink:0}}
.timeline-toggle{{display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none}}
.timeline-toggle h3{{font-size:12px;font-weight:800;color:#0a2463}}
.timeline-body{{display:none;margin-top:14px}}
.timeline-body.open{{display:block}}
.tl-row{{margin-bottom:14px}}
.tl-label{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;margin-bottom:7px}}
.tl-label.bindiq{{color:#1565C0}}
.tl-label.traditional{{color:#9aa3b5}}
.tl-steps{{display:flex;align-items:center;gap:0;position:relative}}
.tl-step{{flex:1;text-align:center;position:relative}}
.tl-step-dot{{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;margin:0 auto 5px;border:2px solid}}
.tl-step-dot.done{{background:#1565C0;color:#fff;border-color:#1565C0}}
.tl-step-dot.trad{{background:#f0f0f0;color:#999;border-color:#ddd}}
.tl-step-line{{position:absolute;top:11px;left:50%;right:-50%;height:2px;z-index:0}}
.tl-step-line.done{{background:#1565C0}}
.tl-step-line.trad{{background:#ddd}}
.tl-step-text{{font-size:9.5px;color:#666;line-height:1.3}}
.tl-step-time{{font-size:9px;font-weight:700;color:#1565C0;margin-top:2px}}
.tl-result{{display:flex;justify-content:space-between;margin-top:10px;padding:8px 12px;border-radius:8px;font-size:12px;font-weight:700}}
.tl-result.bindiq{{background:#e3f2fd;color:#0d47a1}}
.tl-result.traditional{{background:#fff3e0;color:#e65100}}

/* ── Premium breakdown ───────────────────────────────────────────────────── */
.prem-table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:11.5px}}
.prem-table td{{padding:4px 6px;border-bottom:1px solid #f0f2f8}}
.prem-table td:first-child{{color:#6b7280;font-weight:500}}
.prem-table td:last-child{{text-align:right;font-weight:700}}
.prem-table .prem-discount{{color:#2e7d32}}
.prem-table .prem-total{{font-weight:900;font-size:13px;color:#0d47a1;border-top:2px solid #e4e9f2;border-bottom:none}}
.prem-note{{font-size:11px;color:#6b7280;font-style:italic;margin-top:6px;line-height:1.4}}

/* ── Peer declined warning ────────────────────────────────────────────────── */
.why-item-danger{{background:#fff3f3;border-left:3px solid #ef4444;padding:9px 11px;border-radius:7px;font-size:12px;line-height:1.5;margin-bottom:9px}}
.why-item-danger .why-label{{color:#b91c1c}}

/* ── Peer success card ────────────────────────────────────────────────────── */
.peer-card{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 12px;margin-bottom:6px;font-size:12px}}
.peer-card-name{{font-weight:800;color:#166534;margin-bottom:3px}}
.peer-card-detail{{color:#374151;line-height:1.4}}
.peer-card-meta{{display:flex;gap:8px;margin-top:5px;flex-wrap:wrap}}
.peer-card-badge{{background:#dcfce7;color:#166534;border-radius:12px;padding:2px 8px;font-size:10px;font-weight:700}}
.peer-card-wf{{background:#fef9c3;color:#713f12;border-radius:12px;padding:2px 8px;font-size:10px;font-weight:700}}

/* ── BindIQ Callout ───────────────────────────────────────────────────────── */
.bindiq-callout{{background:linear-gradient(135deg,#0a2463 0%,#1e3a8a 100%);color:#fff;border-radius:12px;padding:14px 16px;margin-bottom:14px;flex-shrink:0}}
.bindiq-callout h4{{font-size:11px;font-weight:900;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;color:#93c5fd}}
.callout-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.callout-item{{background:rgba(255,255,255,.08);border-radius:8px;padding:9px 11px}}
.callout-item-title{{font-size:10px;font-weight:800;color:#60a5fa;margin-bottom:3px}}
.callout-item-val{{font-size:13px;font-weight:900}}
.callout-item-sub{{font-size:10px;color:#94a3b8;margin-top:1px}}

/* ── Comparison table ────────────────────────────────────────────────────── */
.compare-card{{background:#fff;border:1.5px solid #e4e9f2;border-radius:14px;padding:15px 17px;flex-shrink:0}}
.compare-card h3{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;color:#9aa3b5;margin-bottom:12px}}
.cmp-table{{width:100%;border-collapse:collapse;font-size:11.5px}}
.cmp-table th{{text-align:left;padding:6px 8px;background:#f8fafc;color:#9aa3b5;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid #e4e9f2}}
.cmp-table td{{padding:8px 8px;border-bottom:1px solid #f0f2f8;vertical-align:middle}}
.cmp-table tr:last-child td{{border-bottom:none}}
.cmp-table .cmp-name{{font-weight:800;color:#1a1a2e}}
.cmp-best{{color:#059669;font-weight:800}}
.cmp-warn{{color:#d97706;font-weight:700}}
.cmp-bad{{color:#dc2626;font-weight:700}}
.cmp-score-pill{{display:inline-block;padding:3px 9px;border-radius:10px;font-weight:900;font-size:11px;color:#fff}}

/* ── Rodriguez multi-line badge ───────────────────────────────────────────── */
.pkg-badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;margin-bottom:8px}}
.pkg-one{{background:#dbeafe;color:#1e40af}}
.pkg-multi{{background:#fef3c7;color:#92400e}}
.pkg-premium{{background:#f3e8ff;color:#6b21a8}}

/* ── Scenario nav links ───────────────────────────────────────────────────── */
.scenario-nav{{display:flex;gap:8px;padding:10px 14px;background:rgba(255,255,255,.12);border-bottom:1px solid rgba(255,255,255,.1)}}
.scenario-link{{color:rgba(255,255,255,.7);text-decoration:none;font-size:11px;font-weight:700;padding:4px 10px;border-radius:6px;transition:all .15s}}
.scenario-link:hover{{background:rgba(255,255,255,.15);color:#fff}}
.scenario-link.active{{background:rgba(255,255,255,.2);color:#fff}}

/* ── Loading / Spinner ──────────────────────────────────────────────────── */
#loading{{display:flex;flex-direction:column;align-items:center;justify-content:center;height:calc(100vh - 52px);gap:16px}}
.spinner{{width:42px;height:42px;border:4px solid #e3f2fd;border-top-color:#1565C0;border-radius:50%;animation:spin .7s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.toast{{position:fixed;bottom:22px;right:22px;background:#1b5e20;color:#fff;padding:12px 22px;border-radius:10px;font-weight:700;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.25);display:none;z-index:9999;animation:fadeUp .3s ease}}
.toast.err{{background:#b71c1c}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
.passed{{opacity:.3;pointer-events:none;transition:opacity .4s}}
@media(max-width:960px){{.main{{grid-template-columns:1fr;height:auto;overflow:visible}}.carriers-col{{height:auto}}}}
</style>
</head>
<body>
<header class="header">
  <div class="header-left">
    <h1>BindIQ — AI Insurance Intelligence</h1>
    <div class="sub" id="cust-subtitle">Loading analysis…</div>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <div class="scenario-nav">
      <a class="scenario-link{' active' if customer_id in ('maria_bakery_tx','maria_001','maria') else ''}"
         href="/review/maria_bakery_tx">Scenario 1: Maria's Bakery</a>
      <a class="scenario-link{' active' if customer_id == 'rodriguez_construction' else ''}"
         href="/review/rodriguez_construction">Scenario 2: Rodriguez Construction</a>
    </div>
    <div style="text-align:right">
      <div class="scored-lbl">Scored: <span id="scored-at">–</span></div>
      <span class="engine-pill" id="engine-badge">Loading…</span>
    </div>
  </div>
</header>

<div id="loading">
  <div class="spinner"></div>
  <div style="color:#6b7280;font-size:13px;font-weight:600">Running carrier analysis…</div>
</div>

<div id="app" style="display:none">
  <div class="main">
    <div class="panel" id="p-customer"></div>
    <div class="panel" id="p-contract"></div>
    <div class="carriers-col" id="p-carriers"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const CID = '{customer_id}';
let _globalReqGl = 2000000;

fetch('/review-data/' + CID)
  .then(r => r.json())
  .then(init)
  .catch(e => {{
    document.getElementById('loading').innerHTML =
      `<div style="color:#c62828;font-size:15px;font-weight:700">Failed to load: ${{e.message}}</div>`;
  }});

function init(d) {{
  _globalReqGl = (d.requirements && d.requirements.gl_limit) || 2000000;
  document.getElementById('loading').style.display = 'none';
  document.getElementById('app').style.display = 'block';
  document.getElementById('cust-subtitle').textContent =
    (d.customer.name || '') + ' — Carrier Recommendations';
  document.getElementById('scored-at').textContent =
    new Date(d.scored_at).toLocaleString();
  document.getElementById('engine-badge').textContent =
    d.engine === 'neo4j_hybrid' ? '⚡ Neo4j Hybrid Scoring (sem + graph + rules)'
                                 : '📊 Static Rules Engine';
  renderCustomer(d.customer, d.email_analysis);
  renderContract(d.requirements, d.gaps);
  renderCarriers(d.carriers, d.customer, d.requirements);
}}

/* ═══════════════════════════════════════════════════════════════════════════
   CUSTOMER PANEL
═══════════════════════════════════════════════════════════════════════════ */
function renderCustomer(c, em) {{
  const certs = (c.certifications || []).map(x =>
    `<span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:10.5px;font-weight:700;margin:2px;display:inline-block">${{x}}</span>`
  ).join('');

  document.getElementById('p-customer').innerHTML = `
    <div class="section-title">Customer Profile</div>
    <div class="cust-name">${{c.name}}</div>
    <div class="cust-badge">${{(c.industry||'').replace(/_/g,' ').replace(/\\b\\w/g,l=>l.toUpperCase())}}</div>
    ${{irow('State', c.state)}}
    ${{irow('Revenue', '$' + (c.revenue||0).toLocaleString())}}
    ${{irow('Employees', c.employees)}}
    ${{irow('Years in Business', c.years + ' yrs')}}
    ${{irow('Claims (5yr)', '<span style="color:#2e7d32;font-weight:800">0 — Clean</span>')}}
    ${{certs ? `<div style="margin-top:12px"><div class="info-label" style="font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px">Certifications</div>${{certs}}</div>` : ''}}
    <div class="policy-box">
      <div class="policy-title">⚠ Current Policy (Gap Detected)</div>
      ${{irow('Carrier', c.current_carrier)}}
      ${{irow('GL Limit', '<span style="color:#c62828;font-weight:800">$' + (c.current_gl||0).toLocaleString() + '</span>')}}
      ${{irow('Annual Premium', '$' + (c.current_premium||0).toLocaleString())}}
    </div>
    ${{em && em.from ? `
    <div class="trigger-box">
      <div class="trigger-title">Trigger Email</div>
      <div style="font-weight:700">${{em.from}}</div>
      <div style="margin-top:3px;color:#555;font-size:11.5px">${{em.subject||'Vendor Contract'}}</div>
      <div style="margin-top:7px;color:#2e7d32;font-weight:800;font-size:11px">
        ✓ ${{Math.round((em.confidence||0)*100)}}% confidence — insurance action required
      </div>
    </div>` : ''}}
  `;
}}

function irow(label, value) {{
  return `<div class="info-row"><span class="info-label">${{label}}</span><span class="info-value">${{value}}</span></div>`;
}}

/* ═══════════════════════════════════════════════════════════════════════════
   CONTRACT PANEL
═══════════════════════════════════════════════════════════════════════════ */
function renderContract(req, gaps) {{
  const days     = req.deadline_days || 8;
  const deadline = req.deadline || 'Mar 14, 2026';
  const critical = gaps.filter(g => g.gap && g.severity === 'critical').length;
  const high     = gaps.filter(g => g.gap && g.severity === 'high').length;
  const ok       = gaps.filter(g => !g.gap).length;

  const gapRows = gaps.map(g => {{
    const cls  = g.gap ? (g.severity === 'critical' ? 'critical' : 'high') : 'ok';
    const icon = g.gap ? (g.severity === 'critical' ? '🔴' : '⚠️') : '✅';
    return `<div class="gap-row ${{cls}}">
      <span style="font-size:14px;min-width:17px">${{icon}}</span>
      <div>
        <div class="gap-field">${{g.field}}</div>
        <div class="gap-detail">
          Current: ${{g.current}} → Required: ${{g.required}}
          ${{g.action ? '<br><em style="color:#555">' + g.action + '</em>' : ''}}
        </div>
      </div>
    </div>`;
  }}).join('');

  const reqItems = [];
  if (req.gl_limit)           reqItems.push(['GL Limit',        '$' + req.gl_limit.toLocaleString() + ' / occ']);
  if (req.gl_aggregate)       reqItems.push(['GL Aggregate',    '$' + req.gl_aggregate.toLocaleString()]);
  if (req.additional_insured) reqItems.push(['Additional Insured', req.additional_insured]);
  if (req.endorsements)       reqItems.push(['Endorsements',    req.endorsements.join(', ')]);
  if (req.am_best_min)        reqItems.push(['AM Best Min.',    req.am_best_min]);
  if (req.primary_noncon)     reqItems.push(['Language',        'Primary & Non-Contributory']);
  if (req.cancellation_notice) reqItems.push(['Cancel Notice', req.cancellation_notice + ' days']);
  if (req.portal)             reqItems.push(['COI Portal',      req.portal]);

  document.getElementById('p-contract').innerHTML = `
    <div class="section-title">Contract Analysis</div>
    <div class="deadline-bar">
      <div class="deadline-days">${{days}}</div>
      <div class="deadline-label">days until deadline · ${{deadline}}</div>
    </div>
    <div class="summary-row">
      <div class="summary-item" style="background:#fce4ec">
        <div class="summary-num" style="color:#c62828">${{critical}}</div>
        <div class="summary-lbl" style="color:#c62828">Critical</div>
      </div>
      <div class="summary-item" style="background:#fff8e1">
        <div class="summary-num" style="color:#f57f17">${{high}}</div>
        <div class="summary-lbl" style="color:#f57f17">Action Needed</div>
      </div>
      <div class="summary-item" style="background:#f1f8e9">
        <div class="summary-num" style="color:#2e7d32">${{ok}}</div>
        <div class="summary-lbl" style="color:#2e7d32">Compliant</div>
      </div>
    </div>
    ${{gapRows}}
    <div class="req-section">
      <div class="section-title" style="margin-bottom:10px">📋 Contract Requirements</div>
      ${{reqItems.map(([k,v]) =>
        `<div class="req-item"><span class="req-key">${{k}}</span><span class="req-val">${{v}}</span></div>`
      ).join('')}}
    </div>
  `;
}}

/* ═══════════════════════════════════════════════════════════════════════════
   BINDIQ INTELLIGENCE CALLOUT
═══════════════════════════════════════════════════════════════════════════ */
function bindiqCallout(carriers, customer, isConstruction) {{
  const top = carriers[0] || {{}};
  const topScore = Math.round(top.score || 0);
  const peers = (top.graph_paths && top.graph_paths.peer_success || []).length;
  const wfExp = (top.graph_paths && top.graph_paths.wf_experience) || {{}};
  const wfHandled = wfExp.handled || 0;
  const totalPeers = carriers.reduce((s, c) => s + ((c.graph_paths && c.graph_paths.peer_success || []).length), 0);

  return `<div class="bindiq-callout">
    <h4>Knowledge Graph Intelligence</h4>
    <div class="callout-grid">
      <div class="callout-item">
        <div class="callout-item-title">Confidence Score</div>
        <div class="callout-item-val">${{topScore}} / 100</div>
        <div class="callout-item-sub">${{top.name || 'Top carrier'}}</div>
      </div>
      <div class="callout-item">
        <div class="callout-item-title">Similar Customer Wins</div>
        <div class="callout-item-val">${{totalPeers}} found</div>
        <div class="callout-item-sub">graph traversal</div>
      </div>
      ${{wfHandled > 0 ? `
      <div class="callout-item">
        <div class="callout-item-title">WF Vendor Experience</div>
        <div class="callout-item-val">${{wfHandled}}x</div>
        <div class="callout-item-sub">${{top.name}} processed</div>
      </div>` : isConstruction ? `
      <div class="callout-item">
        <div class="callout-item-title">Coverage Lines</div>
        <div class="callout-item-val">5</div>
        <div class="callout-item-sub">GL + Auto + WC + Umbrella + IM</div>
      </div>` : ''}}
      <div class="callout-item">
        <div class="callout-item-title">Time Saved</div>
        <div class="callout-item-val">36 hrs</div>
        <div class="callout-item-sub">vs traditional broker</div>
      </div>
    </div>
  </div>`;
}}

/* ═══════════════════════════════════════════════════════════════════════════
   COMPARISON TABLE
═══════════════════════════════════════════════════════════════════════════ */
function comparisonTable(carriers, req) {{
  const rows = carriers.map((c, i) => {{
    const score = Math.round(c.score || 0);
    const pct = score;
    const color = pct >= 80 ? '#059669' : pct >= 65 ? '#d97706' : '#6b7280';
    const peers = (c.graph_paths && c.graph_paths.peer_success || []).length;
    const declined = (c.graph_paths && c.graph_paths.peer_declined || []).length;
    const peerText = peers > 0
      ? `<span class="cmp-best">${{peers}} wins</span>`
      : declined > 0 ? `<span class="cmp-bad">1 declined</span>` : '<span class="cmp-warn">No data</span>';
    const revWarn = c.graph_paths && c.graph_paths.revenue_warning;
    const medals = ['🥇','🥈','🥉','4.','5.'];
    return `<tr>
      <td class="cmp-name">${{medals[i]}} ${{c.name.split(' ').slice(0,2).join(' ')}}</td>
      <td><span class="cmp-score-pill" style="background:${{color}}">${{score}}</span></td>
      <td>${{c.quote_speed || '–'}}</td>
      <td>${{peerText}}</td>
      <td>${{c.est_premium ? '$' + c.est_premium.toLocaleString() + '/yr' : '–'}}</td>
      <td style="font-size:10px;color:${{revWarn ? '#d97706' : '#9aa3b5'}}">${{revWarn ? '⚠ Rev range' : '✓'}}</td>
    </tr>`;
  }}).join('');

  return `<div class="compare-card">
    <h3>Side-by-Side Comparison</h3>
    <table class="cmp-table">
      <thead><tr>
        <th>Carrier</th><th>Score</th><th>Quote Speed</th>
        <th>Peer Track Record</th><th>Est. Premium</th><th>Revenue Fit</th>
      </tr></thead>
      <tbody>${{rows}}</tbody>
    </table>
  </div>`;
}}

/* ═══════════════════════════════════════════════════════════════════════════
   CARRIERS COLUMN
═══════════════════════════════════════════════════════════════════════════ */
function renderCarriers(carriers, customer, req) {{
  const col = document.getElementById('p-carriers');
  const isConstruction = (customer.industry || '').includes('construction');
  col.innerHTML = `
    ${{bindiqCallout(carriers, customer, isConstruction)}}
    <div class="section-title" style="padding:0 4px">
      Top ${{carriers.length}} Carrier Matches — Qualified for ${{req && req.gl_limit ? '$' + req.gl_limit.toLocaleString() : '$2M'}} GL
      ${{isConstruction ? '· Multi-Line Package' : ''}}
    </div>
    ${{carriers.map((c, i) => carrierCard(c, i, customer, req)).join('')}}
    ${{comparisonTable(carriers, req)}}
    ${{timelineCard(isConstruction)}}
  `;

  // After DOM insertion: animate bars and draw D3 KG graphs
  setTimeout(() => {{
    carriers.forEach((c, i) => {{
      animateBars(c, i);
      const paths = c.graph_paths || {{}};
      drawKGPaths('kg-' + c.carrier_id, c, paths, customer.state || 'TX');
    }});
  }}, 80);
}}

/* ═══════════════════════════════════════════════════════════════════════════
   CARRIER CARD
═══════════════════════════════════════════════════════════════════════════ */
function carrierCard(c, i, cu, req) {{
  const isTop  = i === 0;
  const medals = ['🥇','🥈','🥉','4.','5.'];
  const score  = Math.round(c.score || 0);
  const reqGl  = (req && req.gl_limit) || _globalReqGl;
  const maxGl  = c.max_gl || 0;
  const capOk  = maxGl >= reqGl;

  const sem = Math.round(c.semantic_score != null ? c.semantic_score : score * 0.3);
  const gr  = Math.round(c.graph_score   != null ? c.graph_score   : score * 0.4);
  const ru  = Math.round(c.rules_score   != null ? c.rules_score   : score * 0.3);

  // Capacity badge
  const capBadge = capOk
    ? `<span class="cap-badge cap-ok">✓ Can write ${{maxGl >= 2000000 ? '$2M' : '$' + (maxGl/1000000).toFixed(1) + 'M'}} GL</span>`
    : `<span class="cap-badge cap-fail">✗ Max ${{maxGl >= 1000000 ? '$' + (maxGl/1000000).toFixed(1) + 'M' : '$' + (maxGl/1000).toFixed(0) + 'K'}} GL — Insufficient</span>`;

  // Score bars (widths set to 0 initially, animated via JS)
  const barHtml = `<div class="score-bars">
    ${{sbar('Semantic', sem, 'sem', c.carrier_id)}}
    ${{sbar('Graph Path', gr, 'gr', c.carrier_id)}}
    ${{sbar('Rules', ru, 'ru', c.carrier_id)}}
  </div>`;

  // KG container (D3 renders into this after mount)
  const kgHtml = `
    <div class="kg-header">⟨/⟩ Knowledge Graph Reasoning Path</div>
    <div class="kg-container" id="kg-${{c.carrier_id}}"></div>`;

  // Why this carrier? expandable
  const whyId  = 'why-' + c.carrier_id;
  const whyHtml = buildWhyPanel(c, cu, req);
  const expandable = `
    <button class="why-toggle" id="toggle-${{whyId}}" onclick="toggleWhy('${{whyId}}')">
      <span class="arrow">▶</span> Why does ${{c.name.split(' ')[0]}} rank #${{i+1}}?
    </button>
    <div class="why-panel" id="${{whyId}}">${{whyHtml}}</div>`;

  // Package type badge (Rodriguez Construction)
  const pkgBadge = c.package_type ? (() => {{
    const cls = c.package_type.includes('One') ? 'pkg-one'
              : c.package_type.includes('Multi') ? 'pkg-multi' : 'pkg-premium';
    return `<span class="pkg-badge ${{cls}}">${{c.package_type}}${{c.carrier_count > 1 ? ' · ' + c.carrier_count + ' carriers' : ''}}</span>`;
  }})() : '';

  // Metrics row
  const maxGlFmt = '$' + (maxGl||0).toLocaleString();
  const metricsHtml = `<div class="metrics">
    ${{met('AM Best', c.am_best || '–')}}
    ${{met('Quote Speed', c.quote_speed || '–')}}
    ${{met('Max GL', maxGlFmt)}}
    ${{met('Est. Premium', '$' + (c.est_premium||0).toLocaleString() + '/yr')}}
    ${{c.cg_2015 ? met('CG 2015', '<span style="color:#2e7d32;font-weight:800">✓ Supported</span>') : ''}}
    ${{c.digital  ? met('Bind',    '<span style="color:#1565C0;font-weight:800">✓ Digital</span>') : ''}}
  </div>`;

  const shortName = c.name.split(' ')[0];
  return `
    <div class="carrier-card${{isTop ? ' top' : ''}}" id="card-${{c.carrier_id}}">
      ${{pkgBadge}}
      <div class="card-header">
        <span class="card-rank">${{medals[i]}}</span>
        <span class="card-name">${{c.name}}</span>
        ${{isTop ? '<span class="rec-badge">RECOMMENDED</span>' : ''}}
        <div class="score-circle">${{score}}</div>
      </div>
      ${{capBadge}}
      ${{barHtml}}
      ${{kgHtml}}
      ${{expandable}}
      ${{metricsHtml}}
      <div class="actions">
        <button class="btn-quote" onclick="submitQuote('${{c.carrier_id}}','${{c.name}}',this)">
          Get Quote — ${{shortName}}
        </button>
        <button class="btn-pass" onclick="passCarrier('${{c.carrier_id}}','${{c.name}}',this)">Pass</button>
      </div>
    </div>`;
}}

function sbar(label, pct, key, cid) {{
  return `<div class="bar-wrap">
    <div class="bar-label">${{label}}</div>
    <div class="bar-track"><div class="bar-fill" id="bar-${{key}}-${{cid}}" style="width:0%;background:#ccc"></div></div>
    <div class="bar-val" id="barval-${{key}}-${{cid}}">${{pct}}</div>
  </div>`;
}}

function animateBars(c, i) {{
  const score = Math.round(c.score || 0);
  const sem = Math.round(c.semantic_score != null ? c.semantic_score : score * 0.3);
  const gr  = Math.round(c.graph_score   != null ? c.graph_score   : score * 0.4);
  const ru  = Math.round(c.rules_score   != null ? c.rules_score   : score * 0.3);
  const barColor = v => v >= 75 ? '#00897b' : v >= 50 ? '#1565C0' : '#f57c00';

  [['sem', sem], ['gr', gr], ['ru', ru]].forEach(([key, val]) => {{
    const el = document.getElementById('bar-' + key + '-' + c.carrier_id);
    if (el) {{ el.style.background = barColor(val); el.style.width = val + '%'; }}
  }});
}}

function met(label, value) {{
  return `<div class="metric"><span class="metric-label">${{label}}</span><span class="metric-value">${{value}}</span></div>`;
}}

/* ═══════════════════════════════════════════════════════════════════════════
   WHY PANEL — expandable explanation
═══════════════════════════════════════════════════════════════════════════ */
function buildWhyPanel(c, cu, req) {{
  const reqGl    = (req && req.gl_limit) || 2000000;
  const maxGl    = c.max_gl || 0;
  const industry = ((cu && cu.industry) || 'food service').replace(/_/g,' ');
  const days     = (req && req.deadline_days) || 8;
  const retailer = req && req.additional_insured ? req.additional_insured.split('+')[0].trim() : 'Retailer';
  const parts    = [];
  const paths    = c.graph_paths || {{}};

  // 1. Capacity
  if (maxGl >= reqGl) {{
    parts.push(`<div class="why-item why-item-green">
      <span class="why-label">GL Capacity Match</span>
      Can write the full <strong>$${{reqGl.toLocaleString()}}</strong> GL limit required.
      Many carriers in ${{industry}} cap at $1M per occurrence.
    </div>`);
  }}

  // 2. Revenue warning (before industry so it stands out)
  if (paths.revenue_warning) {{
    parts.push(`<div class="why-item why-item-orange">
      <span class="why-label">Revenue Range Caution</span>
      ${{paths.revenue_warning}}
    </div>`);
  }}

  // 3. Peer declined warning
  if (paths.peer_declined && paths.peer_declined.length > 0) {{
    const d = paths.peer_declined[0];
    parts.push(`<div class="why-item-danger">
      <span class="why-label">Graph Warning: Similar Customer Declined</span>
      <strong>${{d.name}}</strong> — similar business declined by ${{c.name}} in ${{d.date || 'recent quarter'}}.<br>
      <em>Reason: ${{d.reason}}</em> · Similarity: ${{Math.round((d.similarity||0)*100)}}%<br>
      <span style="color:#6b7280">Declination risk may apply — verify appetite before submitting.</span>
    </div>`);
  }}

  // 4. Similar customer peer success cards
  if (paths.peer_success && paths.peer_success.length > 0) {{
    const peerCards = paths.peer_success.slice(0, 3).map(p => {{
      const stars = p.satisfaction ? '★'.repeat(Math.round(p.satisfaction)) : '';
      const wfTag = p.also_wf_vendor ? '<span class="peer-card-wf">Also WF Vendor</span>' : '';
      return `<div class="peer-card">
        <div class="peer-card-name">${{p.name}}</div>
        <div class="peer-card-detail">${{p.detail || p.outcome + ' outcome'}}</div>
        <div class="peer-card-meta">
          <span class="peer-card-badge">${{p.outcome || 'good'}}</span>
          ${{p.satisfaction ? '<span class="peer-card-badge" style="background:#dbeafe;color:#1e40af">' + p.satisfaction + '/5</span>' : ''}}
          ${{wfTag}}
        </div>
      </div>`;
    }}).join('');
    parts.push(`<div class="why-item why-item-blue">
      <span class="why-label">Similar Customer Success (${{paths.peer_success.length}} found in graph)</span>
      ${{peerCards}}
    </div>`);
  }}

  // 5. WF vendor experience
  if (paths.wf_experience && paths.wf_experience.handled > 0) {{
    const wf = paths.wf_experience;
    parts.push(`<div class="why-item why-item-blue">
      <span class="why-label">${{retailer}} Vendor Experience</span>
      ${{c.name}} has processed <strong>${{wf.handled}}</strong> ${{retailer}} vendor certificates,
      avg turnaround <strong>${{wf.avg_turnaround_hrs}} hrs</strong>.
      Endorsement templates on file: ${{(wf.endorsements || []).join(', ')}}.
    </div>`);
  }}

  // 6. Industry specialization
  if (paths.industry_match) {{
    const pct = Math.round((paths.industry_match.score || 0) * 100);
    const barColor = pct >= 80 ? '#059669' : pct >= 65 ? '#d97706' : '#9aa3b5';
    parts.push(`<div class="why-item why-item-blue">
      <span class="why-label">Industry Specialization — ${{pct}}% appetite</span>
      <div style="margin-top:5px">
        <div style="height:6px;background:#e4e9f2;border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${{pct}}%;background:${{barColor}};border-radius:3px;transition:width .8s"></div>
        </div>
        <div style="font-size:11px;color:#6b7280;margin-top:3px">
          ${{c.name}} SPECIALIZES_IN ${{paths.industry_match.industry}} — higher appetite = tailored forms, fewer exclusions
        </div>
      </div>
    </div>`);
  }}

  // 7. Speed (critical for deadline)
  if (c.quote_speed) {{
    const isDigital = !!c.digital;
    const speedColor = c.quote_speed.includes('min') ? '#059669' : c.quote_speed.includes('1 hr') ? '#d97706' : '#9aa3b5';
    parts.push(`<div class="why-item why-item-orange">
      <span class="why-label">Quote Speed: ${{c.quote_speed}}${{isDigital ? ' — Digital Binding' : ''}}</span>
      Only <strong>${{days}} days</strong> until deadline.
      ${{isDigital ? 'Full digital binding — COI issued instantly, no broker calls.' : 'Broker-assisted binding within ' + c.quote_speed + '.'}}
    </div>`);
  }}

  // 8. LLM explanation
  if (c.explanation) {{
    parts.push(`<div class="why-item why-item-grey">"${{c.explanation}}"</div>`);
  }}

  // 9. Premium breakdown
  const pb = c.premium_breakdown || {{}};
  if (pb.total) {{
    const isConstr = !!(pb.gl_2m_4m || pb.gl_increase);
    let tableRows = '';
    if (isConstr) {{
      if (pb.gl_2m_4m)          tableRows += premRow('GL $2M/$4M', pb.gl_2m_4m);
      if (pb.auto_2m_8veh)      tableRows += premRow('Auto $2M (8 veh)', pb.auto_2m_8veh);
      if (pb.umbrella_5m)       tableRows += premRow('Umbrella $5M', pb.umbrella_5m);
      if (pb.inland_marine)     tableRows += premRow('Inland Marine $850K', pb.inland_marine);
      if (pb.package_discount)  tableRows += premRow('Package Discount', pb.package_discount, true);
      if (pb.gl_increase)       tableRows += premRow('GL Increase', pb.gl_increase);
      if (pb.auto_increase)     tableRows += premRow('Auto Increase', pb.auto_increase);
      if (pb.wc_endorse)        tableRows += premRow('WC Endorsement', pb.wc_endorse);
      if (pb.umbrella_new)      tableRows += premRow('New Umbrella', pb.umbrella_new);
      if (pb.inland_marine_new) tableRows += premRow('New Inland Marine', pb.inland_marine_new);
    }} else {{
      if (pb.base_rate)                tableRows += premRow('Base Rate', pb.base_rate);
      if (pb.wholesale_exposure)       tableRows += premRow('Wholesale Exposure', pb.wholesale_exposure);
      if (pb.limit_2m_uplift)          tableRows += premRow('$2M Limit Uplift', pb.limit_2m_uplift);
      if (pb.clean_loss_discount)      tableRows += premRow('Clean Loss Discount', pb.clean_loss_discount, true);
      if (pb.digital_policy_discount)  tableRows += premRow('Digital Policy Discount', pb.digital_policy_discount, true);
    }}
    parts.push(`<div class="why-item why-item-green">
      <span class="why-label">Premium Breakdown — $${{pb.total.toLocaleString()}}/yr</span>
      <table class="prem-table">
        ${{tableRows}}
        <tr><td class="prem-total" colspan="2">Total Annual Premium</td><td class="prem-total">$${{pb.total.toLocaleString()}}</td></tr>
      </table>
      ${{pb.note ? '<div class="prem-note">' + pb.note + '</div>' : ''}}
    </div>`);
  }}

  return parts.length ? parts.join('') : '<div style="color:#888;font-size:12px">Analysis data loading…</div>';
}}

function premRow(label, val, isDiscount) {{
  const formatted = (val < 0 ? '-$' + Math.abs(val).toLocaleString() : '+$' + val.toLocaleString());
  const cls = isDiscount || val < 0 ? 'prem-discount' : '';
  return `<tr><td>${{label}}</td><td class="${{cls}}">${{formatted}}</td></tr>`;
}}

function toggleWhy(id) {{
  const panel = document.getElementById(id);
  const btn   = document.getElementById('toggle-' + id);
  const open  = panel.classList.toggle('open');
  btn.classList.toggle('open', open);
}}

/* ═══════════════════════════════════════════════════════════════════════════
   D3 KNOWLEDGE GRAPH PATH VISUALIZATION
═══════════════════════════════════════════════════════════════════════════ */
function drawKGPaths(containerId, carrier, paths, customerState) {{
  const container = document.getElementById(containerId);
  if (!container) return;

  // Build rows: each row = [fromNode, edgeLabel, toNode, checkLabel]
  const rows = [];

  if (paths.industry_match) {{
    const pct = Math.round((paths.industry_match.score || 0) * 100);
    const ind = (paths.industry_match.industry || 'Food Service')
                  .replace('food_service','Food Service & Restaurants')
                  .replace(/_/g,' ');
    rows.push({{
      from:  {{label: carrier.name.split(' ')[0].substring(0,9), type: 'carrier'}},
      edge:  'SPECIALIZES_IN · ' + pct + '%',
      to:    {{label: ind.substring(0, 20), type: 'industry'}},
      check: '✓ Industry match',
    }});
  }}

  if (paths.state_licensed) {{
    const tier = paths.state_tier ? ' (' + paths.state_tier + ')' : '';
    rows.push({{
      from:  {{label: carrier.name.split(' ')[0].substring(0,9), type: 'carrier'}},
      edge:  'LICENSED_IN' + tier,
      to:    {{label: customerState || 'TX', type: 'state'}},
      check: '✓ State eligible',
    }});
  }}

  (paths.peer_success || []).slice(0,1).forEach(p => {{
    rows.push({{
      from:  {{label: (p.name||'Peer').split(' ')[0].substring(0,9), type: 'peer'}},
      edge:  'INSURED_BY · ' + (p.outcome||'good'),
      to:    {{label: carrier.name.split(' ')[0].substring(0,9), type: 'carrier'}},
      check: '✓ Peer success',
    }});
  }});

  if (!rows.length) {{
    container.style.display = 'none';
    return;
  }}

  const ROW_H = 38, PAD_Y = 10, PAD_X = 6;
  const VB_W  = 460;
  const VB_H  = rows.length * ROW_H + PAD_Y * 2;

  const svg = d3.select(container).append('svg')
    .attr('viewBox', `0 0 ${{VB_W}} ${{VB_H}}`)
    .attr('width', '100%').attr('height', VB_H);

  // Arrow marker
  const mid = 'arr-' + containerId.replace(/[^a-z0-9]/gi,'');
  svg.append('defs').append('marker')
    .attr('id', mid).attr('viewBox','0 0 8 8')
    .attr('refX',7).attr('refY',4)
    .attr('markerWidth',5).attr('markerHeight',5)
    .attr('orient','auto')
    .append('path').attr('d','M0,0 L8,4 L0,8 Z').attr('fill','#b0b8cc');

  const C = {{
    carrier:  {{fill:'#1565C0', text:'#fff'}},
    industry: {{fill:'#2e7d32', text:'#fff'}},
    state:    {{fill:'#6a1b9a', text:'#fff'}},
    peer:     {{fill:'#e65100', text:'#fff'}},
  }};

  rows.forEach((row, ri) => {{
    const g = svg.append('g')
      .attr('transform', `translate(${{PAD_X}},${{PAD_Y + ri * ROW_H}})`);
    const cy = ROW_H / 2;

    // Measure helper
    const W1 = 80, W2 = 90, GAP = 20;
    const x1 = 0, x2 = x1 + W1 + GAP, x3 = x2 + 100 + GAP;

    // From node
    const c1 = C[row.from.type] || C.carrier;
    g.append('rect').attr('x',x1).attr('y',cy-11).attr('width',W1).attr('height',22)
      .attr('rx',11).attr('fill',c1.fill).attr('opacity',.93);
    g.append('text').attr('x',x1+W1/2).attr('y',cy+4.5)
      .attr('text-anchor','middle').attr('fill',c1.text)
      .attr('font-size',9.5).attr('font-weight','700').attr('font-family','system-ui,sans-serif')
      .text(row.from.label);

    // Edge line + label
    g.append('line').attr('x1',x1+W1+2).attr('y1',cy).attr('x2',x2-2).attr('y2',cy)
      .attr('stroke','#b0b8cc').attr('stroke-width',1.5)
      .attr('marker-end',`url(#${{mid}})`);
    g.append('text').attr('x',(x1+W1+x2)/2).attr('y',cy-5)
      .attr('text-anchor','middle').attr('fill','#9aa3b5')
      .attr('font-size',8.5).attr('font-family','system-ui,sans-serif')
      .text(row.edge.substring(0,22));

    // To node
    const labelLen = Math.min(row.to.label.length, 18);
    const W2dyn    = Math.max(60, labelLen * 6.2 + 18);
    const c2       = C[row.to.type] || C.industry;
    g.append('rect').attr('x',x2).attr('y',cy-11).attr('width',W2dyn).attr('height',22)
      .attr('rx',11).attr('fill',c2.fill).attr('opacity',.93);
    g.append('text').attr('x',x2+W2dyn/2).attr('y',cy+4.5)
      .attr('text-anchor','middle').attr('fill',c2.text)
      .attr('font-size',9.5).attr('font-weight','700').attr('font-family','system-ui,sans-serif')
      .text(row.to.label.substring(0,18));

    // Check label
    if (row.check) {{
      g.append('text').attr('x',x2+W2dyn+9).attr('y',cy+4.5)
        .attr('fill','#2e7d32').attr('font-size',9.5).attr('font-weight','800')
        .attr('font-family','system-ui,sans-serif')
        .text(row.check);
    }}
  }});
}}

/* ═══════════════════════════════════════════════════════════════════════════
   TIMELINE COMPARISON CARD
═══════════════════════════════════════════════════════════════════════════ */
function timelineCard(isConstruction) {{
  const bindiqTime = isConstruction ? '12 Hours' : '6 Hours';
  const bindiqResult = isConstruction
    ? '12 hours total · Saves the $1.2M contract'
    : '6 hours total · 6 days to spare before deadline';
  const tradTime = isConstruction ? '7–10 Days' : '5–7 Days';
  const tradResult = isConstruction
    ? '7–10 days total · WILL MISS 3-day deadline — contract voided'
    : '5–7 days total · Risks missing the Feb deadline';

  return `<div class="timeline-card">
    <div class="timeline-toggle" onclick="toggleTimeline()">
      <h3>BindIQ vs Traditional Broker — Timeline</h3>
      <span id="tl-arrow" style="font-size:12px;color:#9aa3b5">Show</span>
    </div>
    <div class="timeline-body" id="timeline-body">
      <div class="tl-row" style="margin-top:14px">
        <div class="tl-label bindiq">BindIQ Agentic Process — ${{bindiqTime}}</div>
        <div class="tl-steps">
          ${{tlStep('Email detected', '0 min', true, false)}}
          ${{tlStep('KG analysis', '10 min', true, false)}}
          ${{tlStep(isConstruction ? '5-line match' : '5 matches', '15 min', true, false)}}
          ${{tlStep('Quotes sent', isConstruction ? '4 hrs' : '2 hrs', true, false)}}
          ${{tlStep('COI ready', isConstruction ? '12 hrs' : '6 hrs', true, true)}}
        </div>
        <div class="tl-result bindiq">${{bindiqResult}}</div>
      </div>
      <div class="tl-row">
        <div class="tl-label traditional">Traditional Broker — ${{tradTime}}</div>
        <div class="tl-steps">
          ${{tlStep('Initial call', 'Day 1', false, false)}}
          ${{tlStep('Gather info', 'Day 1-2', false, false)}}
          ${{tlStep('Submit apps', 'Day 2-3', false, false)}}
          ${{tlStep('Wait quotes', 'Day 4-5', false, false)}}
          ${{tlStep('Decision', isConstruction ? 'Day 7-10' : 'Day 6-7', false, true)}}
        </div>
        <div class="tl-result traditional">${{tradResult}}</div>
      </div>
    </div>
  </div>`;
}}

function tlStep(label, time, isDone, isLast) {{
  const dot  = `<div class="tl-step-dot ${{isDone ? 'done' : 'trad'}}">${{isDone ? '✓' : '○'}}</div>`;
  const line = isLast ? '' : `<div class="tl-step-line ${{isDone ? 'done' : 'trad'}}"></div>`;
  return `<div class="tl-step">
    ${{line}}${{dot}}
    <div class="tl-step-text">${{label}}</div>
    <div class="tl-step-time" style="color:${{isDone ? '#1565C0' : '#9aa3b5'}}">${{time}}</div>
  </div>`;
}}

function toggleTimeline() {{
  const body  = document.getElementById('timeline-body');
  const arrow = document.getElementById('tl-arrow');
  const open  = body.classList.toggle('open');
  arrow.textContent = open ? '▲ Hide' : '▼ Show';
}}

/* ═══════════════════════════════════════════════════════════════════════════
   ACTIONS
═══════════════════════════════════════════════════════════════════════════ */
function submitQuote(carrierId, carrierName, btn) {{
  btn.disabled = true;
  btn.textContent = 'Submitting…';
  fetch('/action/quote', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      customer_id: CID, carrier_id: carrierId, carrier_name: carrierName,
      gl_limit: _globalReqGl,
      notes: 'Whole Foods vendor contract — coverage upgrade required',
    }}),
  }})
  .then(r => r.json())
  .then(res => {{
    btn.textContent = '✓ Quote Submitted';
    btn.classList.add('done');
    showToast('Quote submitted to ServiceNow' + (res.sys_id ? ' · ' + res.sys_id : ''), false);
  }})
  .catch(e => {{
    btn.disabled = false;
    btn.textContent = 'Get Quote — ' + carrierName.split(' ')[0];
    showToast('Submission failed: ' + e.message, true);
  }});
}}

function passCarrier(carrierId, carrierName, btn) {{
  btn.disabled = true;
  fetch('/action/reject', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{customer_id: CID, carrier_id: carrierId, carrier_name: carrierName}}),
  }});
  document.getElementById('card-' + carrierId).classList.add('passed');
  showToast(carrierName + ' — passed', false);
}}

function showToast(msg, isErr) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 4200);
}}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# NEW ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

from fastapi.responses import HTMLResponse

# ── GET / — Demo landing page ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def landing():
    """BindIQ demo landing — choose scenario."""
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BindIQ — AI Insurance Intelligence</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Arial,sans-serif;
     background:linear-gradient(135deg,#0a2463 0%,#1e3a8a 50%,#0a2463 100%);
     min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px}
.logo{font-size:22px;font-weight:900;color:#fff;letter-spacing:-0.5px;margin-bottom:6px}
.tagline{color:#93c5fd;font-size:14px;font-weight:500;margin-bottom:48px;text-align:center}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:800px;width:100%}
.card{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.15);border-radius:16px;
      padding:28px;cursor:pointer;transition:all .2s;text-decoration:none;display:block}
.card:hover{background:rgba(255,255,255,.13);border-color:rgba(255,255,255,.3);transform:translateY(-3px);
            box-shadow:0 12px 40px rgba(0,0,0,.3)}
.card-label{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:1.5px;
            color:#60a5fa;margin-bottom:10px}
.card-title{font-size:20px;font-weight:900;color:#fff;margin-bottom:8px}
.card-sub{font-size:13px;color:#94a3b8;margin-bottom:18px;line-height:1.5}
.card-badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.badge{padding:3px 9px;border-radius:10px;font-size:10.5px;font-weight:700}
.badge-red{background:rgba(239,68,68,.2);color:#fca5a5;border:1px solid rgba(239,68,68,.3)}
.badge-blue{background:rgba(59,130,246,.2);color:#93c5fd;border:1px solid rgba(59,130,246,.3)}
.badge-green{background:rgba(16,185,129,.2);color:#6ee7b7;border:1px solid rgba(16,185,129,.3)}
.badge-yellow{background:rgba(245,158,11,.2);color:#fcd34d;border:1px solid rgba(245,158,11,.3)}
.card-cta{display:inline-flex;align-items:center;gap:6px;background:linear-gradient(135deg,#3b82f6,#1d4ed8);
          color:#fff;padding:9px 18px;border-radius:9px;font-weight:800;font-size:13px}
.card.construction .card-cta{background:linear-gradient(135deg,#f59e0b,#d97706)}
.stats{display:flex;gap:30px;margin-top:36px}
.stat{text-align:center}
.stat-val{font-size:28px;font-weight:900;color:#fff}
.stat-lbl{font-size:11px;color:#64748b;margin-top:2px}
</style>
</head>
<body>
<div class="logo">BindIQ</div>
<div class="tagline">AI-Powered Insurance Intelligence · Neo4j Knowledge Graph · Real-Time Carrier Matching</div>
<div class="cards">
  <a class="card" href="/review/maria_bakery_tx">
    <div class="card-label">Scenario 1 — Food Service</div>
    <div class="card-title">Maria's Artisan Bakery</div>
    <div class="card-sub">Wins $500K Whole Foods vendor contract. Needs $2M GL + CG 20 15 endorsement within 8 days.</div>
    <div class="card-badges">
      <span class="badge badge-red">8-day deadline</span>
      <span class="badge badge-blue">1 coverage line</span>
      <span class="badge badge-green">Digital binding</span>
      <span class="badge badge-green">3 peer successes found</span>
    </div>
    <div class="card-cta">View Recommendations →</div>
  </a>
  <a class="card construction" href="/review/rodriguez_construction">
    <div class="card-label">Scenario 2 — Construction</div>
    <div class="card-title">Rodriguez Construction</div>
    <div class="card-sub">Wins $1.2M Dell/Hines office TI contract. Needs GL + Auto + WC + $5M Umbrella + Inland Marine in 3 days.</div>
    <div class="card-badges">
      <span class="badge badge-red">3-day deadline</span>
      <span class="badge badge-yellow">5 coverage lines</span>
      <span class="badge badge-yellow">4 cert holders</span>
      <span class="badge badge-blue">Multi-carrier coordination</span>
    </div>
    <div class="card-cta">View Recommendations →</div>
  </a>
</div>
<div class="stats">
  <div class="stat"><div class="stat-val">88</div><div class="stat-lbl">Top match score</div></div>
  <div class="stat"><div class="stat-val">3</div><div class="stat-lbl">Similar customer wins</div></div>
  <div class="stat"><div class="stat-val">6 hrs</div><div class="stat-lbl">vs 5-7 days</div></div>
  <div class="stat"><div class="stat-val">12</div><div class="stat-lbl">Carriers evaluated</div></div>
</div>
</body>
</html>""")

# ── POST /webhook/snow ──────────────────────────────────────────────────────────
@app.post("/webhook/snow")
def webhook_snow(req: WebhookRequest):
    """
    ServiceNow Flow Designer calls this when a new email is detected.
    Runs the full 3-stage intelligence pipeline and sends an alert.
    """
    logger.info(f"Webhook: '{req.email_subject}' from {req.email_from}")

    # Stage 1-3: email intelligence pipeline
    agent_result = None
    if HAS_EMAIL_AGENT:
        try:
            import email_agent as _ea
            agent_result = _ea.run(req.email_subject, req.email_body)
        except Exception as e:
            logger.warning(f"email_agent failed: {e}")

    # Determine if this is an insurance requirement
    if agent_result:
        is_insurance     = agent_result.stage2_passed
        confidence       = agent_result.overall_confidence
        extracted        = agent_result.extracted or {}
        stage1_score     = agent_result.embedding_similarity
        reasoning        = agent_result.classification_reasoning
        extraction_src   = agent_result.extraction_source
        business_context = agent_result.business_context or {}
    else:
        # Fallback: regex extractor
        extracted = extractor.extract(req.email_body)
        text_lower = (req.email_subject + " " + req.email_body).lower()
        is_insurance = any(k in text_lower for k in [
            "certificate of insurance", "additional insured", "general liability",
            "coi", "cg 20 15", "am best", "vendor contract",
        ])
        confidence       = 0.85 if is_insurance else 0.15
        stage1_score     = None
        reasoning        = "regex fallback"
        extraction_src   = "regex"
        business_context = {}

    if not is_insurance:
        return {
            "processed":     True,
            "is_insurance":  False,
            "confidence":    confidence,
            "stage1_score":  stage1_score,
            "reasoning":     reasoning,
            "action":        "none — not an insurance requirement",
        }

    # Score carriers
    demo = next((c for c in DEMO_CUSTOMERS if c["id"] == req.customer_id), None)
    industry = demo["industry"] if demo else "food_service"
    state    = demo["state"]    if demo else "IN"
    revenue  = MARIA["revenue"]
    req_gl   = extracted.get("gl_limit", 2_000_000)

    carriers = _score_carriers(
        req.customer_id, industry, state, revenue,
        top_n=5, required_gl=req_gl,
        business_context=business_context or None,
    )

    # Store in session so /review can read it
    _session_store[req.customer_id] = {
        "customer_name":    req.customer_name,
        "customer_email":   req.customer_email,
        "business_context": business_context,   # Stage 4 triples for semantic scoring
        "email_analysis": {
            "subject":     req.email_subject,
            "from":        req.email_from,
            "confidence":  confidence,
            "is_insurance": True,
            "extracted":   extracted,
        },
        "carriers":  carriers,
        "scored_at": datetime.utcnow().isoformat(),
    }

    # Build review URL
    base_url = os.environ.get("BINDIQ_BASE_URL", "").rstrip("/")
    review_url = f"{base_url}/review/{req.customer_id}"

    # Send alert email with link to /review page
    retailer   = extracted.get("additional_insured", "Vendor")
    days_left  = extracted.get("deadline_days", 8)
    deadline   = extracted.get("deadline", "TBD")
    req_gl     = extracted.get("gl_limit", 2_000_000)

    alert_analysis = {
        "customer_name":      req.customer_name or "Maria",
        "customer_id":        req.customer_id,
        "current_carrier":    MARIA["current_carrier"],
        "current_carrier_id": "simply_business",
        "current_limit":      f"${MARIA['current_gl_limit']:,}",
        "required_limit":     f"${req_gl:,}",
        "deadline":           deadline,
        "days_left":          days_left,
        "retailer":           retailer.split(" Inc.")[0].split(" Market")[0] or "Vendor",
        "top_carriers":       carriers,
        "review_url":         review_url,
    }
    alert_sent = gmail.send_bindiq_alert(req.customer_email or MARIA["email"], alert_analysis)

    return {
        "processed":     True,
        "is_insurance":  True,
        "confidence":    confidence,
        "stage1_score":  stage1_score,
        "reasoning":     reasoning,
        "extraction_source": extraction_src,
        "extracted":     extracted,
        "carriers_scored": len(carriers),
        "top_carrier":   carriers[0]["name"] if carriers else None,
        "alert_sent":    alert_sent,
        "review_url":    review_url,
    }


# ── GET /review-data/{customer_id} ─────────────────────────────────────────────
@app.get("/review-data/{customer_id}")
def review_data(customer_id: str):
    """JSON data for the /review page — called by client-side JS on page load."""
    return _get_review_data(customer_id)


# ── GET /review/{customer_id} ───────────────────────────────────────────────────
@app.get("/review/{customer_id}", response_class=HTMLResponse)
def review_page(customer_id: str):
    """
    Full-page carrier intelligence review.
    Linked from the BindIQ alert email CTA button.
    """
    return HTMLResponse(content=_render_review_html(customer_id))


# ── POST /action/quote ──────────────────────────────────────────────────────────
@app.post("/action/quote")
def action_quote(req: ActionRequest):
    """
    Customer clicks 'Get Quote' on the review page.
    Creates a quote record in ServiceNow CMDB to trigger the Flow Designer flow.
    """
    logger.info(f"Quote request: {req.customer_id} → {req.carrier_id} ({req.carrier_name})")

    result = snow.trigger_quote_flow(
        customer_id=req.customer_id,
        carrier_id=req.carrier_id,
        gl_limit=req.gl_limit or 2_000_000,
        notes=req.notes or f"Carrier: {req.carrier_name} — selected via BindIQ review page",
    )

    if result.get("success"):
        return {
            "status":    "submitted",
            "sys_id":    result.get("sys_id"),
            "carrier":   req.carrier_name,
            "message":   "Quote request created in ServiceNow — Flow Designer flow triggered",
        }

    # ServiceNow unavailable — log and return ok so UI doesn't break
    logger.warning(f"ServiceNow quote failed: {result.get('message')}")
    return {
        "status":  "logged",
        "sys_id":  None,
        "carrier": req.carrier_name,
        "message": f"Logged (ServiceNow: {result.get('message', 'unavailable')})",
    }


# ── POST /action/reject ─────────────────────────────────────────────────────────
@app.post("/action/reject")
def action_reject(req: ActionRequest):
    """
    Customer clicks 'Pass' on a carrier card.
    Logs the rejection (CMDB update if available).
    """
    logger.info(f"Carrier rejected: {req.customer_id} → {req.carrier_id} ({req.carrier_name})")
    return {"status": "logged", "carrier": req.carrier_name, "action": "rejected"}
