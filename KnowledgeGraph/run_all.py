"""
BindIQ Agent 2 — Orchestrator
Runs the full Knowledge Graph build pipeline:

  Step 1 — neo4j_loader   : Load Agent 1 tables → Neo4j nodes + relationships
  Step 2 — cmdb_loader    : Sync carriers + customers → ServiceNow CMDB
  Step 3 — seed_customers : Load 12 demo customers → Neo4j
  Step 4 — embeddings     : Generate + store embeddings (carriers + customers)
  Step 5 — score demo     : Run hybrid scoring for all demo customers

Usage:
  python run_all.py                          # full pipeline
  python run_all.py --skip-cmdb             # skip ServiceNow (no creds needed)
  python run_all.py --skip-embed            # skip embeddings (no GPU/model)
  python run_all.py --score maria_bakery_tx # score one customer only
  python run_all.py --demo                  # seed + score all 12 customers
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import LOG_DIR, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "run_all.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_all")

STEP_SEPARATOR = "─" * 60


def step(n: int, label: str):
    logger.info(f"\n{STEP_SEPARATOR}")
    logger.info(f"  Step {n}: {label}")
    logger.info(STEP_SEPARATOR)


def run_pipeline(
    skip_cmdb: bool = False,
    skip_embed: bool = False,
    score_customer_id: str | None = None,
    demo_mode: bool = False,
) -> dict:
    started = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("BindIQ Agent 2 — Knowledge Graph Builder")
    logger.info(f"Started: {started.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    results: dict = {"steps": {}, "status": "ok"}

    # ── Step 1: Neo4j Loader ─────────────────────────────────────────────────
    step(1, "Neo4j Loader — Agent 1 tables → graph")
    try:
        import neo4j_loader
        res = neo4j_loader.run()
        results["steps"]["neo4j_loader"] = res
        logger.info(f"  Step 1 OK: {res.get('summary', {})}")
    except FileNotFoundError as e:
        logger.error(f"  Step 1 FAILED: {e}")
        logger.error("  → Run Agent 1 first: python DataExtractor/run_all.py")
        results["steps"]["neo4j_loader"] = {"status": "error", "reason": str(e)}
        results["status"] = "partial"
    except Exception as e:
        logger.error(f"  Step 1 FAILED: {e}")
        results["steps"]["neo4j_loader"] = {"status": "error", "reason": str(e)}
        results["status"] = "partial"

    # ── Step 2: Seed Demo Customers ──────────────────────────────────────────
    step(2, "Seed demo customers → Neo4j")
    try:
        import seed_customers
        res = seed_customers.run()
        results["steps"]["seed_customers"] = {
            "status": "ok",
            "loaded": res["loaded"],
            "failed": res["failed"],
        }
        customers = res.get("customers", [])
        logger.info(f"  Step 2 OK: {res['loaded']} customers seeded")
    except Exception as e:
        logger.error(f"  Step 2 FAILED: {e}")
        results["steps"]["seed_customers"] = {"status": "error", "reason": str(e)}
        customers = []
        results["status"] = "partial"

    # ── Step 3: ServiceNow CMDB ──────────────────────────────────────────────
    if skip_cmdb:
        logger.info(f"\n{STEP_SEPARATOR}")
        logger.info("  Step 3: ServiceNow CMDB — SKIPPED (--skip-cmdb)")
        results["steps"]["cmdb_loader"] = {"status": "skipped"}
    else:
        step(3, "ServiceNow CMDB sync")
        try:
            import cmdb_loader
            res = cmdb_loader.run(customers=customers)
            results["steps"]["cmdb_loader"] = res
            logger.info(f"  Step 3 OK: {res.get('status')}")
        except Exception as e:
            logger.warning(f"  Step 3 WARNING (non-fatal): {e}")
            results["steps"]["cmdb_loader"] = {"status": "error", "reason": str(e)}
            # CMDB failure is non-fatal — graph still works

    # ── Step 4: Embeddings ───────────────────────────────────────────────────
    if skip_embed:
        logger.info(f"\n{STEP_SEPARATOR}")
        logger.info("  Step 4: Embeddings — SKIPPED (--skip-embed)")
        results["steps"]["embeddings"] = {"status": "skipped"}
    else:
        step(4, "Generate + store embeddings")
        try:
            import embeddings
            res = embeddings.run()
            results["steps"]["embeddings"] = res
            logger.info(
                f"  Step 4 OK: {res['carriers_embedded']} carrier, "
                f"{res['customers_embedded']} customer embeddings, "
                f"{res['similarity_edges']} similarity edges"
            )
        except Exception as e:
            logger.warning(f"  Step 4 WARNING (non-fatal): {e}")
            results["steps"]["embeddings"] = {"status": "error", "reason": str(e)}
            # Embedding failure is non-fatal — scoring will use graph/rules only

    # ── Step 5: Scoring ──────────────────────────────────────────────────────
    step(5, "Hybrid scoring demo")
    try:
        from scoring import score_customer
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

        if score_customer_id:
            targets = [score_customer_id]
        elif demo_mode:
            targets = [c["customer_id"] for c in customers]
        else:
            # Score the flagship demo customer
            targets = ["maria_bakery_tx"]

        scoring_results = []
        for cid in targets:
            try:
                rankings = score_customer(cid, driver=driver, top_n=3, explain=True)
                scoring_results.append({"customer_id": cid, "rankings": rankings})
                logger.info(f"\n  Rankings for [{cid}]:")
                for r in rankings:
                    logger.info(
                        f"    {r['carrier_name']:25s}  {r['total_score']:.1f}/100"
                    )
                    if r.get("explanation"):
                        logger.info(f"      → {r['explanation'][:120]}")
            except Exception as e:
                logger.warning(f"  Could not score [{cid}]: {e}")

        driver.close()
        results["steps"]["scoring"] = {"status": "ok", "scored": len(scoring_results)}
        results["scoring"] = scoring_results

    except Exception as e:
        logger.error(f"  Step 5 FAILED: {e}")
        results["steps"]["scoring"] = {"status": "error", "reason": str(e)}

    # ── Summary ──────────────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(f"\n{'=' * 60}")
    logger.info(f"  Pipeline complete in {elapsed:.1f}s")
    logger.info(f"  Overall status: {results['status']}")
    for step_name, step_res in results["steps"].items():
        status = step_res.get("status", "?")
        logger.info(f"    {step_name:20s} → {status}")
    logger.info(f"{'=' * 60}")

    # Save results
    out_path = LOG_DIR / f"run_all_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info(f"  Results saved → {out_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="BindIQ Agent 2 — Knowledge Graph Builder")
    parser.add_argument("--skip-cmdb",  action="store_true", help="Skip ServiceNow CMDB sync")
    parser.add_argument("--skip-embed", action="store_true", help="Skip embedding generation")
    parser.add_argument("--score",      metavar="CUSTOMER_ID",  help="Score a specific customer only")
    parser.add_argument("--demo",       action="store_true", help="Score all 12 demo customers")
    args = parser.parse_args()

    results = run_pipeline(
        skip_cmdb=args.skip_cmdb,
        skip_embed=args.skip_embed,
        score_customer_id=args.score,
        demo_mode=args.demo,
    )

    print(f"\nKnowledge Graph Builder done. Status: {results['status']}")

    if results.get("scoring"):
        print("\nTop carrier recommendations:")
        for entry in results["scoring"]:
            cid = entry["customer_id"]
            print(f"\n  Customer: {cid}")
            for i, r in enumerate(entry["rankings"], 1):
                print(f"    {i}. {r['carrier_name']:25s}  {r['total_score']:.1f}/100")
                if r.get("explanation"):
                    print(f"       {r['explanation'][:100]}...")


if __name__ == "__main__":
    main()
