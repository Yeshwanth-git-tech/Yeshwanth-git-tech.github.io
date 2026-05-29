"""
BindIQ — AI Insurance Intelligence Platform
Streamlit Demo App

Scenario: Maria's Artisan Bakery receives a Whole Foods vendor contract.
          BindIQ automatically detects the insurance gap, scores carriers,
          and sends Maria a proactive alert with recommendations.

Run:
  cd UI && streamlit run app.py
"""

import os
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime

import streamlit as st

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
KG_DIR   = BASE_DIR.parent / "KnowledgeGraph"
sys.path.insert(0, str(KG_DIR))
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(KG_DIR / ".env")

# ── KnowledgeGraph imports ────────────────────────────────────────────────────
try:
    from gap_analyzer import CoverageRequirement, CurrentPolicy, detect_gaps, find_carriers_for_gap
    from carrier_capabilities import CARRIERS, can_write_in_state, can_quote_at_revenue
    HAS_GAP = True
except Exception:
    HAS_GAP = False

try:
    from neo4j import GraphDatabase
    from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
    _drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    _drv.verify_connectivity()
    _drv.close()
    HAS_NEO4J = True
except Exception:
    HAS_NEO4J = False

try:
    import scoring as _scoring
    HAS_SCORING = HAS_NEO4J
except Exception:
    HAS_SCORING = False

# ── Local module imports ──────────────────────────────────────────────────────
import email_watcher        as gmail
import requirement_extractor as extractor
import snow_setup           as snow

try:
    import email_agent
    HAS_EMAIL_AGENT = True
except Exception:
    HAS_EMAIL_AGENT = False

try:
    from pyvis.network import Network
    import streamlit.components.v1 as stc
    HAS_PYVIS = True
