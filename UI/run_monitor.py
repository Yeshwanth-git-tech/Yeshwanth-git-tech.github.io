"""
BindIQ Email Monitor — Standalone Polling Service

Polls warantheyanesh@gmail.com every 5 minutes for vendor contract emails.
When triggered:
  1. 3-stage email agent (embedding -> Claude Haiku classify -> Claude Haiku extract)
  2. Carrier scoring via Neo4j hybrid engine (semantic + graph + rules)
  3. Gap analysis against current policy
  4. Sends HTML alert email to the customer

Also exposes a Flask webhook at /api/check-inbox so ServiceNow Flow Designer
can trigger the pipeline on-demand (no need to wait for the 5-min poll).

Usage:
  cd UI
  python run_monitor.py                # starts poller + webhook server
  python run_monitor.py --once         # single check and exit
  python run_monitor.py --webhook-only # webhook server only (no polling)
  python run_monitor.py --poll-only    # polling only (no Flask server)
"""

import argparse
import logging
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
KG_DIR   = BASE_DIR.parent / "KnowledgeGraph"
sys.path.insert(0, str(KG_DIR))
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(KG_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("bindiq_monitor")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))  # 2 min default
ALERT_TO      = os.environ.get("ALERT_TO", "warantheyanesh@gmail.com")

# ── Lazy imports (avoid startup crash if optional deps missing) ────────────────

def _load_modules():
    """Load all pipeline modules; return dict of what's available."""
    mods = {}
    try:
        import email_watcher
        mods["gmail"]    = email_watcher
        mods["can_send"] = email_watcher.is_configured()
    except Exception as e:
        logger.warning(f"email_watcher unavailable: {e}")

    try:
        import email_agent
        mods["agent"] = email_agent
    except Exception as e:
        logger.warning(f"email_agent unavailable: {e}")

    try:
        import requirement_extractor
        mods["extractor"] = requirement_extractor
    except Exception as e:
        logger.warning(f"requirement_extractor unavailable: {e}")

    try:
        import scoring as _scoring
        from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
        from neo4j import GraphDatabase
        drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        drv.verify_connectivity()
        drv.close()
        mods["scoring"] = _scoring
        mods["has_neo4j"] = True
    except Exception as e:
        logger.warning(f"Neo4j/scoring unavailable: {e}")
        mods["has_neo4j"] = False

    return mods


# ── CARRIER SCORING ───────────────────────────────────────────────────────────

_BASE_RATES = {
    "next": 0.00028, "hartford": 0.00035, "travelers": 0.00040,
    "chubb": 0.00045, "nationwide": 0.00032, "progressive": 0.00030,
    "zurich": 0.00042, "liberty_mutual": 0.00038, "cna": 0.00037,
    "hiscox": 0.00025, "markel": 0.00033, "simply_business": 0.00022,
}


def _score_carriers(mods: dict, customer_id: str = "maria_bakery_tx",
                    revenue: int = 3_600_000) -> list[dict]:
    """Score carriers using Neo4j hybrid engine; fallback to static list."""
    if mods.get("has_neo4j") and mods.get("scoring"):
        try:
            rankings = mods["scoring"].score_customer(
                customer_id=customer_id,
                top_n=5,
                explain=False,
            )
            if rankings:
                for r in rankings:
                    cid = r["carrier_id"]
                    r["est_premium"] = max(800, int(revenue * _BASE_RATES.get(cid, 0.00035)))
                return rankings
        except Exception as e:
            logger.warning(f"Neo4j scoring failed: {e}")

    # Static fallback
    return [
        {"carrier_id": "next",      "carrier_name": "NEXT Insurance",   "total_score": 93.5,
         "quote_speed": "15 min",   "est_premium": 1008},
        {"carrier_id": "hartford",  "carrier_name": "The Hartford",     "total_score": 87.2,
         "quote_speed": "2 hr",     "est_premium": 1260},
        {"carrier_id": "travelers", "carrier_name": "Travelers",        "total_score": 82.1,
         "quote_speed": "2 hr",     "est_premium": 1440},
    ]


