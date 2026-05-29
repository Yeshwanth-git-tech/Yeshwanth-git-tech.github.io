"""
BindIQ Agent 2 — ServiceNow CMDB Loader
Loads carrier and customer records into ServiceNow via REST Table API.

Tables used:
  u_bindiq_carriers   — 12 carriers (configuration items)
  u_bindiq_customers  — demo/seed customers
  u_bindiq_policies   — placeholder for future policy records

Run standalone:
  python cmdb_loader.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD,
    SNOW_TABLE_CARRIERS, SNOW_TABLE_CUSTOMERS, SNOW_TABLE_POLICIES,
    AGENT1_DIR, LOG_DIR,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "cmdb_loader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("cmdb_loader")

# ── REST helpers ──────────────────────────────────────────────────────────────
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
REQUEST_TIMEOUT = 30


def snow_url(table: str) -> str:
    return f"{SNOW_INSTANCE}/api/now/table/{table}"


def _auth():
    return HTTPBasicAuth(SNOW_USER, SNOW_PASSWORD)


def snow_get(table: str, query: str = "") -> list[dict]:
    """Return all records matching sysparm_query."""
    params = {
        "sysparm_query": query,
        "sysparm_limit": 1000,
        "sysparm_display_value": "false",
    }
    resp = requests.get(
        snow_url(table), headers=HEADERS, auth=_auth(),
        params=params, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def snow_create(table: str, payload: dict) -> dict:
    """Create a new record; return the created record."""
    resp = requests.post(
        snow_url(table), headers=HEADERS, auth=_auth(),
        data=json.dumps(payload), timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("result", {})


def snow_update(table: str, sys_id: str, payload: dict) -> dict:
    """Update an existing record by sys_id."""
    resp = requests.patch(
        f"{snow_url(table)}/{sys_id}", headers=HEADERS, auth=_auth(),
        data=json.dumps(payload), timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("result", {})


def upsert_record(table: str, unique_field: str, unique_value: str, payload: dict) -> tuple[str, bool]:
    """
    Insert or update.
    Returns (sys_id, created: bool).
    """
    existing = snow_get(table, f"{unique_field}={unique_value}")
    if existing:
        sys_id = existing[0]["sys_id"]
        snow_update(table, sys_id, payload)
        return sys_id, False
    else:
        record = snow_create(table, payload)
        return record.get("sys_id", ""), True


# ═════════════════════════════════════════════════════════════════════════════
# CARRIER CMDB RECORDS
# ═════════════════════════════════════════════════════════════════════════════

def build_carrier_payload(row: dict) -> dict:
    """Map Agent 1 carrier_identity row → ServiceNow record fields."""
    # strengths: master uses 'core_strengths', older flat format uses 'strengths'
    strengths = row.get("core_strengths") or row.get("strengths") or []
    if isinstance(strengths, list):
        strengths = ", ".join(strengths)
    return {
        "u_carrier_id":      row["carrier_id"],
        "u_name":            row.get("name", row["carrier_id"]),
        "u_am_best_rating":  row.get("am_best_rating", ""),
        "u_carrier_type":    row.get("carrier_type") or row.get("type", ""),
        "u_founded_year":    str(row.get("founded_year", "")),
        "u_naic_code":       row.get("naic_code", ""),
        "u_strengths":       strengths,
        "u_avg_monthly_gl":  str(row.get("avg_monthly_gl", "")),
        "u_complaint_ratio": str(row.get("complaint_ratio", "")),
        "u_insurify_rating": str(row.get("insurify_rating") or row.get("overall_customer_rating", "")),
        "u_binding_speed":   row.get("binding_speed_tier", ""),
        "u_last_synced":     datetime.now(timezone.utc).isoformat(),
    }


def _extract_table(agent1_data: dict, table_name: str) -> list:
    """
    Handle both master JSON structures:
      - Flat:   agent1_data[table_name] -> list
      - Nested: agent1_data["tables"][table_name]["data"] -> list
    """
    # Nested master format: {"tables": {"carrier_identity": {"data": [...]}}}
    tables = agent1_data.get("tables", {})
    if tables:
        entry = tables.get(table_name, {})
        if isinstance(entry, dict):
            return entry.get("data", [])
        if isinstance(entry, list):
            return entry
    # Flat format: {"carrier_identity": [...]}
    entry = agent1_data.get(table_name, [])
    return entry if isinstance(entry, list) else []


def load_carriers(agent1_data: dict) -> dict:
    carriers = _extract_table(agent1_data, "carrier_identity")
    logger.info(f"Syncing {len(carriers)} carriers to ServiceNow...")

    # Merge reliability data into identity rows for richer CMDB records
    reliability_map = {
        r["carrier_id"]: r
        for r in _extract_table(agent1_data, "reliability")
    }
    for row in carriers:
        rel = reliability_map.get(row["carrier_id"], {})
        row["complaint_ratio"]    = rel.get("complaint_ratio", "")
        row["insurify_rating"]    = rel.get("insurify_overall_rating", "")
        row["binding_speed_tier"] = rel.get("binding_speed_tier", "")

    results = {"created": 0, "updated": 0, "failed": 0}
    for row in carriers:
        cid = row["carrier_id"]
        try:
            payload = build_carrier_payload(row)
            _, created = upsert_record(
                SNOW_TABLE_CARRIERS, "u_carrier_id", cid, payload
            )
            if created:
                results["created"] += 1
                logger.info(f"  + Created carrier: {cid}")
            else:
                results["updated"] += 1
                logger.info(f"  ~ Updated carrier: {cid}")
            time.sleep(0.3)   # respect ServiceNow rate limits
        except Exception as e:
            results["failed"] += 1
            logger.warning(f"  ! Failed carrier [{cid}]: {e}")

    return results


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOMER CMDB RECORDS
# ═════════════════════════════════════════════════════════════════════════════

def build_customer_payload(customer: dict) -> dict:
    return {
        "u_customer_id":    customer["customer_id"],
        "u_business_name":  customer["business_name"],
        "u_industry_id":    customer["industry_id"],
        "u_state":          customer["state"],
        "u_annual_revenue": str(customer.get("annual_revenue", 0)),
        "u_employee_count": str(customer.get("employee_count", 0)),
        "u_years_in_biz":   str(customer.get("years_in_business", 0)),
        "u_description":    customer.get("description", ""),
        "u_coverage_needs": ", ".join(customer.get("coverage_needs", [])),
        "u_urgency":        customer.get("urgency", "standard"),
        "u_last_synced":    datetime.now(timezone.utc).isoformat(),
    }


def load_customers(customers: list[dict]) -> dict:
    logger.info(f"Syncing {len(customers)} customers to ServiceNow...")

    results = {"created": 0, "updated": 0, "failed": 0}
    for customer in customers:
        cid = customer["customer_id"]
        try:
            payload = build_customer_payload(customer)
            _, created = upsert_record(
                SNOW_TABLE_CUSTOMERS, "u_customer_id", cid, payload
            )
            if created:
                results["created"] += 1
                logger.info(f"  + Created customer: {customer['business_name']} [{cid}]")
            else:
                results["updated"] += 1
                logger.info(f"  ~ Updated customer: {customer['business_name']}")
            time.sleep(0.3)
        except Exception as e:
            results["failed"] += 1
            logger.warning(f"  ! Failed customer [{cid}]: {e}")

    return results


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTIVITY CHECK
# ═════════════════════════════════════════════════════════════════════════════

def check_connectivity() -> bool:
    try:
        resp = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/sys_user?sysparm_limit=1",
            headers=HEADERS, auth=_auth(), timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"ServiceNow connected: {SNOW_INSTANCE}")
        return True
    except Exception as e:
        logger.error(f"ServiceNow connection failed: {e}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run(customers: list[dict] | None = None) -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 2 — ServiceNow CMDB Loader")
    logger.info("=" * 60)

    if not check_connectivity():
        return {"status": "error", "reason": "ServiceNow unreachable"}

    # Load Agent 1 data
    from neo4j_loader import find_latest_master, load_agent1_data
    agent1_data = load_agent1_data(AGENT1_DIR)

    results = {}

    # 1. Sync carriers
    results["carriers"] = load_carriers(agent1_data)

    # 2. Sync customers (if provided; seeded separately via seed_customers.py)
    if customers:
        results["customers"] = load_customers(customers)
    else:
        logger.info("No customers provided — skipping customer sync")
        results["customers"] = {"created": 0, "updated": 0, "failed": 0}

    logger.info("\n  ServiceNow CMDB sync complete")
    logger.info(f"  Carriers  — created: {results['carriers']['created']}, "
                f"updated: {results['carriers']['updated']}, "
                f"failed: {results['carriers']['failed']}")
    logger.info(f"  Customers — created: {results['customers']['created']}, "
                f"updated: {results['customers']['updated']}, "
                f"failed: {results['customers']['failed']}")

    return {"status": "ok", "results": results}


if __name__ == "__main__":
    result = run()
    print(f"\nCMDB loader done. Status: {result['status']}")
