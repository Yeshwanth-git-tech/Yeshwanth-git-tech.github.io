"""
BindIQ Agent 2 — Demo Customer Seed
Loads 12 synthetic customers into Neo4j (one per industry scenario).

Each customer matches the "competition story" from Agent 1 config:
  Maria's Bakery (food_service / TX) — wins Whole Foods contract
  Atlas Construction (construction / OH) — wins office buildout
  ... etc.

Run standalone:
  python seed_customers.py
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
        logging.FileHandler(LOG_DIR / "seed.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("seed_customers")


# ═════════════════════════════════════════════════════════════════════════════
# DEMO CUSTOMER DATA
# ═════════════════════════════════════════════════════════════════════════════

DEMO_CUSTOMERS = [
    {
        "customer_id":        "maria_bakery_tx",
        "business_name":      "Maria's Artisan Bakery",
        "industry_id":        "food_service",
        "state":              "TX",
        "annual_revenue":     850_000,
        "employee_count":     12,
        "years_in_business":  7,
        "coverage_needs":     ["GL", "product_liability", "liquor_liability"],
        "urgency":            "48_hours",
        "description": (
            "Maria's Artisan Bakery is a 7-year-old specialty bakery in Austin, TX "
            "that just won a $500K supply contract with Whole Foods. We make custom "
            "artisan breads, pastries, and cakes for restaurants and grocery retail. "
            "We need $2M general liability and product liability coverage urgently "
            "to satisfy the Whole Foods vendor requirement within 48 hours."
        ),
    },
    {
        "customer_id":        "atlas_construction_oh",
        "business_name":      "Atlas Commercial Contractors",
        "industry_id":        "construction",
        "state":              "OH",
        "annual_revenue":     4_200_000,
        "employee_count":     35,
        "years_in_business":  12,
        "coverage_needs":     ["GL", "workers_comp", "builders_risk", "umbrella"],
        "urgency":            "72_hours",
        "description": (
            "Atlas Commercial Contractors is an Ohio-based general contractor "
            "specialising in office and commercial buildouts. We recently won a "
            "$3M office renovation project under AIA contract, which requires "
            "$2M GL and $5M umbrella before we break ground. "
            "We employ licensed electricians, plumbers, and carpenters."
        ),
    },
    {
        "customer_id":        "precision_machining_in",
        "business_name":      "Precision Machining Solutions",
        "industry_id":        "manufacturing",
        "state":              "IN",
        "annual_revenue":     6_500_000,
        "employee_count":     58,
        "years_in_business":  18,
        "coverage_needs":     ["GL", "product_liability", "property", "equipment_breakdown"],
        "urgency":            "5_days",
        "description": (
            "Precision Machining Solutions is an Indiana machine shop that just won "
            "a Tesla supplier contract for custom CNC-machined EV battery components. "
            "Tesla requires $5M product liability coverage within 5 days or we lose "
            "the contract. We operate 24/7 with heavy CNC equipment and ISO 9001 certification."
        ),
    },
    {
        "customer_id":        "cloudpeak_tech_ca",
        "business_name":      "CloudPeak Technology",
        "industry_id":        "technology",
        "state":              "CA",
        "annual_revenue":     2_100_000,
        "employee_count":     22,
        "years_in_business":  4,
        "coverage_needs":     ["tech_eo", "cyber", "GL", "professional_liability"],
        "urgency":            "48_hours",
        "description": (
            "CloudPeak Technology is a California SaaS startup providing AI-powered "
            "workflow automation. We just landed a Fortune 500 enterprise client that "
            "requires $2M cyber liability and $1M E&O within 48 hours of contract signing. "
            "We handle sensitive HR and financial data for our clients."
        ),
    },
    {
        "customer_id":        "sunstone_medical_fl",
        "business_name":      "Sunstone Medical Group",
        "industry_id":        "healthcare",
        "state":              "FL",
        "annual_revenue":     3_800_000,
        "employee_count":     28,
        "years_in_business":  9,
        "coverage_needs":     ["malpractice", "GL", "cyber", "workers_comp"],
        "urgency":            "5_days",
        "description": (
            "Sunstone Medical Group is a multi-specialty outpatient clinic in Tampa, FL. "
            "We are joining a hospital network credentialing process that requires "
            "proof of $1M/$3M malpractice coverage within 5 days. "
            "We employ 6 physicians and 22 clinical staff across 3 locations."
        ),
    },
    {
        "customer_id":        "fastlane_logistics_tx",
        "business_name":      "FastLane Logistics",
        "industry_id":        "logistics_transport",
        "state":              "TX",
        "annual_revenue":     5_400_000,
        "employee_count":     44,
        "years_in_business":  6,
        "coverage_needs":     ["commercial_auto", "cargo", "GL", "workers_comp"],
        "urgency":            "72_hours",
        "description": (
            "FastLane Logistics is a Dallas-based last-mile delivery company that won "
            "an Amazon DSP (Delivery Service Partner) contract. Amazon requires full "
            "commercial auto fleet insurance plus cargo coverage within 3 days. "
            "We operate 28 vans and 4 cargo trucks serving the DFW metro area."
        ),
    },
    {
        "customer_id":        "pinnacle_realty_ny",
        "business_name":      "Pinnacle Property Management",
        "industry_id":        "real_estate",
        "state":              "NY",
        "annual_revenue":     2_700_000,
        "employee_count":     18,
        "years_in_business":  14,
        "coverage_needs":     ["GL", "property", "umbrella", "eo_real_estate"],
        "urgency":            "30_days",
        "description": (
            "Pinnacle Property Management manages 50+ commercial properties across "
            "New York City. We just signed a 50-unit commercial lease portfolio where "
            "each property requires a separate certificate of insurance within 30 days. "
            "We need GL, property, and umbrella coverage for the entire portfolio."
        ),
    },
    {
        "customer_id":        "vertex_consulting_ca",
        "business_name":      "Vertex Strategy Consulting",
        "industry_id":        "professional_services",
        "state":              "CA",
        "annual_revenue":     1_800_000,
        "employee_count":     14,
        "years_in_business":  5,
        "coverage_needs":     ["professional_liability", "eo", "GL", "cyber", "do"],
        "urgency":            "7_days",
        "description": (
            "Vertex Strategy Consulting is a boutique management consulting firm in "
            "San Francisco that just landed a Deloitte subcontract. Deloitte requires "
            "$2M E&O and $1M D&O within one week. We advise Fortune 1000 clients "
            "on digital transformation and operational restructuring."
        ),
    },
    {
        "customer_id":        "coastal_retail_fl",
        "business_name":      "Coastal Home & Garden",
        "industry_id":        "retail",
        "state":              "FL",
        "annual_revenue":     3_200_000,
        "employee_count":     31,
        "years_in_business":  11,
        "coverage_needs":     ["GL", "product_liability", "property", "crime"],
        "urgency":            "standard",
        "description": (
            "Coastal Home & Garden is a Florida-based consumer goods brand that just "
            "secured shelf space at Target nationwide. The Target vendor agreement "
            "requires $2M product liability insurance before the first shipment. "
            "We sell outdoor furniture, garden decor, and home accessories."
        ),
    },
    {
        "customer_id":        "sparkle_clean_ca",
        "business_name":      "Sparkle Commercial Cleaning",
        "industry_id":        "cleaning_services",
        "state":              "CA",
        "annual_revenue":     780_000,
        "employee_count":     23,
        "years_in_business":  8,
        "coverage_needs":     ["GL", "workers_comp", "bonding", "commercial_auto"],
        "urgency":            "24_hours",
        "description": (
            "Sparkle Commercial Cleaning is a Bay Area commercial janitorial company "
            "that just won a contract to clean Google's Mountain View campus. "
            "Google requires $1M GL and $500K bonding within 24 hours. "
            "We operate 6 cleaning vans and serve 80+ commercial clients."
        ),
    },
    {
        "customer_id":        "greenthumb_landscape_tx",
        "business_name":      "GreenThumb Landscape Services",
        "industry_id":        "landscaping",
        "state":              "TX",
        "annual_revenue":     1_200_000,
        "employee_count":     19,
        "years_in_business":  10,
        "coverage_needs":     ["GL", "workers_comp", "commercial_auto", "equipment"],
        "urgency":            "48_hours",
        "description": (
            "GreenThumb Landscape Services won a City of Houston parks maintenance "
            "contract. The municipality requires $2M GL and workers comp certificate "
            "before the first day on site. We maintain 40+ commercial and municipal "
            "properties with a crew of 19 and $400K in landscape equipment."
        ),
    },
    {
        "customer_id":        "frontier_foods_oh",
        "business_name":      "Frontier Foods Manufacturing",
        "industry_id":        "food_manufacturing",
        "state":              "OH",
        "annual_revenue":     8_900_000,
        "employee_count":     72,
        "years_in_business":  15,
        "coverage_needs":     ["product_liability", "GL", "property", "recall_insurance"],
        "urgency":            "5_days",
        "description": (
            "Frontier Foods Manufacturing is an Ohio food processing plant that just "
            "won a Walmart supplier deal for private-label snack foods. Walmart requires "
            "$5M product liability and recall coverage within 5 days. "
            "We produce 2M+ units monthly with SQF Level 2 food safety certification."
        ),
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# NEO4J LOADER
# ═════════════════════════════════════════════════════════════════════════════

def load_customer_to_neo4j(session, customer: dict) -> None:
    # 1. Create/update Customer node
    session.run(
        """
        MERGE (cu:Customer {id: $id})
        SET cu.business_name   = $business_name,
            cu.industry_id     = $industry_id,
            cu.state           = $state,
            cu.annual_revenue  = $annual_revenue,
            cu.employee_count  = $employee_count,
            cu.years_in_biz    = $years_in_biz,
            cu.description     = $description,
            cu.coverage_needs  = $coverage_needs,
            cu.urgency         = $urgency
        """,
        id=customer["customer_id"],
        business_name=customer["business_name"],
        industry_id=customer["industry_id"],
        state=customer["state"],
        annual_revenue=customer["annual_revenue"],
        employee_count=customer["employee_count"],
        years_in_biz=customer["years_in_business"],
        description=customer["description"],
        coverage_needs=", ".join(customer["coverage_needs"]),
        urgency=customer["urgency"],
    )

    # 2. OPERATES_IN relationship
    session.run(
        """
        MATCH (cu:Customer {id: $cid})
        MATCH (i:Industry {id: $iid})
        MERGE (cu)-[:OPERATES_IN]->(i)
        """,
        cid=customer["customer_id"],
        iid=customer["industry_id"],
    )

    # 3. Customer's state node (ensure it exists)
    session.run(
        """
        MERGE (s:State {code: $code})
        SET s.name = $name
        """,
        code=customer["state"],
        name=customer["state"],
    )


def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 2 — Demo Customer Seed")
    logger.info(f"Seeding {len(DEMO_CUSTOMERS)} customers into Neo4j")
    logger.info("=" * 60)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    loaded = 0
    failed = 0

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            for customer in DEMO_CUSTOMERS:
                try:
                    load_customer_to_neo4j(session, customer)
                    logger.info(f"  + {customer['business_name']} [{customer['customer_id']}]")
                    loaded += 1
                except Exception as e:
                    logger.warning(f"  ! Failed: {customer['customer_id']}: {e}")
                    failed += 1

    finally:
        driver.close()

    logger.info(f"\n  Seeded: {loaded} customers, {failed} failed")
    return {"status": "ok", "loaded": loaded, "failed": failed, "customers": DEMO_CUSTOMERS}


if __name__ == "__main__":
    result = run()
    print(f"\nSeed done: {result['loaded']} customers loaded")