# ── PIPELINE ──────────────────────────────────────────────────────────────────

def run_pipeline(email_data: dict, mods: dict) -> dict:
    """
    Full analysis pipeline for one detected trigger email.

    email_data: {subject, body, from, confidence, agent_result (optional)}
    Returns pipeline result dict.
    """
    subject = email_data.get("subject", "")
    body    = email_data.get("body",    "")
    result  = {"email": email_data, "steps": {}}

    # ── Step 1: Extract requirements ─────────────────────────────────────────
    extracted = {}
    if mods.get("extractor"):
        try:
            extracted = mods["extractor"].extract(subject + "\n\n" + body)
            result["steps"]["extraction"] = {"ok": True, "data": extracted}
            logger.info(
                f"  Extracted: GL=${extracted.get('gl_limit', 'n/a')} "
                f"deadline={extracted.get('deadline', 'n/a')}"
            )
        except Exception as e:
            logger.warning(f"  Extraction failed: {e}")
            result["steps"]["extraction"] = {"ok": False, "error": str(e)}
    else:
        # Minimal fallback from agent_result if present
        ar = email_data.get("agent_result")
        if ar and hasattr(ar, "extracted"):
            extracted = ar.extracted

    # ── Step 2: Score carriers ────────────────────────────────────────────────
    # Map extracted retailer to customer ID (extend as needed)
    retailer  = extracted.get("retailer", "Whole Foods")
    cust_id   = "maria_bakery_tx"  # In production, look up by email domain
    carriers  = _score_carriers(mods, customer_id=cust_id)
    result["steps"]["scoring"] = {"ok": bool(carriers), "count": len(carriers)}
    logger.info(f"  Scored {len(carriers)} carriers; top={carriers[0].get('carrier_name','?')}")

    # ── Step 3: Build and send alert ─────────────────────────────────────────
    top = carriers[0] if carriers else {}
    analysis = {
        "customer_name":     "Maria",
        "customer_id":       cust_id,
        "current_carrier":   "Simply Business",
        "current_carrier_id":"simply_business",
        "current_limit":     "$1,000,000",
        "required_limit":    f"${extracted.get('gl_limit', 2_000_000):,}" if extracted.get("gl_limit") else "$2,000,000",
        "deadline":          str(extracted.get("deadline", "Mar 14, 2026")),
        "days_left":         int(extracted.get("deadline_days", 8)),
        "retailer":          retailer,
        "top_carriers": [
            {
                "name":        c.get("carrier_name", c.get("name", "")),
                "score":       c.get("total_score",  c.get("score", 0)),
                "quote_speed": c.get("quote_speed",  "varies"),
                "est_premium": c.get("est_premium",  0),
            }
            for c in carriers
        ],
    }

    if mods.get("gmail"):
        alert_ok = mods["gmail"].send_bindiq_alert(ALERT_TO, analysis)
        result["steps"]["alert"] = {"ok": alert_ok, "to": ALERT_TO}
        logger.info(f"  Alert email {'sent' if alert_ok else 'FAILED'} to {ALERT_TO}")
    else:
        result["steps"]["alert"] = {"ok": False, "reason": "gmail not configured"}

    result["analysis"] = analysis
    return result


# ── POLL LOOP ─────────────────────────────────────────────────────────────────

