"""
BindIQ — Seed Similar Customers + Graph Relationships

Adds the missing Neo4j relationships that drive differentiated scoring:
  - SIMILAR_TO  : peer customer similarity (cosine-based, synthetic)
  - INSURED_BY  : historical outcomes
  - SUPPLIES    : retailer relationships
  - HANDLES_VENDOR_REQUIREMENTS : carrier × retailer experience

Run standalone:
  python seed_similar_customers.py

Or import:
  from seed_similar_customers import run
  run()
"""

import logging
import sys
from pathlib import Path
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent))
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE, LOG_DIR

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "seed_similar.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("seed_similar_customers")


# ─────────────────────────────────────────────────────────────────────────────
# SIMILAR CUSTOMER PROFILES
# ─────────────────────────────────────────────────────────────────────────────

SIMILAR_CUSTOMERS = [
    # ── Similar to Maria's Bakery (food_service / TX) ─────────────────────────
    {
        "id":              "sarah_specialty_breads",
        "business_name":   "Sarah's Specialty Breads",
        "industry_id":     "food_service",
        "state":           "TX",
        "annual_revenue":  4_100_000,
        "employee_count":  18,
        "years_in_biz":    9,
        "description":     "Artisan sourdough wholesale bakery supplying Whole Foods and HEB in Austin TX. ServSafe + HACCP certified. Zero claims in 5 years.",
        "certifications":  "ServSafe,HACCP",
        "business_model":  "wholesale_b2b",
        "product_focus":   "artisan_sourdough",
        "wf_vendor":       True,
    },
    {
        "id":              "jakes_artisan_bakery",
        "business_name":   "Jake's Artisan Bakery",
        "industry_id":     "food_service",
        "state":           "TX",
        "annual_revenue":  3_200_000,
        "employee_count":  12,
        "years_in_biz":    6,
        "description":     "Specialty bread and pastry wholesale bakery serving restaurant accounts across DFW. ServSafe certified.",
        "certifications":  "ServSafe",
        "business_model":  "wholesale_b2b",
        "product_focus":   "specialty_breads",
        "wf_vendor":       False,
    },
    {
        "id":              "artisan_bread_co",
        "business_name":   "Artisan Bread Co",
        "industry_id":     "food_service",
        "state":           "TX",
        "annual_revenue":  3_800_000,
        "employee_count":  16,
        "years_in_biz":    11,
        "description":     "Organic artisan bread manufacturer supplying specialty grocery chains in Texas. HACCP + Organic certified. 1 minor claim in 5 years.",
        "certifications":  "ServSafe,HACCP,Organic_Cert",
        "business_model":  "wholesale_b2b",
        "product_focus":   "organic_breads",
        "wf_vendor":       False,
    },
    {
        "id":              "jakes_wholesale_bakery_declined",
        "business_name":   "Jake's Wholesale Bakery",
        "industry_id":     "food_service",
        "state":           "TX",
        "annual_revenue":  3_100_000,
        "employee_count":  11,
        "years_in_biz":    5,
        "description":     "Wholesale bakery with high product recall exposure — declined by Travelers in 2025-Q4 due to wholesale distribution risk.",
        "certifications":  "ServSafe",
        "business_model":  "wholesale_b2b",
        "product_focus":   "specialty_breads",
        "wf_vendor":       False,
    },
    # ── Similar to Rodriguez Construction ─────────────────────────────────────
    {
        "id":              "lopez_construction_llc",
        "business_name":   "Lopez Construction LLC",
        "industry_id":     "construction",
        "state":           "TX",
        "annual_revenue":  4_200_000,
        "employee_count":  20,
        "years_in_biz":    10,
        "description":     "Commercial general contractor in Austin TX specializing in tenant improvements for Hines Development and other major GCs. Required $5M umbrella for Hines job. CNA multi-line package, 0 claims post-umbrella.",
        "certifications":  "OSHA_30,TEXO",
        "business_model":  "commercial_gc",
        "product_focus":   "tenant_improvement",
        "wf_vendor":       False,
    },
    {
        "id":              "apex_commercial_builders",
        "business_name":   "Apex Commercial Builders",
        "industry_id":     "construction",
        "state":           "TX",
        "annual_revenue":  5_100_000,
        "employee_count":  28,
        "years_in_biz":    14,
        "description":     "Commercial office and retail buildout specialist in Austin TX. Multi-line CNA package (GL+Auto+Umbrella) for 3 years.",
        "certifications":  "OSHA_30",
        "business_model":  "commercial_gc",
        "product_focus":   "commercial_ti",
        "wf_vendor":       False,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# INSURED_BY RELATIONSHIPS — historical outcomes
# ─────────────────────────────────────────────────────────────────────────────

INSURED_BY_RELS = [
    # Sarah + NEXT (excellent)
    {
        "customer_id": "sarah_specialty_breads",
        "carrier_id":  "next",
        "start_date":  "2023-05-01",
        "years_active": 3,
        "claims_count": 0,
        "outcome":     "excellent",
        "satisfaction_score": 4.8,
        "renewal_count": 2,
        "avg_premium": 1_150,
    },
    # Jake's Artisan + NEXT (good)
    {
        "customer_id": "jakes_artisan_bakery",
        "carrier_id":  "next",
        "start_date":  "2024-02-15",
        "years_active": 2,
        "claims_count": 0,
        "outcome":     "good",
        "satisfaction_score": 4.5,
        "renewal_count": 1,
        "avg_premium": 980,
    },
    # Artisan Bread Co + Chubb (good, 1 claim handled well)
    {
        "customer_id": "artisan_bread_co",
        "carrier_id":  "chubb",
        "start_date":  "2022-08-01",
        "years_active": 4,
        "claims_count": 1,
        "outcome":     "good",
        "satisfaction_score": 4.2,
        "renewal_count": 3,
        "avg_premium": 1_680,
    },
    # Jake's Wholesale — Travelers DECLINED (not insured, just a declination record)
    # Represented as outcome="declined" with no policy start
    {
        "customer_id": "jakes_wholesale_bakery_declined",
        "carrier_id":  "travelers",
        "start_date":  None,
        "years_active": 0,
        "claims_count": 0,
        "outcome":     "declined",
        "satisfaction_score": None,
        "renewal_count": 0,
        "avg_premium": 0,
        "decline_reason": "wholesale_food_exposure_high_recall_risk",
        "decline_date":   "2025-Q4",
    },
    # Lopez Construction + CNA (excellent)
    {
        "customer_id": "lopez_construction_llc",
        "carrier_id":  "cna",
        "start_date":  "2023-03-01",
        "years_active": 3,
        "claims_count": 0,
        "outcome":     "excellent",
        "satisfaction_score": 4.7,
        "renewal_count": 2,
        "avg_premium": 8_400,
    },
    # Apex + CNA (good)
    {
        "customer_id": "apex_commercial_builders",
        "carrier_id":  "cna",
        "start_date":  "2022-06-01",
        "years_active": 4,
        "claims_count": 1,
        "outcome":     "good",
        "satisfaction_score": 4.4,
        "renewal_count": 3,
        "avg_premium": 9_200,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# SIMILAR_TO RELATIONSHIPS — cosine similarity
# ─────────────────────────────────────────────────────────────────────────────

SIMILAR_TO_RELS = [
    # Maria ↔ Sarah
    {"from": "maria_bakery_tx", "to": "sarah_specialty_breads",
     "score": 0.87, "basis": "revenue_match,product_match,certification_match,wf_vendor"},
    # Maria ↔ Jake's Artisan
    {"from": "maria_bakery_tx", "to": "jakes_artisan_bakery",
     "score": 0.81, "basis": "revenue_match,business_model_match"},
    # Maria ↔ Artisan Bread Co
    {"from": "maria_bakery_tx", "to": "artisan_bread_co",
     "score": 0.79, "basis": "revenue_match,product_match,employee_count_match"},
    # Maria ↔ Jake's Wholesale (declined — also similar, which is a warning)
    {"from": "maria_bakery_tx", "to": "jakes_wholesale_bakery_declined",
     "score": 0.81, "basis": "revenue_match,business_model_match,product_match"},
    # Rodriguez ↔ Lopez
    {"from": "rodriguez_construction", "to": "lopez_construction_llc",
     "score": 0.89, "basis": "revenue_match,industry_match,gc_type_match,tx_state"},
    # Rodriguez ↔ Apex
    {"from": "rodriguez_construction", "to": "apex_commercial_builders",
     "score": 0.82, "basis": "revenue_match,industry_match,gc_type_match"},
]

# ─────────────────────────────────────────────────────────────────────────────
# SUPPLIES RELATIONSHIPS — customer → retailer
# ─────────────────────────────────────────────────────────────────────────────

RETAILERS = [
    {"id": "whole_foods", "name": "Whole Foods Market Inc.",
     "vendor_tier": "premium", "insurance_strict": True,
     "required_gl": 2_000_000, "required_endorsements": "CG_2015,Primary_NonContrib"},
]

SUPPLIES_RELS = [
    {"customer_id": "maria_bakery_tx",      "retailer_id": "whole_foods",
     "contract_value": 500_000, "start_date": "2026-02-07"},
    {"customer_id": "sarah_specialty_breads","retailer_id": "whole_foods",
     "contract_value": 380_000, "start_date": "2023-06-01"},
]

# ─────────────────────────────────────────────────────────────────────────────
# HANDLES_VENDOR_REQUIREMENTS — carrier × retailer experience
# ─────────────────────────────────────────────────────────────────────────────

HANDLES_RELS = [
    {"carrier_id": "next", "retailer_id": "whole_foods",
     "times_processed": 47, "avg_turnaround_hrs": 4,
     "endorsement_templates": "CG_2015,Primary_NonContrib"},
    {"carrier_id": "hartford", "retailer_id": "whole_foods",
     "times_processed": 12, "avg_turnaround_hrs": 8,
     "endorsement_templates": "CG_2015"},
]


# ─────────────────────────────────────────────────────────────────────────────
# NEO4J LOADER
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_customers(session) -> int:
    loaded = 0
    for c in SIMILAR_CUSTOMERS:
        try:
            session.run(
                """
                MERGE (cu:Customer {id: $id})
                SET cu.business_name  = $business_name,
                    cu.industry_id    = $industry_id,
                    cu.state          = $state,
                    cu.annual_revenue = $annual_revenue,
                    cu.employee_count = $employee_count,
                    cu.years_in_biz   = $years_in_biz,
                    cu.description    = $description,
                    cu.certifications = $certifications,
                    cu.business_model = $business_model,
                    cu.product_focus  = $product_focus,
                    cu.wf_vendor      = $wf_vendor
                """,
                **{k: v for k, v in c.items()},
            )
            # OPERATES_IN
            session.run(
                """
                MATCH (cu:Customer {id: $cid})
                MATCH (i:Industry {id: $iid})
                MERGE (cu)-[:OPERATES_IN]->(i)
                """,
                cid=c["id"], iid=c["industry_id"],
            )
            logger.info(f"  + Customer: {c['business_name']} [{c['id']}]")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! Customer {c['id']}: {e}")
    return loaded


def _upsert_insured_by(session) -> int:
    loaded = 0
    for rel in INSURED_BY_RELS:
        try:
            session.run(
                """
                MATCH (cu:Customer {id: $cid})
                MATCH (ca:Carrier  {id: $caid})
                MERGE (cu)-[r:INSURED_BY]->(ca)
                SET r.start_date        = $start_date,
                    r.years_active      = $years_active,
                    r.claims_count      = $claims_count,
                    r.outcome           = $outcome,
                    r.satisfaction_score= $satisfaction_score,
                    r.renewal_count     = $renewal_count,
                    r.avg_premium       = $avg_premium,
                    r.decline_reason    = $decline_reason,
                    r.decline_date      = $decline_date
                """,
                cid=rel["customer_id"],
                caid=rel["carrier_id"],
                start_date=rel.get("start_date"),
                years_active=rel.get("years_active", 0),
                claims_count=rel.get("claims_count", 0),
                outcome=rel.get("outcome"),
                satisfaction_score=rel.get("satisfaction_score"),
                renewal_count=rel.get("renewal_count", 0),
                avg_premium=rel.get("avg_premium", 0),
                decline_reason=rel.get("decline_reason"),
                decline_date=rel.get("decline_date"),
            )
            logger.info(f"  + INSURED_BY: {rel['customer_id']} → {rel['carrier_id']} ({rel['outcome']})")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! INSURED_BY {rel['customer_id']}→{rel['carrier_id']}: {e}")
    return loaded


def _upsert_similar_to(session) -> int:
    loaded = 0
    for rel in SIMILAR_TO_RELS:
        try:
            session.run(
                """
                MATCH (a:Customer {id: $from_id})
                MATCH (b:Customer {id: $to_id})
                MERGE (a)-[r:SIMILAR_TO]-(b)
                SET r.similarity_score   = $score,
                    r.basis              = $basis,
                    r.computed_method    = 'embedding_cosine'
                """,
                from_id=rel["from"],
                to_id=rel["to"],
                score=rel["score"],
                basis=rel["basis"],
            )
            logger.info(f"  + SIMILAR_TO: {rel['from']} ↔ {rel['to']} ({rel['score']:.2f})")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! SIMILAR_TO {rel['from']}↔{rel['to']}: {e}")
    return loaded


def _upsert_retailers(session) -> int:
    loaded = 0
    for r in RETAILERS:
        try:
            session.run(
                """
                MERGE (ret:Retailer {id: $id})
                SET ret.name                 = $name,
                    ret.vendor_tier          = $vendor_tier,
                    ret.insurance_strict     = $insurance_strict,
                    ret.required_gl          = $required_gl,
                    ret.required_endorsements= $required_endorsements
                """,
                **r,
            )
            logger.info(f"  + Retailer: {r['name']}")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! Retailer {r['id']}: {e}")
    return loaded


def _upsert_supplies(session) -> int:
    loaded = 0
    for rel in SUPPLIES_RELS:
        try:
            session.run(
                """
                MATCH (cu:Customer {id: $cid})
                MATCH (ret:Retailer {id: $rid})
                MERGE (cu)-[r:SUPPLIES]->(ret)
                SET r.contract_value = $contract_value,
                    r.start_date     = $start_date
                """,
                cid=rel["customer_id"],
                rid=rel["retailer_id"],
                contract_value=rel["contract_value"],
                start_date=rel["start_date"],
            )
            logger.info(f"  + SUPPLIES: {rel['customer_id']} → {rel['retailer_id']}")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! SUPPLIES {rel['customer_id']}: {e}")
    return loaded


def _upsert_handles(session) -> int:
    loaded = 0
    for rel in HANDLES_RELS:
        try:
            session.run(
                """
                MATCH (ca:Carrier {id: $cid})
                MATCH (ret:Retailer {id: $rid})
                MERGE (ca)-[r:HANDLES_VENDOR_REQUIREMENTS]->(ret)
                SET r.times_processed        = $times_processed,
                    r.avg_turnaround_hrs     = $avg_turnaround_hrs,
                    r.endorsement_templates  = $endorsement_templates
                """,
                cid=rel["carrier_id"],
                rid=rel["retailer_id"],
                times_processed=rel["times_processed"],
                avg_turnaround_hrs=rel["avg_turnaround_hrs"],
                endorsement_templates=rel["endorsement_templates"],
            )
            logger.info(f"  + HANDLES: {rel['carrier_id']} → {rel['retailer_id']} ({rel['times_processed']} times)")
            loaded += 1
        except Exception as e:
            logger.warning(f"  ! HANDLES {rel['carrier_id']}: {e}")
    return loaded


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ — Seed Similar Customers + Graph Relationships")
    logger.info("=" * 60)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    results = {}

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            logger.info("\n[1/6] Upserting similar customer nodes…")
            results["customers"] = _upsert_customers(session)

            logger.info("\n[2/6] Creating INSURED_BY relationships…")
            results["insured_by"] = _upsert_insured_by(session)

            logger.info("\n[3/6] Creating SIMILAR_TO relationships…")
            results["similar_to"] = _upsert_similar_to(session)

            logger.info("\n[4/6] Creating Retailer nodes…")
            results["retailers"] = _upsert_retailers(session)

            logger.info("\n[5/6] Creating SUPPLIES relationships…")
            results["supplies"] = _upsert_supplies(session)

            logger.info("\n[6/6] Creating HANDLES_VENDOR_REQUIREMENTS relationships…")
            results["handles"] = _upsert_handles(session)

    finally:
        driver.close()

    logger.info("\n" + "=" * 60)
    logger.info("Done: " + ", ".join(f"{k}={v}" for k, v in results.items()))
    logger.info("=" * 60)
    return {"status": "ok", **results}


if __name__ == "__main__":
    result = run()
    print(f"\nSeed complete: {result}")
