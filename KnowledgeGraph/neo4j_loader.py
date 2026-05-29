"""
BindIQ Agent 2 — Neo4j Loader
Reads Agent 1's kg_master JSON and loads it into Neo4j as nodes + relationships.

Node labels:
  (:Carrier)   (:Industry)   (:State)   (:Customer)

Relationships:
  (Carrier)-[:SPECIALIZES_IN {score}]->(Industry)
  (Carrier)-[:LICENSED_IN {tier}]->(State)
  (Customer)-[:OPERATES_IN]->(Industry)
  (Customer)-[:INSURED_BY {outcome}]->(Carrier)
  (Customer)-[:SIMILAR_TO {score}]->(Customer)

Run standalone:
  python neo4j_loader.py
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase, exceptions as neo4j_exc

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE,
    AGENT1_DIR, LOG_DIR,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "neo4j_loader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("neo4j_loader")


# ═════════════════════════════════════════════════════════════════════════════
# DRIVER
# ═════════════════════════════════════════════════════════════════════════════

def get_driver():
    try:
        driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
        driver.verify_connectivity()
        logger.info(f"Neo4j connected: {NEO4J_URI} / db={NEO4J_DATABASE}")
        return driver
    except neo4j_exc.ServiceUnavailable as e:
        logger.error(f"Neo4j unavailable: {e}")
        raise


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA (indexes + constraints)
# ═════════════════════════════════════════════════════════════════════════════

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT carrier_id IF NOT EXISTS FOR (c:Carrier) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT industry_id IF NOT EXISTS FOR (i:Industry) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT state_code IF NOT EXISTS FOR (s:State) REQUIRE s.code IS UNIQUE",
    "CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (cu:Customer) REQUIRE cu.id IS UNIQUE",
    "CREATE INDEX carrier_name IF NOT EXISTS FOR (c:Carrier) ON (c.name)",
    "CREATE INDEX customer_industry IF NOT EXISTS FOR (cu:Customer) ON (cu.industry_id)",
]


def create_schema(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        for stmt in SCHEMA_STATEMENTS:
            try:
                session.run(stmt)
            except Exception as e:
                logger.debug(f"Schema stmt skipped (may exist): {e}")
    logger.info("Schema / indexes ready")


# ═════════════════════════════════════════════════════════════════════════════
# LOAD AGENT 1 DATA
# ═════════════════════════════════════════════════════════════════════════════

def find_latest_master(agent1_dir: Path) -> Path | None:
    files = sorted(agent1_dir.glob("kg_master_*.json"), reverse=True)
    return files[0] if files else None


def load_agent1_data(agent1_dir: Path) -> dict:
    master = find_latest_master(agent1_dir)
    if not master:
        raise FileNotFoundError(
            f"No kg_master_*.json found in {agent1_dir}. "
            "Run Agent 1 first: python DataExtractor/run_all.py"
        )
    logger.info(f"Loading Agent 1 data: {master.name}")
    return json.loads(master.read_text(encoding="utf-8"))


# ═════════════════════════════════════════════════════════════════════════════
# NODE LOADERS
# ═════════════════════════════════════════════════════════════════════════════

def load_carriers(session, table1: list):
    """Load (:Carrier) nodes from kg_table_1_carrier_identity."""
    count = 0
    for row in table1:
        session.run(
            """
            MERGE (c:Carrier {id: $id})
            SET c.name         = $name,
                c.am_best      = $am_best,
                c.type         = $type,
                c.founded_year = $founded_year,
                c.naic_code    = $naic_code,
                c.updated_at   = $ts
            """,
            id=row["carrier_id"],
            name=row.get("name", row["carrier_id"]),
            am_best=row.get("am_best_rating", ""),
            type=row.get("type", ""),
            founded_year=row.get("founded_year", 0),
            naic_code=row.get("naic_code", ""),
            ts=datetime.now(timezone.utc).isoformat(),
        )
        count += 1
    logger.info(f"  Carriers: {count} upserted")


def load_industries(session, table4: list):
    """Load (:Industry) nodes from kg_table_4_appetite."""
    seen = set()
    count = 0
    for row in table4:
        iid = row["industry_id"]
        if iid in seen:
            continue
        seen.add(iid)
        session.run(
            """
            MERGE (i:Industry {id: $id})
            SET i.name = $name
            """,
            id=iid,
            name=row.get("industry_name", iid.replace("_", " ").title()),
        )
        count += 1
    logger.info(f"  Industries: {count} upserted")


def load_states(session, table5: list):
    """Load (:State) nodes from kg_table_5_state_presence."""
    seen = set()
    count = 0
    for row in table5:
        # Handle both field name variants: state (from kg_master) or state_code
        code = row.get("state") or row.get("state_code")
        if not code or code in seen:
            continue
        seen.add(code)
        session.run(
            """
            MERGE (s:State {code: $code})
            SET s.name = $name
            """,
            code=code,
            name=row.get("state_name", code),
        )
        count += 1
    logger.info(f"  States: {count} upserted")


# ═════════════════════════════════════════════════════════════════════════════
# RELATIONSHIP LOADERS
# ═════════════════════════════════════════════════════════════════════════════

# Maps the text appetite label (from Agent 1 carrier_collector) to a numeric score.
# Agent 1 outputs: appetite="strong"|"moderate"|"neutral"|"low"|"none"
# is_specialty=True adds a +0.05 bonus (capped at 1.0).
_APPETITE_TO_SCORE = {
    "strong":   0.90,
    "moderate": 0.60,
    "neutral":  0.35,
    "low":      0.15,
    "none":     0.05,
}


def _appetite_score_from_row(row: dict) -> float:
    """Compute a numeric SPECIALIZES_IN score from appetite text fields."""
    # Prefer explicit numeric field if Agent 1 ever adds it
    if "appetite_score" in row and row["appetite_score"] is not None:
        return float(row["appetite_score"])
    base = _APPETITE_TO_SCORE.get(str(row.get("appetite", "neutral")).lower(), 0.35)
    if row.get("is_specialty") or row.get("is_primary_focus"):
        base = min(1.0, base + 0.05)
    return base


def load_specializes_in(session, table4: list):
    """(Carrier)-[:SPECIALIZES_IN {score, tier}]->(Industry)"""
    count = 0
    for row in table4:
        score = _appetite_score_from_row(row)
        tier  = row.get("tier") or str(row.get("appetite", "neutral")).lower()
        session.run(
            """
            MATCH (c:Carrier {id: $cid})
            MATCH (i:Industry {id: $iid})
            MERGE (c)-[r:SPECIALIZES_IN]->(i)
            SET r.score    = $score,
                r.tier     = $tier,
                r.is_focus = $is_focus
            """,
            cid=row["carrier_id"],
            iid=row["industry_id"],
            score=score,
            tier=tier,
            is_focus=bool(row.get("is_primary_focus") or row.get("is_specialty")),
        )
        count += 1
    logger.info(f"  SPECIALIZES_IN: {count} relationships")


def load_licensed_in(session, table5: list):
    """(Carrier)-[:LICENSED_IN {tier, complaint_ratio}]->(State)"""
    count = 0
    for row in table5:
        if not row.get("is_licensed", True):
            continue
        # Handle both field name variants: state (from kg_master) or state_code
        code = row.get("state") or row.get("state_code")
        if not code:
            continue
        session.run(
            """
            MATCH (c:Carrier {id: $cid})
            MATCH (s:State {code: $code})
            MERGE (c)-[r:LICENSED_IN]->(s)
            SET r.tier             = $tier,
                r.complaint_ratio  = $cr,
                r.market_share_pct = $ms
            """,
            cid=row["carrier_id"],
            code=code,
            tier=row.get("presence_tier", "standard"),
            cr=float(row.get("complaint_ratio", 1.0)),
            ms=float(row.get("market_share_pct", 0.0)),
        )
        count += 1
    logger.info(f"  LICENSED_IN: {count} relationships")


def load_pricing(session, table2: list):
    """Store pricing benchmarks as Carrier node properties (JSON blob)."""
    # Group by carrier
    pricing_map: dict[str, list] = {}
    for row in table2:
        cid = row["carrier_id"]
        pricing_map.setdefault(cid, []).append(row)

    count = 0
    for cid, rows in pricing_map.items():
        avg_monthly = None
        monthly_vals = [
            r["monthly_rate"] for r in rows
            if r.get("monthly_rate") and r["monthly_rate"] > 0
        ]
        if monthly_vals:
            avg_monthly = round(sum(monthly_vals) / len(monthly_vals), 2)

        session.run(
            """
            MATCH (c:Carrier {id: $cid})
            SET c.avg_monthly_gl = $avg_monthly,
                c.pricing_rows   = $count_rows
            """,
            cid=cid,
            avg_monthly=avg_monthly,
            count_rows=len(rows),
        )
        count += 1
    logger.info(f"  Pricing: updated {count} carriers")


def load_reliability(session, table3: list):
    """Store reliability data as Carrier node properties."""
    count = 0
    for row in table3:
        session.run(
            """
            MATCH (c:Carrier {id: $cid})
            SET c.complaint_ratio_nat  = $cr,
                c.insurify_rating      = $ins_rating,
                c.binding_speed_tier   = $speed,
                c.digital_maturity     = $digital
            """,
            cid=row["carrier_id"],
            cr=float(row.get("complaint_ratio") or 1.0),
            ins_rating=float(row.get("insurify_overall_rating") or 0.0),
            speed=row.get("binding_speed_tier", "standard"),
            digital=row.get("digital_maturity_score", 0),
        )
        count += 1
    logger.info(f"  Reliability: updated {count} carriers")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 2 — Neo4j Loader")
    logger.info("=" * 60)

    # 1. Load Agent 1 output
    data = load_agent1_data(AGENT1_DIR)
    
    # Handle both kg_master format (with tables key) and individual table format
    if "tables" in data:
        # kg_master format: data["tables"]["carrier_identity"]["data"]
        tables = data["tables"]
        t1 = tables.get("carrier_identity", {}).get("data", [])
        t2 = tables.get("pricing_benchmarks", {}).get("data", [])
        t3 = tables.get("reliability", {}).get("data", [])
        t4 = tables.get("appetite", {}).get("data", [])
        t5 = tables.get("state_presence", {}).get("data", [])
    else:
        # Individual table format (fallback)
        t1 = data.get("carrier_identity", [])
        t2 = data.get("pricing_benchmarks", [])
        t3 = data.get("reliability", [])
        t4 = data.get("appetite", [])
        t5 = data.get("state_presence", [])

    logger.info(
        f"Agent 1 data: {len(t1)} carriers | {len(t2)} pricing rows | "
        f"{len(t3)} reliability | {len(t4)} appetite | {len(t5)} state rows"
    )

    # 2. Connect
    driver = get_driver()

    try:
        # 3. Schema
        create_schema(driver)

        # 4. Load nodes
        with driver.session(database=NEO4J_DATABASE) as session:
            logger.info("Loading nodes...")
            load_carriers(session, t1)
            load_industries(session, t4)
            load_states(session, t5)

            logger.info("Loading relationships...")
            load_specializes_in(session, t4)
            load_licensed_in(session, t5)
            load_pricing(session, t2)
            load_reliability(session, t3)

        # 5. Summary query
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run(
                """
                MATCH (c:Carrier) WITH count(c) AS carriers
                MATCH (i:Industry) WITH carriers, count(i) AS industries
                MATCH (s:State) WITH carriers, industries, count(s) AS states
                RETURN carriers, industries, states
                """
            )
            row = result.single()
            summary = dict(row) if row else {}

        logger.info(
            f"\n  Neo4j graph: {summary.get('carriers', 0)} carriers | "
            f"{summary.get('industries', 0)} industries | "
            f"{summary.get('states', 0)} states"
        )
        logger.info("  Neo4j load complete")
        return {"status": "ok", "summary": summary}

    finally:
        driver.close()


if __name__ == "__main__":
    result = run()
    print(f"\nNeo4j loader done. Status: {result['status']}")
    s = result.get("summary", {})
    print(f"  Graph: {s.get('carriers', 0)} carriers, "
          f"{s.get('industries', 0)} industries, "
          f"{s.get('states', 0)} states")