def check_once(mods: dict) -> list[dict]:
    """Single inbox check. Returns list of pipeline results."""
    if not mods.get("gmail"):
        logger.warning("Gmail module not available")
        return []

    logger.info("Checking inbox...")
    triggers = mods["gmail"].check_inbox_for_trigger(since_minutes=POLL_INTERVAL // 60 + 2)

    if not triggers:
        logger.info("No trigger emails found")
        return []

    logger.info(f"Found {len(triggers)} trigger email(s)")
    results = []
    for email_data in triggers:
        logger.info(f"Processing: '{email_data.get('subject', '')[:60]}' ({email_data.get('confidence', 0)}%)")
        result = run_pipeline(email_data, mods)
        results.append(result)

    return results


def poll_loop(mods: dict):
    """Continuous polling loop — runs forever until interrupted."""
    logger.info(f"Starting poll loop (interval={POLL_INTERVAL}s, {POLL_INTERVAL//60} min)")
    logger.info(f"Monitoring: {ALERT_TO}")

    while True:
        try:
            check_once(mods)
        except Exception as e:
            logger.error(f"Poll iteration error: {e}")

        next_check = datetime.now().strftime("%H:%M:%S")
        logger.info(f"Next check at {next_check} + {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)


# ── FLASK WEBHOOK ─────────────────────────────────────────────────────────────

def make_flask_app(mods: dict):
    """
    Flask app that ServiceNow Flow Designer calls every 5 min.
    POST /api/check-inbox  -> runs pipeline, returns JSON result
    GET  /api/status       -> returns monitor status
    """
    try:
        from flask import Flask, jsonify, request as flask_request
    except ImportError:
        logger.warning("Flask not installed — webhook server disabled. pip install flask")
        return None

    app = Flask("bindiq_monitor")

    @app.post("/api/check-inbox")
    def api_check_inbox():
        body = flask_request.get_json(silent=True) or {}
        logger.info(f"Webhook triggered by {body.get('source', 'unknown')}")

        results = check_once(mods)
        return jsonify({
            "ok":       True,
            "triggers": len(results),
            "results":  [
                {
                    "subject":    r["email"].get("subject", "")[:80],
                    "confidence": r["email"].get("confidence", 0),
                    "alert_sent": r["steps"].get("alert", {}).get("ok", False),
                    "top_carrier":r.get("analysis", {}).get("top_carriers", [{}])[0].get("name", ""),
                }
                for r in results
            ],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

    @app.get("/api/status")
    def api_status():
        return jsonify({
            "service":    "BindIQ Email Monitor",
            "gmail":      mods.get("can_send", False),
            "neo4j":      mods.get("has_neo4j", False),
            "agent":      "agent" in mods,
            "poll_interval_seconds": POLL_INTERVAL,
            "monitoring": ALERT_TO,
        })

    return app


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BindIQ Email Monitor")
    parser.add_argument("--once",         action="store_true", help="Check inbox once and exit")
    parser.add_argument("--webhook-only", action="store_true", help="Run webhook server only (no poll loop)")
    parser.add_argument("--poll-only",    action="store_true", help="Run poll loop only (no Flask server)")
    parser.add_argument("--port",  type=int, default=5000,    help="Flask webhook port (default 5000)")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("BindIQ Email Monitor")
    logger.info("=" * 60)

    mods = _load_modules()
    logger.info(f"Gmail: {'ready' if mods.get('can_send') else 'simulation mode'}")
    logger.info(f"Neo4j: {'connected' if mods.get('has_neo4j') else 'offline (static fallback)'}")
    logger.info(f"Agent: {'ready' if mods.get('agent') else 'unavailable'}")

    if args.once:
        results = check_once(mods)
        logger.info(f"Done. Processed {len(results)} trigger email(s).")
        return

    flask_app = None if args.poll_only else make_flask_app(mods)

    if flask_app and not args.webhook_only:
        # Run Flask in background thread; polling in main thread
        def run_flask():
            flask_app.run(host="0.0.0.0", port=args.port, debug=False, use_reloader=False)

        t = threading.Thread(target=run_flask, daemon=True)
        t.start()
        logger.info(f"Webhook server listening on http://0.0.0.0:{args.port}")
        poll_loop(mods)

    elif flask_app and args.webhook_only:
        logger.info(f"Webhook-only mode — listening on http://0.0.0.0:{args.port}")
        flask_app.run(host="0.0.0.0", port=args.port, debug=False)

    else:
        poll_loop(mods)


if __name__ == "__main__":
    main()