except Exception:
    HAS_PYVIS = False

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BindIQ — AI Insurance Intelligence",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.email-card {
    background: #f8f9fa;
    border-left: 4px solid #1976D2;
    padding: 1.2rem 1.4rem;
    border-radius: 4px;
    font-family: 'Courier New', monospace;
    font-size: 0.82rem;
    line-height: 1.6;
    white-space: pre-wrap;
}
.email-meta { color: #555; margin-bottom: 0.5rem; }
.highlight  { background: #FFF176; padding: 1px 3px; border-radius: 2px; }
.gap-row-bad  { color: #c62828; font-weight: 600; }
.gap-row-ok   { color: #2e7d32; }
.carrier-card {
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.6rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.score-chip {
    display: inline-block;
    background: #1565C0;
    color: #fff;
    border-radius: 20px;
    padding: 3px 12px;
    font-weight: 700;
    font-size: 1rem;
}
.step-done    { color: #2e7d32; font-weight: 600; }
.step-pending { color: #9e9e9e; }
.alert-box {
    background: #FFF3E0;
    border-left: 4px solid #F57C00;
    padding: 1rem 1.4rem;
    border-radius: 4px;
    font-size: 0.88rem;
}
.snow-ok   { color: #2e7d32; font-weight: 600; }
.snow-warn { color: #e65100; font-weight: 600; }
.status-dot-ok   { color: #4caf50; }
.status-dot-warn { color: #ff9800; }
.status-dot-err  { color: #f44336; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS — Maria's Scenario
# ═════════════════════════════════════════════════════════════════════════════

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
TO:   warantheyanesh@gmail.com
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
  • General Liability: $2,000,000 per occurrence / $4,000,000 aggregate
  • Additional Insured: Whole Foods Market Inc. (CG 20 15 endorsement required)
  • Carrier Rating: AM Best A- or better
  • Primary & Non-Contributory language required
  • 30-day cancellation notice to certificate holder

Register at: https://exigis.com/wholefoods

First delivery: Saturday, March 14  (8 days from now!)

Questions? Call me at 512-555-FOOD

Jordan Smith
Regional Vendor Coordinator
Whole Foods Market — Midwest Region
"""

WF_REQUIREMENTS = {
    "gl_limit":           2_000_000,
    "additional_insured": "Whole Foods Market Inc.",
    "endorsement":        "CG 20 15 (Broad Form Vendor)",
    "am_best_min":        "A-",
    "primary_noncon":     True,
    "cancel_notice":      30,
    "deadline":           "Mar 14, 2026",
    "deadline_days":      8,
}

DEMO_CUSTOMERS = [
    {"id": "maria_bakery_tx",       "name": "Maria's Artisan Bakery",    "industry": "food_service",    "state": "TX"},
    {"id": "atlas_construction_oh", "name": "Atlas Commercial Contractors","industry": "construction",   "state": "OH"},
    {"id": "cloudpeak_tech_ca",     "name": "CloudPeak Technology",       "industry": "technology",     "state": "CA"},
    {"id": "fastlane_logistics_tx", "name": "FastLane Logistics",         "industry": "logistics_transport","state": "TX"},
    {"id": "sparkle_clean_ca",      "name": "Sparkle Commercial Cleaning","industry": "cleaning_services","state": "CA"},
    {"id": "greenthumb_landscape_tx","name": "GreenThumb Landscape",      "industry": "landscaping",    "state": "TX"},
    {"id": "frontier_foods_oh",     "name": "Frontier Foods Manufacturing","industry": "food_manufacturing","state":"OH"},
]


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _carrier_metadata(cid: str) -> dict:
    """
    Return display-only metadata (am_best, quote_speed, max_gl, cg_2015, digital)
    for a carrier ID. Uses carrier_capabilities when available, otherwise safe defaults.
    """
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
            "max_gl":      f"${max_gl:,.0f}",
            "cg_2015":     getattr(c, "supports_cg_2015", True),
            "digital":     getattr(c, "allows_online_binding", False),
        }
    return {
        "am_best":     "A rated",
        "quote_speed": "varies",
        "max_gl":      "$2,000,000",
        "cg_2015":     True,
        "digital":     False,
    }


def score_carriers_for_maria() -> list[dict]:
    """
    Score all carriers for Maria's Whole Foods requirement.
    Uses Neo4j hybrid scoring (semantic + graph + rules) when available;
    falls back to static gap-analysis rules.
    """
    # ── Primary: Neo4j hybrid scoring ─────────────────────────────────────────
    if HAS_SCORING:
        neo4j_rankings = _neo4j_score("maria_bakery_tx")
        if neo4j_rankings:
            results = []
            for r in neo4j_rankings:
                cid  = r["carrier_id"]
                meta = _carrier_metadata(cid)
                results.append({
                    "carrier_id":      cid,
                    "name":            r["carrier_name"],
                    "score":           r["total_score"],
                    "am_best":         meta["am_best"],
                    "quote_speed":     meta["quote_speed"],
                    "max_gl":          meta["max_gl"],
                    "cg_2015":         meta["cg_2015"],
                    "digital":         meta["digital"],
                    "est_premium":     _estimate_premium(cid, MARIA["revenue"]),
                    "explanation":     r.get("explanation", ""),
                    "semantic_score":  r.get("semantic_score", 0),
                    "graph_score":     r.get("graph_score",   0),
                    "rules_score":     r.get("rules_score",   0),
                    "source":          "neo4j_hybrid",
                })
            return results

    # ── Fallback: static capability rules ─────────────────────────────────────
    if not HAS_GAP:
        return _static_carrier_results()

    results = []
    for cid, carrier in CARRIERS.items():
        if not can_write_in_state(carrier, MARIA["state"]):
            continue
        if not can_quote_at_revenue(carrier, MARIA["revenue"]):
            continue
        if MARIA["industry"] not in carrier.supported_industries:
            continue

        am = carrier.am_best_rating.upper()
        if not (am.startswith("A") or "A+" in am or "A-" in am):
            continue

        score = 0.0
        max_gl = max((gl.per_occurrence for gl in carrier.gl_limits), default=0)
        if max_gl >= 2_000_000:
            score += 30
        elif max_gl >= 1_000_000:
            score += 15

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

        if MARIA["industry"] in carrier.focus_industries:
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
            "max_gl":      f"${max_gl:,.0f}",
            "cg_2015":     getattr(carrier, "supports_cg_2015", True),
            "digital":     getattr(carrier, "allows_online_binding", False),
            "est_premium": _estimate_premium(cid, MARIA["revenue"]),
            "source":      "static_rules",
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:5]


def _estimate_premium(carrier_id: str, revenue: int) -> int:
    """Rough GL premium estimate for $2M limit based on revenue."""
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


def _static_carrier_results() -> list[dict]:
    """Fallback when carrier_capabilities not importable."""
    return [
        {"carrier_id": "next",     "name": "NEXT Insurance",  "score": 93.5,
         "am_best": "A (Excellent)", "quote_speed": "15 min",
         "max_gl": "$2,000,000", "cg_2015": True, "digital": True,  "est_premium": 1008},
        {"carrier_id": "hartford", "name": "The Hartford",    "score": 87.2,
         "am_best": "A (Excellent)", "quote_speed": "2 hr",
         "max_gl": "$2,000,000", "cg_2015": True, "digital": False, "est_premium": 1260},
        {"carrier_id": "travelers","name": "Travelers",       "score": 82.1,
         "am_best": "A+ (Superior)", "quote_speed": "2 hr",
         "max_gl": "$2,000,000", "cg_2015": True, "digital": False, "est_premium": 1440},
    ]


def _build_neo4j_graph_html(customer_id: str, state: str = "IN",
                            industry: str = "food_service") -> str | None:
    """
    Query Neo4j and build a pyvis interactive graph HTML string.
    Shows Carrier -> Industry (SPECIALIZES_IN) and Customer -> Industry (OPERATES_IN).
    Returns HTML string or None if unavailable.
    """
    if not (HAS_NEO4J and HAS_PYVIS):
        return None

    try:
        from neo4j import GraphDatabase
        from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

        net = Network(height="520px", width="100%", bgcolor="#0f1117",
                      font_color="white", directed=True)
        net.set_options("""
        {
          "nodes": {"font": {"size": 13}},
          "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}},
                    "smooth": {"type": "cubicBezier"}},
          "physics": {"barnesHut": {"gravitationalConstant": -8000,
                                    "springLength": 120}, "stabilization": false}
        }
        """)

        drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with drv.session(database=NEO4J_DATABASE) as sess:
            # Carriers + their industry specialization
            rows = sess.run("""
                MATCH (c:Carrier)-[r:SPECIALIZES_IN]->(i:Industry)
                RETURN c.id AS cid, c.name AS cname,
                       i.id AS iid, i.name AS iname,
                       r.score AS score
                ORDER BY r.score DESC
            """).data()

            seen_carriers  = set()
            seen_industries = set()

            for row in rows:
                cid, cname = row["cid"], row["cname"] or row["cid"]
                iid, iname = row["iid"], row["iname"] or row["iid"]
                score = round(row["score"] or 0, 2)

                if cid not in seen_carriers:
                    is_highlight = score >= 0.7 and iid == industry
                    net.add_node(f"c_{cid}", label=cname,
                                 color="#1565C0" if not is_highlight else "#f57c00",
                                 shape="dot", size=18,
                                 title=f"Carrier: {cname}")
                    seen_carriers.add(cid)

                if iid not in seen_industries:
                    is_target = iid == industry
                    net.add_node(f"i_{iid}", label=iname,
                                 color="#2e7d32" if is_target else "#555",
                                 shape="diamond", size=14,
                                 title=f"Industry: {iname}")
                    seen_industries.add(iid)

                if score >= 0.55:  # only show strong specializations
                    net.add_edge(f"c_{cid}", f"i_{iid}",
                                 label=f"{score:.2f}",
                                 color="#ffffff44", width=max(1, score * 4))

            # Customer node
            cu_rows = sess.run(
                "MATCH (cu:Customer {id: $cid})-[:OPERATES_IN]->(i:Industry) "
                "RETURN cu.name AS name, i.id AS iid",
                cid=customer_id,
            ).data()
            if cu_rows:
                cu_name = cu_rows[0]["name"] or customer_id
                net.add_node(f"cu_{customer_id}", label=cu_name,
                             color="#c62828", shape="star", size=22,
                             title=f"Customer: {cu_name}")
                for cu_row in cu_rows:
                    iid = cu_row["iid"]
                    if f"i_{iid}" in [n["id"] for n in net.nodes]:
                        net.add_edge(f"cu_{customer_id}", f"i_{iid}",
                                     label="operates in", color="#ff5252",
                                     dashes=True, width=2)

            # LICENSED_IN edges (state filter)
            state_rows = sess.run("""
                MATCH (c:Carrier)-[:LICENSED_IN]->(s:State {code: $state})
                RETURN c.id AS cid, s.code AS scode
            """, state=state).data()
            if state_rows:
                net.add_node(f"s_{state}", label=state,
                             color="#7b1fa2", shape="box", size=12,
                             title=f"State: {state}")
                for sr in state_rows:
                    if f"c_{sr['cid']}" in [n["id"] for n in net.nodes]:
                        net.add_edge(f"c_{sr['cid']}", f"s_{state}",
                                     color="#9c27b044", width=1)

        drv.close()

        # Return as HTML string
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
        net.save_graph(tmp.name)
        tmp.close()
        with open(tmp.name, "r", encoding="utf-8") as f:
            html = f.read()
        os.unlink(tmp.name)
        return html

    except Exception as e:
        logger.warning(f"Graph viz failed: {e}")
        return None


def _render_agent_result(ar) -> None:
    """Render an AgentResult as stage-by-stage cards in Streamlit."""
    # Stage 1
    s1_color = "#2e7d32" if ar.stage1_passed else "#c62828"
    s1_label = "PASSED" if ar.stage1_passed else "FAILED"
    st.markdown(
        f"<div style='border-left:4px solid {s1_color}; padding:0.6rem 1rem; margin-bottom:0.5rem; background:#fafafa; border-radius:4px'>"
        f"<b>Stage 1 — Embedding Similarity</b> &nbsp; "
        f"<span style='color:{s1_color}; font-weight:700'>{s1_label}</span> &nbsp; "
        f"Score: <b>{ar.embedding_similarity:.3f}</b> (threshold 0.38)"
        f"</div>",
        unsafe_allow_html=True,
    )

    if ar.stage1_passed:
        s2_color = "#2e7d32" if ar.stage2_passed else "#c62828"
        s2_label = "YES — insurance action required" if ar.is_insurance_requirement else "NO — not insurance related"
        st.markdown(
            f"<div style='border-left:4px solid {s2_color}; padding:0.6rem 1rem; margin-bottom:0.5rem; background:#fafafa; border-radius:4px'>"
            f"<b>Stage 2 — LLM Classification</b> &nbsp; "
            f"<span style='color:{s2_color}; font-weight:700'>{s2_label}</span><br>"
            f"Confidence: <b>{ar.classification_confidence:.0%}</b> &nbsp;·&nbsp; "
            f"{ar.classification_reasoning}"
            f"</div>",
            unsafe_allow_html=True,
        )

    if ar.stage2_passed and ar.extracted:
        ext = ar.extracted
        fields = []
        if ext.get("gl_limit"):
            fields.append(f"GL: <b>${ext['gl_limit']:,}</b>")
        if ext.get("gl_aggregate"):
            fields.append(f"Aggregate: <b>${ext['gl_aggregate']:,}</b>")
        if ext.get("additional_insured"):
            fields.append(f"Add'l Insured: <b>{ext['additional_insured']}</b>")
        if ext.get("endorsements"):
            fields.append(f"Endorsements: <b>{', '.join(ext['endorsements'])}</b>")
        if ext.get("am_best_min"):
            fields.append(f"AM Best: <b>{ext['am_best_min']}</b>")
        if ext.get("deadline"):
            fields.append(f"Deadline: <b>{ext['deadline']}</b>")
        if ext.get("portal"):
            fields.append(f"Portal: {ext['portal']}")

        st.markdown(
            f"<div style='border-left:4px solid #1565C0; padding:0.6rem 1rem; margin-bottom:0.5rem; background:#fafafa; border-radius:4px'>"
            f"<b>Stage 3 — Structured Extraction</b> &nbsp; "
            f"<span style='color:#1565C0; font-weight:700'>COMPLETE</span>"
            f"<br>{'&nbsp;&nbsp;·&nbsp;&nbsp;'.join(fields)}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Overall
    conf_pct = int(ar.overall_confidence * 100)
    conf_color = "#2e7d32" if conf_pct >= 70 else "#e65100" if conf_pct >= 40 else "#c62828"
    st.markdown(
        f"<div style='background:#e8f5e9; border-radius:6px; padding:0.5rem 1rem; display:inline-block'>"
        f"<b>Overall Confidence:</b> "
        f"<span style='color:{conf_color}; font-size:1.1rem; font-weight:700'>{conf_pct}%</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=60)
def _neo4j_score(customer_id: str) -> list[dict]:
    if not HAS_SCORING:
        return []
    try:
        return _scoring.score_customer(customer_id, top_n=5, explain=False)
    except Exception:
        return []


def _snow_status() -> dict:
    try:
        return snow.check_status()
    except Exception as e:
        return {"reachable": False, "message": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## BindIQ")
    st.markdown("**AI Insurance Intelligence**")
    st.markdown("---")

    # System status
    st.markdown("**System Status**")

    neo_icon  = "🟢" if HAS_NEO4J  else "🔴"
    gap_icon  = "🟢" if HAS_GAP    else "🔴"
    gmail_status = gmail.get_status()
    gmail_icon = "🟢" if gmail_status["connected"] else "🟡"
    claude_ok  = bool(os.environ.get("ANTHROPIC_API_KEY"))
    claude_icon = "🟢" if claude_ok else "🔴"

    st.markdown(f"{neo_icon} Neo4j Graph DB")
    st.markdown(f"{gap_icon} Gap Analyzer")
    st.markdown(f"{claude_icon} Claude API")
    st.markdown(f"{gmail_icon} Gmail Monitor")

    if not gmail_status["connected"]:
        st.caption(gmail_status["message"])

    st.markdown("---")
    st.markdown("**Demo Customer**")
    st.markdown("**Maria's Artisan Bakery**")
    st.caption("Industry: Food Service | State: IN")
    st.caption(f"Revenue: ${MARIA['revenue']:,.0f}")
    st.caption(f"Current GL: ${MARIA['current_gl_limit']:,.0f} (Simply Business)")
    st.markdown("---")
    st.caption("BindIQ v1.0 · FSO Hackathon 2026")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN TABS
# ═════════════════════════════════════════════════════════════════════════════

tab_demo, tab_intel, tab_snow, tab_email, tab_upload = st.tabs([
    "🎬  Live Demo",
    "📊  Market Intelligence",
    "🏢  ServiceNow CMDB",
    "📧  Email Monitor",
    "📁  Upload Contract",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — LIVE DEMO
# ─────────────────────────────────────────────────────────────────────────────

with tab_demo:
    st.title("Maria's Whole Foods Contract — Live Demo")
    st.caption("Friday, Feb 7, 2026  4:47 PM  ·  BindIQ detects a coverage gap before Maria even opens her email")

    # ── Session state init ────────────────────────────────────────────────────
    if "demo_step" not in st.session_state:
        st.session_state.demo_step = 0
    if "carriers" not in st.session_state:
        st.session_state.carriers = []
    if "alert_sent" not in st.session_state:
        st.session_state.alert_sent = False

    # ── Step progress bar ─────────────────────────────────────────────────────
    steps = [
        "Email Detected",
        "Requirements Extracted",
        "Gap Analysis",
        "Carrier Match",
        "Alert Sent",
    ]
    cols = st.columns(5)
    for i, (col, label) in enumerate(zip(cols, steps)):
        with col:
            done = st.session_state.demo_step > i
            active = st.session_state.demo_step == i + 1
            if done:
                st.markdown(f"<div class='step-done'>✓ {label}</div>", unsafe_allow_html=True)
            elif active:
                st.markdown(f"**► {label}**")
            else:
                st.markdown(f"<div class='step-pending'>○ {label}</div>", unsafe_allow_html=True)

    st.markdown("---")

    # ── Control buttons ───────────────────────────────────────────────────────
    btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 4])
    with btn_col1:
        run_all = st.button("🚀 Run Full Demo", type="primary",
                            disabled=st.session_state.demo_step == 5)
    with btn_col2:
        if st.button("↺ Reset"):
            st.session_state.demo_step = 0
            st.session_state.carriers  = []
            st.session_state.alert_sent = False
            st.rerun()

    if run_all:
        # Animate through all 5 steps
        progress = st.progress(0, text="Starting...")
        for step_num in range(1, 6):
            st.session_state.demo_step = step_num
            progress.progress(step_num / 5, text=f"Step {step_num}/5: {steps[step_num-1]}...")
            if step_num == 4:
                st.session_state.carriers = score_carriers_for_maria()
            if step_num == 5:
                alert_analysis = {
                    "customer_name":     "Maria",
                    "customer_id":       MARIA["customer_id"],
                    "current_carrier":   MARIA["current_carrier"],
                    "current_carrier_id":"simply_business",
                    "current_limit":     f"${MARIA['current_gl_limit']:,}",
                    "required_limit":    "$2,000,000",
                    "deadline":          WF_REQUIREMENTS["deadline"],
                    "days_left":         WF_REQUIREMENTS["deadline_days"],
                    "retailer":          "Whole Foods",
                    "top_carriers":      st.session_state.carriers,
                }
                alert_ok = gmail.send_bindiq_alert(MARIA["email"], alert_analysis)
                st.session_state.alert_sent  = alert_ok
                st.session_state.alert_analysis = alert_analysis
            time.sleep(0.6)
        progress.empty()
        st.rerun()

    st.markdown("")

    # ─── STEP 1: Email ────────────────────────────────────────────────────────
    if st.session_state.demo_step >= 1:
        with st.expander("Step 1 — Email Detected  ✓", expanded=(st.session_state.demo_step == 1)):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown("**Incoming email at 4:47 PM — BindIQ background monitor triggered**")
                # Highlight key terms in the email
                highlighted = WF_EMAIL_TEXT
                for term in ["$2,000,000", "CG 20 15", "March 14", "Whole Foods Market Inc."]:
                    highlighted = highlighted.replace(term, f"**{term}**")
                st.code(WF_EMAIL_TEXT, language=None)
            with c2:
                st.markdown("**Trigger signals detected:**")
                st.markdown("- From: @wholefoods.com")
                st.markdown("- Subject: 'Vendor Contract'")
                st.markdown("- Keywords: $2,000,000 · CG 2015")
                st.markdown("- Attachment: PDF contract")
                st.metric("Confidence", "98%")
                st.metric("Time to detect", "47 sec")

    # ─── STEP 2: Requirements Extracted ─────────────────────────────────────
    if st.session_state.demo_step >= 2:
        with st.expander("Step 2 — Requirements Extracted  ✓", expanded=(st.session_state.demo_step == 2)):
            reqs = extractor.extract(WF_EMAIL_TEXT)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Claude extracted these requirements:**")
                st.markdown(f"| Field | Extracted Value |")
                st.markdown(f"|-------|----------------|")
                st.markdown(f"| GL Limit | **${reqs['gl_limit']:,}** per occurrence |")
                st.markdown(f"| Additional Insured | {reqs['additional_insured']} |")
                st.markdown(f"| Endorsements | {', '.join(reqs['endorsements'])} |")
                st.markdown(f"| AM Best Minimum | {reqs['am_best_min']} |")
                st.markdown(f"| Cancel Notice | {reqs.get('cancellation_notice', 30)} days |")
                st.markdown(f"| Deadline | **{reqs.get('deadline', 'Feb 17, 2026')}** |")
            with c2:
                st.metric("Days Until Deadline", reqs.get("deadline_days", 8), delta="-8 days", delta_color="inverse")
                st.metric("Extraction Confidence", f"{reqs.get('confidence', 98)}%")
                st.caption(f"Extracted by: Claude Haiku + regex fallback")
                if reqs.get("portal"):
                    st.caption(f"COI Portal: {reqs['portal']}")

    # ─── STEP 3: Gap Analysis ─────────────────────────────────────────────────
    if st.session_state.demo_step >= 3:
        with st.expander("Step 3 — Coverage Gap Analysis  ✓", expanded=(st.session_state.demo_step == 3)):
            st.markdown("**Maria's current coverage vs. Whole Foods requirements:**")

            gap_data = [
                ("GL Limit",          f"${MARIA['current_gl_limit']:,}",  "$2,000,000",  "CRITICAL — $1M short"),
                ("CG 2015 Endorsement","None",                             "Required",    "MISSING"),
                ("Additional Insured", "None",                             "Whole Foods", "MISSING"),
                ("AM Best Rating",     "A (Simply Business)",              "A- minimum",  "OK"),
                ("Carrier",            "Simply Business",                  "Any A- rated","OK — but may need to switch"),
            ]

            for label, current, required, status in gap_data:
                c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
                is_gap = status not in ("OK", "OK — but may need to switch")
                c1.markdown(f"**{label}**")
                c2.markdown(current)
                c3.markdown(f"_{required}_")
                if is_gap:
                    c4.markdown(f"<span class='gap-row-bad'>✗ {status}</span>", unsafe_allow_html=True)
                else:
                    c4.markdown(f"<span class='gap-row-ok'>✓ {status}</span>", unsafe_allow_html=True)

            st.markdown("---")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Total Gaps",    "3",      delta="action required", delta_color="inverse")
            col_b.metric("Critical",      "1",      delta="GL limit")
            col_c.metric("Days to Fix",   "8",      delta_color="inverse")

    # ─── STEP 4: Carrier Recommendations ─────────────────────────────────────
    if st.session_state.demo_step >= 4:
        with st.expander("Step 4 — Top Carrier Recommendations  ✓", expanded=(st.session_state.demo_step == 4)):
            if not st.session_state.carriers:
                st.session_state.carriers = score_carriers_for_maria()

            carriers = st.session_state.carriers
            st.markdown(f"**{len(carriers)} carriers scored** for Maria's profile (IN · food_service · $3.6M revenue)")
            st.markdown("")

            source_label = "Neo4j hybrid scoring" if carriers and carriers[0].get("source") == "neo4j_hybrid" else "static rules"
            st.caption(f"Scored using: {source_label} (semantic + graph + rules)")

            for i, c in enumerate(carriers[:3], 1):
                medal = ["1.", "2.", "3."][i - 1]
                breakdown = ""
                if c.get("semantic_score") is not None:
                    breakdown = (
                        f"&nbsp;&nbsp;<small style='color:#777'>"
                        f"sem={c['semantic_score']:.0f} "
                        f"graph={c['graph_score']:.0f} "
                        f"rules={c['rules_score']:.0f}"
                        f"</small>"
                    )
                with st.container():
                    st.markdown(
                        f"""<div class='carrier-card'>
                        <b>{medal} {c['name']}</b>
                        &nbsp;&nbsp;
                        <span class='score-chip'>{c['score']:.0f}/100</span>
                        {breakdown}
                        &nbsp;&nbsp;
                        <b>AM Best:</b> {c['am_best']}
                        &nbsp;&nbsp;
                        <b>Quote:</b> {c['quote_speed']}
                        &nbsp;&nbsp;
                        <b>Max GL:</b> {c['max_gl']}
                        &nbsp;&nbsp;
                        <b>Est. Premium:</b> ${c['est_premium']:,}/yr
                        &nbsp;&nbsp;
                        {'&#x2705; CG 2015' if c['cg_2015'] else '&#x274C; No CG 2015'}
                        &nbsp;
                        {'&#x2705; Digital Bind' if c['digital'] else ''}
                        </div>""",
                        unsafe_allow_html=True,
                    )
                if c.get("explanation"):
                    st.caption(f"   {c['explanation']}")

            if len(carriers) > 3:
                with st.expander(f"Show {len(carriers) - 3} more carriers"):
                    for c in carriers[3:]:
                        st.markdown(
                            f"- **{c['name']}** — {c['score']:.0f}/100 · {c['am_best']} · Quote: {c['quote_speed']} · ${c['est_premium']:,}/yr"
                        )

    # ─── STEP 5: Alert Sent ───────────────────────────────────────────────────
    if st.session_state.demo_step >= 5:
        with st.expander("Step 5 — Alert Sent to Maria  ✓", expanded=True):
            c1, c2 = st.columns([2, 1])

            # Build analysis dict for the preview (same as what was sent)
            alert_analysis = st.session_state.get("alert_analysis") or {
                "customer_name":     "Maria",
                "customer_id":       MARIA["customer_id"],
                "current_carrier":   MARIA["current_carrier"],
                "current_carrier_id":"simply_business",
                "current_limit":     f"${MARIA['current_gl_limit']:,}",
                "required_limit":    "$2,000,000",
                "deadline":          WF_REQUIREMENTS["deadline"],
                "days_left":         WF_REQUIREMENTS["deadline_days"],
                "retailer":          "Whole Foods",
                "top_carriers":      st.session_state.carriers,
            }

            with c1:
                st.markdown(
                    f"**FROM:** noreply@bindiq.com &nbsp;·&nbsp; "
                    f"**TO:** {MARIA['email']} &nbsp;·&nbsp; "
                    f"**Time:** {datetime.now().strftime('%I:%M %p')}"
                )
                alert_html, _ = gmail.build_alert_html(alert_analysis)
                try:
                    import streamlit.components.v1 as stc
                    stc.html(alert_html, height=620, scrolling=True)
                except Exception:
                    st.markdown(alert_html, unsafe_allow_html=True)

            with c2:
                mode = "LIVE (Gmail)" if gmail.is_configured() else "SIMULATED"
                sent = "Sent" if st.session_state.alert_sent else "Queued"
                st.metric("Email Status", sent)
                st.metric("Mode", mode)
                st.metric("Time to Alert", "2 min 14 sec")
                st.caption(f"Sent to: {MARIA['email']}")

                if not gmail.is_configured():
                    st.info(
                        "Add GMAIL_APP_PASSWORD to .env for live email sending.\n"
                        "See email_watcher.py for setup instructions."
                    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — MARKET INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

with tab_intel:
    st.title("Market Intelligence — Carrier Scoring")
    st.caption("Score carriers for any demo customer using the hybrid engine (semantic + graph + rules)")

    if not HAS_NEO4J:
        st.warning("Neo4j not connected — showing static gap-analysis scores only.")

    customer_names = {c["id"]: c["name"] for c in DEMO_CUSTOMERS}
    selected_id = st.selectbox(
        "Select customer:",
        options=[c["id"] for c in DEMO_CUSTOMERS],
        format_func=lambda x: f"{customer_names[x]} ({x})",
    )
    selected = next(c for c in DEMO_CUSTOMERS if c["id"] == selected_id)

    col_a, col_b = st.columns([1, 3])
    with col_a:
        st.markdown(f"**{selected['name']}**")
        st.caption(f"Industry: {selected['industry']}")
        st.caption(f"State: {selected['state']}")
        run_score = st.button("Score Carriers", type="primary")

    with col_b:
        if run_score:
            with st.spinner("Running hybrid scoring engine..."):
                if HAS_SCORING:
                    rankings = _neo4j_score(selected_id)
                else:
                    rankings = []

            if rankings:
                st.markdown("**Hybrid score (semantic 30% + graph 40% + rules 30%):**")
                for i, r in enumerate(rankings, 1):
                    st.markdown(
                        f"{i}. **{r['carrier_name']}** — "
                        f"`{r['total_score']:.1f}/100` "
                        f"(sem={r['semantic_score']:.0f} "
                        f"graph={r['graph_score']:.0f} "
                        f"rules={r['rules_score']:.0f})"
                    )
                    if r.get("explanation"):
                        st.caption(f"   {r['explanation'][:120]}")
            elif selected_id == "maria_bakery_tx":
                # Fallback: show gap-analysis scores for Maria
                st.markdown("**Gap-analysis carrier scores (static):**")
                for i, c in enumerate(score_carriers_for_maria(), 1):
                    st.markdown(
                        f"{i}. **{c['name']}** — `{c['score']:.0f}/100` "
                        f"· AM Best {c['am_best']} · Quote {c['quote_speed']} · ${c['est_premium']:,}/yr"
                    )
            else:
                st.info("Connect Neo4j and run `python run_all.py` to see hybrid scores for this customer.")

    st.markdown("---")
    st.subheader("Carrier Capability Reference")
    if HAS_GAP:
        carrier_rows = []
        for cid, c in CARRIERS.items():
            max_gl = max((gl.per_occurrence for gl in c.gl_limits), default=0)
            carrier_rows.append({
                "Carrier":       c.name,
                "AM Best":       c.am_best_rating,
                "Max GL":        f"${max_gl:,.0f}",
                "Quote Speed":   f"{c.auto_quote_hours:.1f}h" if c.auto_quote_hours >= 1 else f"{int(c.auto_quote_hours*60)}min",
                "Digital Bind":  "Yes" if c.allows_online_binding else "No",
                "CG 2015":       "Yes" if c.supports_cg_2015 else "No",
                "States":        len(c.licensed_states),
                "Focus":         ", ".join(list(c.focus_industries)[:2]),
            })
        import pandas as pd
        st.dataframe(pd.DataFrame(carrier_rows), width="stretch", hide_index=True)

    st.markdown("---")
    st.subheader("Knowledge Graph — Neo4j Visualization")

    if not HAS_NEO4J:
        st.warning("Neo4j not connected. Start Neo4j and run `python KnowledgeGraph/run_all.py` to populate the graph.")
    elif not HAS_PYVIS:
        st.warning("Install pyvis: `pip install pyvis` — then restart the app.")
    else:
        sel_cust = next((c for c in DEMO_CUSTOMERS if c["id"] == selected_id), DEMO_CUSTOMERS[0])
        show_graph = st.button("Show Knowledge Graph", key="show_graph_btn")
        if show_graph or st.session_state.get("graph_shown"):
            st.session_state["graph_shown"] = True
            st.caption(
                f"Carriers (blue=specialist, orange=top match) → Industries (green=target: {sel_cust['industry']}) "
                f"| Customer (red star) | Licensed in {sel_cust['state']} (purple)"
            )
            with st.spinner("Building graph from Neo4j..."):
                graph_html = _build_neo4j_graph_html(
                    sel_cust["id"], sel_cust["state"], sel_cust["industry"]
                )
            if graph_html:
                stc.html(graph_html, height=540, scrolling=False)
            else:
                st.error("Graph query failed — check Neo4j connection and that run_all.py has been run.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — ServiceNow CMDB
# ─────────────────────────────────────────────────────────────────────────────

with tab_snow:
    st.title("ServiceNow CMDB Sync")

    c1, c2 = st.columns([1, 2])

    # ── Architecture diagram ──────────────────────────────────────────────────
    st.markdown("""
**Architecture:** CMDB stores *who* (carrier records, customer profiles, policy requests).
Neo4j stores *why* (relationships, specialization scores, semantic embeddings).
Flow Designer automates *what happens next* (quote workflows, email alerts).
""")

    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown("**Instance**")
        snow_instance = os.environ.get("SNOW_INSTANCE", "https://dev252187.service-now.com")
        st.code(snow_instance)
        st.markdown(f"**User:** {os.environ.get('SNOW_USER', 'admin')}")
        st.markdown(f"**Password:** {'*' * 8 if os.environ.get('SNOW_PASSWORD') else 'not set'}")
        check_btn    = st.button("Check Connection", type="primary")
        setup_btn    = st.button("Create Tables")
        sync_btn     = st.button("Sync Carriers + Customers")
        flow_btn     = st.button("Request Quote via Flow Designer",
                                 help="Creates a u_bindiq_policies record — triggers the Flow Designer flow")
        webhook_url  = st.text_input("Webhook URL (for email monitor)",
                                     value=os.environ.get("BINDIQ_BASE_URL", "http://localhost:5000"),
                                     help="URL of the BindIQ API that ServiceNow will call every 5 min")
        monitor_btn  = st.button("Setup Email Monitor (SN Scheduled Script)",
                                 help="Creates a sysauto_script that calls your webhook every 5 minutes")

    with c2:
        # ── Quote / Flow Designer trigger ─────────────────────────────────────
        if flow_btn:
            with st.spinner("Creating quote request in ServiceNow..."):
                result = snow.trigger_quote_flow(
                    customer_id=MARIA["customer_id"],
                    carrier_id="next",
                    gl_limit=2_000_000,
                    notes="Whole Foods vendor contract — 8 days to comply",
                )
            if result["success"]:
                st.success(f"Quote request created! sys_id: `{result['sys_id']}`")
                st.markdown(
                    "The ServiceNow Flow Designer flow will now:\n"
                    "1. Send an email confirmation to Maria\n"
                    "2. Set status → `in_progress`\n"
                    "3. Create a broker task\n\n"
                    f"View in ServiceNow: `{snow_instance}/now/nav/ui/classic/params/target/"
                    f"u_bindiq_policies_list.do`"
                )
            else:
                st.warning(f"Could not create record: {result['message']}")
                st.markdown("**Set up the u_bindiq_policies table first, then try again.**")
                with st.expander("Flow Designer setup instructions"):
                    st.code(snow.FLOW_DESIGNER_INSTRUCTIONS.format(instance=snow_instance), language=None)

        if monitor_btn:
            with st.spinner("Creating ServiceNow scheduled email monitor..."):
                mon_result = snow.create_scheduled_monitor(webhook_url=webhook_url)
            if mon_result["success"]:
                sched = mon_result["results"].get("scheduler", {})
                st.success(f"Email monitor scheduled script created! (sys_id: `{sched.get('sys_id', '')}`)")
                st.markdown(
                    f"ServiceNow will call **{webhook_url}/api/check-inbox** every **5 minutes**.\n\n"
                    f"View script: [{snow_instance}/sysauto_script.do]"
                    f"({snow_instance}/sysauto_script.do?sys_id={sched.get('sys_id', '')})"
                )
            else:
                st.warning(f"Could not create scheduler: {mon_result['message']}")
                with st.expander("Paste this into ServiceNow Background Script manually"):
                    st.code(snow._MONITOR_SCRIPT, language="javascript")

        if check_btn or setup_btn or sync_btn:
            with st.spinner("Connecting to ServiceNow..."):
                status = _snow_status()

            if status.get("reachable"):
                st.success(f"Connected: {snow_instance}")
                tables = status.get("tables", {})
                for tname, exists in tables.items():
                    icon = "✅" if exists else "❌"
                    st.markdown(f"{icon} `{tname}`")

                if setup_btn:
                    missing = [t for t, ok in status.get("tables", {}).items() if not ok]
                    if not missing:
                        st.success("All tables already exist — ready to sync!")
                    else:
                        st.warning(
                            f"PDI blocks automated table creation. "
                            f"Create {len(missing)} missing table(s) manually:"
                        )
                        snow_url = status.get("instance", snow_instance)
                        st.markdown(
                            f"**1. Open Tables list →** "
                            f"[sys_db_object_list.do]({snow_url}/now/nav/ui/classic/params/target/sys_db_object_list.do)"
                        )
                        for t in missing:
                            label = t.replace("u_bindiq_", "BindIQ ").title()
                            st.markdown(
                                f"**2. Create** `{t}` — Label: _{label}_ — Extends: _Configuration Item [cmdb\\_ci]_"
                            )
                        with st.expander("Or run this script in ServiceNow Background Script"):
                            bg_url = f"{snow_url}/sys.scripts.do"
                            st.caption(f"Go to: {bg_url}")
                            st.code(status.get("background_script", snow.BACKGROUND_SCRIPT), language="javascript")
                        st.caption("After creating tables, click 'Check Connection' then 'Sync'.")

                if sync_btn and status.get("all_ready"):
                    with st.spinner("Syncing to ServiceNow CMDB..."):
                        sys.path.insert(0, str(KG_DIR))
                        try:
                            import cmdb_loader
                            from seed_customers import DEMO_CUSTOMERS as seed_data
                            result = cmdb_loader.run(customers=seed_data)
                            st.success(f"CMDB sync complete: {result.get('status')}")
                            r = result.get("results", {})
                            if r.get("carriers"):
                                car = r["carriers"]
                                st.markdown(
                                    f"Carriers: {car['created']} created, "
                                    f"{car['updated']} updated, {car['failed']} failed"
                                )
                            if r.get("customers"):
                                cus = r["customers"]
                                st.markdown(
                                    f"Customers: {cus['created']} created, "
                                    f"{cus['updated']} updated, {cus['failed']} failed"
                                )
                        except Exception as e:
                            st.error(f"Sync failed: {e}")
                elif sync_btn:
                    st.warning("Run 'Create Tables' first to set up the custom CMDB tables.")

            else:
                st.error(f"Instance unreachable: {status.get('message')}")
                st.markdown(
                    "**Developer instances hibernate after ~10 days of inactivity.**\n\n"
                    "To wake it up:\n"
                    "1. Go to [developer.servicenow.com](https://developer.servicenow.com)\n"
                    "2. Log in and click **Start Building**\n"
                    "3. Wait 3-5 minutes, then retry."
                )

        if not any([check_btn, setup_btn, sync_btn, flow_btn]):
            st.info("Click **Check Connection** to test ServiceNow connectivity.")
            st.markdown("**Required CMDB Tables:**")
            for t, lbl in [
                ("u_bindiq_carriers",  "12 carriers — identity, AM Best, binding speed"),
                ("u_bindiq_customers", "Demo customers — business profiles"),
                ("u_bindiq_policies",  "Quote requests — triggers Flow Designer flows"),
            ]:
                st.markdown(f"- `{t}` — {lbl}")

    st.markdown("---")
    st.subheader("Flow Designer Integration")
    fd_col1, fd_col2 = st.columns(2)
    with fd_col1:
        st.markdown("""
**How it works:**
1. BindIQ runs carrier scoring → picks best match
2. User clicks **Request Quote via Flow Designer**
3. BindIQ creates `u_bindiq_policies` record in CMDB with `status=pending`
4. Flow Designer flow fires automatically:
   - Emails Maria with quote details
   - Updates record to `in_progress`
   - Creates broker task in ServiceNow
5. Broker fulfills quote, updates record to `complete`
""")
    with fd_col2:
        with st.expander("View Flow Designer setup instructions"):
            st.code(snow.FLOW_DESIGNER_INSTRUCTIONS.format(instance=os.environ.get("SNOW_INSTANCE", "https://dev252187.service-now.com")), language=None)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — EMAIL MONITOR
# ─────────────────────────────────────────────────────────────────────────────

with tab_email:
    st.title("Email Monitor — 3-Stage Intelligence Agent")

    gmail_status = gmail.get_status()

    # ── Agent status banner ───────────────────────────────────────────────────
    agent_col1, agent_col2, agent_col3 = st.columns(3)
    with agent_col1:
        agent_icon = "🟢" if HAS_EMAIL_AGENT else "🔴"
        st.markdown(f"{agent_icon} **Email Agent** — {'3-stage pipeline active' if HAS_EMAIL_AGENT else 'unavailable (keyword fallback)'}")
    with agent_col2:
        stage1_icon = "🟢" if HAS_EMAIL_AGENT else "🔴"
        st.markdown(f"{stage1_icon} Stage 1: Embedding similarity (sentence-transformers)")
    with agent_col3:
        stage2_icon = "🟢" if bool(os.environ.get("ANTHROPIC_API_KEY")) else "🟡"
        st.markdown(f"{stage2_icon} Stage 2: LLM classification ({'Claude Haiku' if os.environ.get('ANTHROPIC_API_KEY') else 'heuristic fallback'})")

    st.markdown("---")

    c1, c2 = st.columns([1, 2])
    with c1:
        st.markdown("**Monitor Account**")
        st.code("warantheyanesh@gmail.com")
        if gmail_status["connected"]:
            st.success("Connected (live mode)")
        elif gmail_status["mode"] == "simulation":
            st.warning("Simulation mode")
            st.caption("Add GMAIL_APP_PASSWORD to .env for live email")
        else:
            st.error(gmail_status["message"])

        send_wf     = st.button("Send Whole Foods Test Email", type="primary")
        check_inbox = st.button("Check Inbox for Triggers")
        run_agent   = st.button("Run Agent on Demo Email", type="secondary",
                                disabled=not HAS_EMAIL_AGENT)

    with c2:
        # ── Send test email ───────────────────────────────────────────────────
        if send_wf:
            with st.spinner("Sending Whole Foods contract email..."):
                ok = gmail.send_whole_foods_trigger(to="warantheyanesh@gmail.com")
            if ok:
                st.success(
                    "Email sent to warantheyanesh@gmail.com — "
                    "go to 'Live Demo' tab to process it."
                )
                with st.expander("Email preview"):
                    st.code(WF_EMAIL_TEXT, language=None)
            else:
                st.error("Send failed — check GMAIL_APP_PASSWORD in .env")

        # ── Check inbox (uses email_agent internally) ─────────────────────────
        if check_inbox:
            with st.spinner("Checking Gmail inbox with 3-stage agent..."):
                triggers = gmail.check_inbox_for_trigger()
            if triggers:
                st.success(f"Found {len(triggers)} trigger email(s)!")
                for t in triggers:
                    with st.expander(f"**{t['subject']}** — confidence: {t['confidence']}%"):
                        ar = t.get("agent_result")
                        if ar:
                            _render_agent_result(ar)
                        else:
                            st.markdown(f"Keyword hits: {t.get('keyword_hits', '—')}")
                            st.markdown(f"From: {t['from']}")
                            st.code(t["body"], language=None)
            elif gmail_status["mode"] == "simulation":
                st.info("Simulation mode — no real inbox to check.")
            else:
                st.info("No new trigger emails found.")

        # ── Run agent on the demo WF email ────────────────────────────────────
        if run_agent and HAS_EMAIL_AGENT:
            subject_demo = "Congratulations! Whole Foods Vendor Contract — Action Required"
            with st.spinner("Running 3-stage intelligence pipeline..."):
                ar = email_agent.run(subject_demo, WF_EMAIL_TEXT)

            st.markdown("### Agent Result — Whole Foods Demo Email")
            _render_agent_result(ar)

        if not send_wf and not check_inbox and not run_agent:
            st.markdown("**How the 3-stage agent works:**")
            st.markdown("""
| Stage | Method | Cost | What it does |
|-------|--------|------|-------------|
| **1 — Embedding filter** | sentence-transformers | Free | Cosine similarity vs 15 positive / 10 negative examples |
| **2 — LLM classification** | Claude Haiku | ~$0.001 | Confirms it's an insurance requirement + reasoning |
| **3 — Structured extraction** | Claude Haiku | ~$0.002 | Extracts GL limit, endorsements, deadline, portal URL |

Only emails that pass Stage 1 reach Stage 2 (saves API calls).
Only emails that pass Stage 2 reach Stage 3.
""")

        st.markdown("---")
        st.markdown("**Gmail App Password setup:**")
        with st.expander("How to enable live email monitoring"):
            st.markdown("""
1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification**
3. Search for **App Passwords** → Create → Mail → Other
4. Copy the 16-character password
5. Add to `KnowledgeGraph/.env`:
   ```
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```
6. Also enable IMAP in Gmail Settings → See All Settings → Forwarding and POP/IMAP
""")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — UPLOAD CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

with tab_upload:
    st.title("Upload & Analyze a Contract")
    st.caption("Paste or upload any vendor contract — BindIQ will extract insurance requirements and recommend carriers")

    # ── Banner if user arrived from an email CTA link ─────────────────────────
    came_from_email = st.query_params.get("view") in ("review", "upload")
    if came_from_email:
        st.success(
            "You were directed here from your BindIQ alert email. "
            "Paste or upload your full contract below for a complete analysis, "
            "or review the pre-loaded demo email."
        )

    # ── Input area ────────────────────────────────────────────────────────────
    col_input, col_help = st.columns([3, 1])

    with col_help:
        st.markdown("**What this does:**")
        st.markdown(
            "1. Embedding filter (Stage 1)\n"
            "2. LLM classification (Stage 2)\n"
            "3. Structured extraction (Stage 3)\n"
            "4. Carrier scoring via KG"
        )
        st.markdown("**Accepts:**")
        st.markdown("- Vendor contracts\n- Retailer emails\n- Any doc with COI requirements")
        st.caption("PDF requires pdfplumber (`pip install pdfplumber`)")

    with col_input:
        uploaded_file = st.file_uploader(
            "Upload contract (TXT or PDF):", type=["txt", "pdf"], key="upload_file"
        )

        prefill = WF_EMAIL_TEXT if came_from_email else ""

        if uploaded_file is not None:
            if uploaded_file.name.lower().endswith(".pdf"):
                try:
                    import pdfplumber, io as _io
                    with pdfplumber.open(_io.BytesIO(uploaded_file.read())) as _pdf:
                        prefill = "\n".join(p.extract_text() or "" for p in _pdf.pages)
                    st.success(f"PDF loaded — {len(prefill):,} chars extracted")
                except ImportError:
                    st.warning("Install pdfplumber to read PDFs: `pip install pdfplumber`. Paste text below.")
                except Exception as _e:
                    st.warning(f"Could not read PDF ({_e}). Paste text below instead.")
            else:
                prefill = uploaded_file.read().decode("utf-8", errors="replace")
                st.success(f"File loaded — {len(prefill):,} chars")

        contract_text = st.text_area(
            "Or paste contract / email text:",
            value=prefill,
            height=240,
            placeholder="Paste vendor contract, email, or any document with insurance requirements...",
            key="upload_text",
        )

    # ── Analyze button ────────────────────────────────────────────────────────
    analyze_btn = st.button("Analyze Contract", type="primary", key="upload_analyze")

    if analyze_btn:
        if not contract_text.strip():
            st.warning("Please upload or paste a contract first.")
        else:
            is_insurance = False
            reqs = {}

            # ── Stage 1-2-3 pipeline ──────────────────────────────────────────
            if HAS_EMAIL_AGENT:
                with st.spinner("Running 3-stage intelligence pipeline..."):
                    ar = email_agent.run("Contract Upload", contract_text)

                st.markdown("### Pipeline Results")
                _render_agent_result(ar)
                is_insurance = ar.stage2_passed
            else:
                st.warning("Email agent unavailable — using keyword detection.")
                kw_hits = sum(
                    1 for kw in ["certificate of insurance", "additional insured",
                                 "general liability", "coi", "$2,000,000"]
                    if kw in contract_text.lower()
                )
                is_insurance = kw_hits >= 2
                if is_insurance:
                    st.success(f"Insurance requirement detected ({kw_hits} keyword signals found)")
                else:
                    st.info("No strong insurance requirement signals detected in this document.")

            # ── Extraction + scoring (only if insurance-related) ──────────────
            if is_insurance:
                with st.spinner("Extracting structured requirements..."):
                    reqs = extractor.extract(contract_text, use_static_demo=False)

                if any(reqs.get(k) for k in ("gl_limit", "deadline", "additional_insured", "endorsements")):
                    st.markdown("### Extracted Requirements")
                    m1, m2, m3, m4 = st.columns(4)
                    if reqs.get("gl_limit"):
                        m1.metric("GL Limit", f"${reqs['gl_limit']:,}")
                    if reqs.get("gl_aggregate"):
                        m2.metric("Aggregate", f"${reqs['gl_aggregate']:,}")
                    if reqs.get("deadline"):
                        m3.metric("Deadline", str(reqs["deadline"]))
                    if reqs.get("deadline_days"):
                        m4.metric("Days Left", str(reqs["deadline_days"]))

                    if reqs.get("additional_insured"):
                        st.markdown(f"**Additional Insured:** {reqs['additional_insured']}")
                    if reqs.get("endorsements"):
                        st.markdown(f"**Required endorsements:** {', '.join(reqs['endorsements'])}")
                    if reqs.get("am_best_min"):
                        st.markdown(f"**AM Best minimum:** {reqs['am_best_min']}")
                    if reqs.get("portal"):
                        st.markdown(f"**COI portal:** {reqs['portal']}")

                # ── Carrier recommendations ───────────────────────────────────
                st.markdown("### Recommended Carriers")
                st.caption("Scored using Knowledge Graph hybrid engine (semantic + graph + rules)")

                with st.spinner("Scoring carriers..."):
                    upload_carriers = score_carriers_for_maria()

                for i, c in enumerate(upload_carriers[:3], 1):
                    medal = f"{i}."
                    st.markdown(
                        f"""<div class='carrier-card'>
                        <b>{medal} {c['name']}</b>
                        &nbsp;&nbsp;<span class='score-chip'>{c['score']:.0f}/100</span>
                        &nbsp;&nbsp;<b>AM Best:</b> {c['am_best']}
                        &nbsp;&nbsp;<b>Quote speed:</b> {c['quote_speed']}
                        &nbsp;&nbsp;<b>Est. premium:</b> ${c['est_premium']:,}/yr
                        &nbsp;&nbsp;{'&#x2705; CG 2015' if c['cg_2015'] else '&#x274C; No CG 2015'}
                        {'&nbsp;&nbsp;&#x2705; Digital bind' if c['digital'] else ''}
                        </div>""",
                        unsafe_allow_html=True,
                    )
                    if c.get("explanation"):
                        st.caption(f"   {c['explanation']}")

                if len(upload_carriers) > 3:
                    with st.expander(f"Show {len(upload_carriers) - 3} more carriers"):
                        for c in upload_carriers[3:]:
                            st.markdown(
                                f"- **{c['name']}** — {c['score']:.0f}/100 "
                                f"· {c['am_best']} · Quote: {c['quote_speed']} · ${c['est_premium']:,}/yr"
                            )

                # ── Send alert email ──────────────────────────────────────────
                st.markdown("---")
                send_col, _ = st.columns([1, 3])
                with send_col:
                    if st.button("Send Alert Email to Customer", type="primary", key="upload_send_alert"):
                        alert_payload = {
                            "customer_name":      "Maria",
                            "customer_id":        MARIA["customer_id"],
                            "current_carrier":    MARIA["current_carrier"],
                            "current_carrier_id": "simply_business",
                            "current_limit":      f"${MARIA['current_gl_limit']:,}",
                            "required_limit":     f"${reqs.get('gl_limit', 2_000_000):,}",
                            "deadline":           str(reqs.get("deadline", "TBD")),
                            "days_left":          int(reqs.get("deadline_days") or 0),
                            "retailer":           reqs.get("retailer", "Vendor"),
                            "top_carriers": [
                                {
                                    "name":        c["name"],
                                    "score":       c["score"],
                                    "quote_speed": c["quote_speed"],
                                }
                                for c in upload_carriers
                            ],
                        }
                        with st.spinner("Sending alert email..."):
                            ok = gmail.send_bindiq_alert(MARIA["email"], alert_payload)
                        if ok:
                            st.success(f"Alert sent to {MARIA['email']}")
                        else:
                            st.error("Send failed — check GMAIL_APP_PASSWORD in .env")
