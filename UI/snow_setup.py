"""
BindIQ — ServiceNow CMDB Table Setup

PDIs (free developer instances) block table creation via sys_db_object REST API.
This module uses the ServiceNow Background Script endpoint instead, which works
on all admin accounts.

Tables created:
  u_bindiq_carriers   — carrier master data
  u_bindiq_customers  — customer profiles

Usage:
  python snow_setup.py --check    # check status
  python snow_setup.py            # create tables via background script
"""

import argparse
import os
import sys
import json
import textwrap
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, str(Path(__file__).parent.parent / "KnowledgeGraph"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "KnowledgeGraph" / ".env")

SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "https://dev252187.service-now.com")
SNOW_USER     = os.environ.get("SNOW_USER",     "admin")
SNOW_PASSWORD = os.environ.get("SNOW_PASSWORD",  "")
TIMEOUT       = 20

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def _auth():
    return HTTPBasicAuth(SNOW_USER, SNOW_PASSWORD)


# ── Connectivity ───────────────────────────────────────────────────────────────

def ping() -> bool:
    try:
        r = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/sys_user?sysparm_limit=1",
            headers=HEADERS, auth=_auth(), timeout=TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def table_exists(table_name: str) -> bool:
    try:
        r = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/{table_name}?sysparm_limit=1",
            headers=HEADERS, auth=_auth(), timeout=TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


# ── Background Script approach (works on PDIs) ─────────────────────────────────

_CREATE_TABLES_SCRIPT = textwrap.dedent("""
    // BindIQ CMDB Table Setup Script
    // Run via: System Definition > Scripts - Background

    var tables = [
        {name: 'u_bindiq_carriers',  label: 'BindIQ Carriers'},
        {name: 'u_bindiq_customers', label: 'BindIQ Customers'},
    ];

    tables.forEach(function(t) {
        var existing = new GlideRecord('sys_db_object');
        existing.addQuery('name', t.name);
        existing.query();
        if (existing.next()) {
            gs.print(t.name + ': already exists');
            return;
        }
        var tbl = new GlideRecord('sys_db_object');
        tbl.initialize();
        tbl.setValue('name',  t.name);
        tbl.setValue('label', t.label);
        tbl.setValue('super_class', gs.getProperty('com.glide.cms.default_super_class', 'cmdb_ci'));
        tbl.insert();
        gs.print(t.name + ': created');
    });
    gs.print('Done.');
""")


def run_background_script(script: str) -> tuple[bool, str]:
    """
    Execute a Glide script via the ServiceNow background script endpoint.
    Returns (success, output_text).
    """
    # ServiceNow background script endpoint
    url = f"{SNOW_INSTANCE}/sys.scripts.do"
    try:
        r = requests.post(
            url,
            data={"script": script, "runscript": "Run script"},
            auth=_auth(),
            timeout=30,
        )
        if r.status_code == 200 and "gs.print" in script:
            # Extract print output from HTML response
            import re
            matches = re.findall(r"<span[^>]*>([^<]+)</span>", r.text)
            output = "\n".join(m for m in matches if m.strip() and len(m) < 200)
            return True, output or "Script executed (check ServiceNow for output)"
        return r.status_code == 200, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def create_tables_via_script() -> dict:
    """Create BindIQ tables using the background script endpoint."""
    ok, output = run_background_script(_CREATE_TABLES_SCRIPT)
    return {"success": ok, "output": output}


# ── Field setup (after tables exist) ──────────────────────────────────────────

CARRIER_FIELDS = [
    ("u_carrier_id",      "Carrier ID",           "string",  80),
    ("u_name",            "Carrier Name",          "string",  255),
    ("u_am_best_rating",  "AM Best Rating",        "string",  20),
    ("u_carrier_type",    "Carrier Type",          "string",  50),
    ("u_founded_year",    "Founded Year",          "integer", 0),
    ("u_naic_code",       "NAIC Code",             "string",  20),
    ("u_strengths",       "Industry Strengths",    "string",  500),
    ("u_avg_monthly_gl",  "Avg Monthly GL Premium","decimal", 0),
    ("u_complaint_ratio", "Complaint Ratio",       "decimal", 0),
    ("u_binding_speed",   "Binding Speed Tier",    "string",  50),
    ("u_graph_node_id",   "Neo4j Node ID",         "string",  100),
    ("u_last_synced",     "Last Synced",           "glide_date_time", 0),
]

CUSTOMER_FIELDS = [
    ("u_customer_id",    "Customer ID",           "string",  80),
    ("u_business_name",  "Business Name",         "string",  255),
    ("u_industry_id",    "Industry",              "string",  80),
    ("u_state",          "State",                 "string",  5),
    ("u_annual_revenue", "Annual Revenue",        "decimal", 0),
    ("u_employee_count", "Employee Count",        "integer", 0),
    ("u_years_in_biz",   "Years in Business",     "integer", 0),
    ("u_description",    "Business Description",  "string",  2000),
    ("u_coverage_needs", "Coverage Needs",        "string",  500),
    ("u_urgency",        "Urgency",               "string",  50),
    ("u_graph_node_id",  "Neo4j Node ID",         "string",  100),
    ("u_risk_tier",      "Risk Tier",             "string",  30),
    ("u_last_synced",    "Last Synced",           "glide_date_time", 0),
]


def _add_fields_script(table: str, fields: list) -> str:
    """Generate a Glide script that adds fields to an existing table."""
    lines = [f"// Add fields to {table}"]
    for col_name, col_label, col_type, col_len in fields:
        lines.append(textwrap.dedent(f"""
        (function() {{
            var existing = new GlideRecord('sys_dictionary');
            existing.addQuery('name', '{table}');
            existing.addQuery('element', '{col_name}');
            existing.query();
            if (existing.next()) {{ return; }}
            var d = new GlideRecord('sys_dictionary');
            d.initialize();
            d.setValue('name', '{table}');
            d.setValue('element', '{col_name}');
            d.setValue('column_label', '{col_label}');
            d.setValue('internal_type', '{col_type}');
            {f"d.setValue('max_length', '{col_len}');" if col_len else ""}
            d.insert();
        }})();"""))
    return "\n".join(lines)


def setup_fields() -> dict:
    """Add custom fields to the carrier and customer tables."""
    results = {}
    for table, fields in [
        ("u_bindiq_carriers",  CARRIER_FIELDS),
        ("u_bindiq_customers", CUSTOMER_FIELDS),
    ]:
        script = _add_fields_script(table, fields)
        ok, output = run_background_script(script)
        results[table] = {"ok": ok, "output": output}
    return results


# ── Flow Designer trigger (quote request) ─────────────────────────────────────

def trigger_quote_flow(customer_id: str, carrier_id: str, gl_limit: int,
                       notes: str = "") -> dict:
    """
    Create a u_bindiq_policies record to trigger the ServiceNow Flow Designer.
    The flow listens for new records on this table with u_status=pending.
    Returns {success, sys_id, message}.
    """
    from datetime import datetime, timezone
    payload = {
        "u_customer_id":        customer_id,
        "u_carrier_id":         carrier_id,
        "u_gl_limit_requested": str(gl_limit),
        "u_status":             "pending",
        "u_notes":              notes or f"Quote requested by BindIQ at {datetime.now(timezone.utc).isoformat()}",
        "u_created_by":         "BindIQ Agent",
    }
    try:
        r = requests.post(
            f"{SNOW_INSTANCE}/api/now/table/u_bindiq_policies",
            headers=HEADERS, auth=_auth(),
            json=payload, timeout=TIMEOUT,
        )
        if r.status_code in (200, 201):
            sys_id = r.json().get("result", {}).get("sys_id", "")
            return {"success": True, "sys_id": sys_id,
                    "message": f"Quote request created (sys_id={sys_id})"}
        return {"success": False, "sys_id": "",
                "message": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"success": False, "sys_id": "", "message": str(e)}


# ── Email Monitor — ServiceNow Scheduled Script + Outbound REST ───────────────

_MONITOR_SCRIPT = """
// BindIQ Email Monitor — runs every 5 minutes
// Calls the BindIQ webhook to check warantheyanesh@gmail.com for vendor contracts
// Triggers: Scheduled Script Execution (sys_trigger table)
(function() {
    var webhookUrl = gs.getProperty('bindiq.webhook_url', 'http://localhost:5000');
    try {
        var rm = new sn_ws.RESTMessageV2();
        rm.setEndpoint(webhookUrl + '/api/check-inbox');
        rm.setHttpMethod('POST');
        rm.setRequestHeader('Content-Type', 'application/json');
        rm.setRequestHeader('X-BindIQ-Source', 'servicenow_scheduler');
        rm.setRequestBody(JSON.stringify({
            monitor_email: 'warantheyanesh@gmail.com',
            since_minutes: 6,
            source: 'snow_scheduled'
        }));
        rm.setHttpTimeout(20000);
        var resp = rm.execute();
        var status = resp.getStatusCode();
        if (status === 200) {
            var body = JSON.parse(resp.getBody());
            if (body.triggers && body.triggers.length > 0) {
                gs.log('[BindIQ] ' + body.triggers.length + ' trigger email(s) found — pipeline running', 'BindIQ');
            } else {
                gs.log('[BindIQ] No new trigger emails', 'BindIQ');
            }
        } else {
            gs.logWarning('[BindIQ] Webhook returned HTTP ' + status, 'BindIQ');
        }
    } catch(e) {
        gs.logError('[BindIQ] Email monitor failed: ' + e, 'BindIQ');
    }
})();
""".strip()


def create_scheduled_monitor(webhook_url: str = "") -> dict:
    """
    Create a ServiceNow Scheduled Script Execution that polls every 5 minutes.
    Also stores the webhook URL as a system property.
    Returns {success, sys_id, message}.
    """
    results = {}

    # 1. Store webhook URL as SN system property
    if webhook_url:
        prop_payload = {
            "name":        "bindiq.webhook_url",
            "value":       webhook_url,
            "description": "BindIQ webhook URL for email monitor",
            "type":        "string",
        }
        try:
            r = requests.post(
                f"{SNOW_INSTANCE}/api/now/table/sys_properties",
                headers=HEADERS, auth=_auth(),
                json=prop_payload, timeout=TIMEOUT,
            )
            results["property"] = {"ok": r.status_code in (200, 201), "status": r.status_code}
        except Exception as e:
            results["property"] = {"ok": False, "error": str(e)}

    # 2. Create Scheduled Script Execution (sysauto_script)
    schedule_payload = {
        "name":          "BindIQ Email Monitor",
        "script":        _MONITOR_SCRIPT,
        "run_type":      "periodically",
        "run_period":    "00:05:00",   # every 5 minutes
        "active":        "true",
        "description":   "Polls warantheyanesh@gmail.com every 5 min for vendor contract emails",
        "run_as":        "admin",
    }
    try:
        r = requests.post(
            f"{SNOW_INSTANCE}/api/now/table/sysauto_script",
            headers=HEADERS, auth=_auth(),
            json=schedule_payload, timeout=TIMEOUT,
        )
        if r.status_code in (200, 201):
            sys_id = r.json().get("result", {}).get("sys_id", "")
            results["scheduler"] = {
                "ok":     True,
                "sys_id": sys_id,
                "url":    f"{SNOW_INSTANCE}/sysauto_script.do?sys_id={sys_id}",
            }
        else:
            results["scheduler"] = {
                "ok":      False,
                "status":  r.status_code,
                "message": r.text[:200],
            }
    except Exception as e:
        results["scheduler"] = {"ok": False, "error": str(e)}

    success = results.get("scheduler", {}).get("ok", False)
    return {
        "success": success,
        "results": results,
        "message": (
            f"Scheduled script created: {results['scheduler'].get('sys_id', '')}"
            if success else
            f"Failed: {results.get('scheduler', {}).get('message', 'unknown error')}"
        ),
    }


def get_scheduled_monitor_status() -> dict:
    """Check if the BindIQ email monitor scheduled script exists and is active."""
    try:
        r = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/sysauto_script"
            f"?sysparm_query=name=BindIQ Email Monitor&sysparm_fields=sys_id,name,active,run_period",
            headers=HEADERS, auth=_auth(), timeout=TIMEOUT,
        )
        if r.status_code == 200:
            records = r.json().get("result", [])
            if records:
                rec = records[0]
                return {
                    "exists": True,
                    "active": rec.get("active") == "true",
                    "sys_id": rec.get("sys_id", ""),
                    "period": rec.get("run_period", ""),
                }
            return {"exists": False}
        return {"exists": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"exists": False, "error": str(e)}


FLOW_DESIGNER_INSTRUCTIONS = """
FLOW DESIGNER SETUP (one-time, ~5 minutes)
==========================================

1. Go to: {instance}/now/nav/ui/classic/params/target/sys_hub_flow_list.do
   (Flow Designer -> All Flows)

2. Click "New" -> "Flow"
   Name:        BindIQ Quote Request Handler
   Description: Triggered when a quote request is created in u_bindiq_policies

3. Add Trigger:
   Trigger type:  Record Created
   Table:         u_bindiq_policies
   Condition:     u_status = pending

4. Add Action 1 - Send Email (use "Send Email" action):
   To:      warantheyanesh@gmail.com
   Subject: BindIQ: Quote request received
   Body:    Customer {{u_customer_id}} requests GL ${{u_gl_limit_requested}} with {{u_carrier_id}}

5. Add Action 2 - Update Record:
   Table:  u_bindiq_policies
   Record: Trigger record
   Field:  u_status -> in_progress

6. Activate the flow -> Save.

Every "Request Quote" click in BindIQ creates a CMDB record that fires this flow.
""".strip()


# ── Manual setup instructions ──────────────────────────────────────────────────

MANUAL_SETUP_INSTRUCTIONS = """
MANUAL TABLE SETUP — PDI requires creating tables in the UI
===========================================================

Step 1: Open Tables list
  URL: {instance}/now/nav/ui/classic/params/target/sys_db_object_list.do

Step 2: Create Table 1 — Carriers
  Click New
    Name:    u_bindiq_carriers
    Label:   BindIQ Carriers
    Extends: Configuration Item [cmdb_ci]
  Click Submit

Step 3: Create Table 2 — Customers
  Click New
    Name:    u_bindiq_customers
    Label:   BindIQ Customers
    Extends: Configuration Item [cmdb_ci]
  Click Submit

Step 4: Create Table 3 — Policies
  Click New
    Name:    u_bindiq_policies
    Label:   BindIQ Policies
    Extends: Configuration Item [cmdb_ci]
  Click Submit

Step 5: Come back here and click "Sync Carriers + Customers".
  (ServiceNow REST API will auto-create missing fields on first write.)

Tip: If you see "Table Field Validation" errors, the Business Rule
     is blocking automated creation — manual setup is the only path on PDI.
""".strip()

# ── Background script for manual copy-paste into ServiceNow ──────────────────

BACKGROUND_SCRIPT = """// BindIQ — create 3 tables
// Paste into: System Definition > Scripts - Background > Run
var tables = [
    {name: 'u_bindiq_carriers',  label: 'BindIQ Carriers'},
    {name: 'u_bindiq_customers', label: 'BindIQ Customers'},
    {name: 'u_bindiq_policies',  label: 'BindIQ Policies'},
];
tables.forEach(function(t) {
    var gr = new GlideRecord('sys_db_object');
    gr.addQuery('name', t.name);
    gr.query();
    if (gr.next()) { gs.print(t.name + ': already exists'); return; }
    var tbl = new GlideRecord('sys_db_object');
    tbl.initialize();
    tbl.setValue('name',  t.name);
    tbl.setValue('label', t.label);
    tbl.insert();
    gs.print(t.name + ': created (sys_id=' + tbl.getUniqueValue() + ')');
});
gs.print('Done.');
""".strip()


# ── Status check ──────────────────────────────────────────────────────────────

def check_status() -> dict:
    reachable = ping()
    if not reachable:
        return {
            "reachable": False,
            "tables":    {},
            "message":   "Instance unreachable (may be hibernating)",
            "wake_url":  "https://developer.servicenow.com",
        }

    tables = {t: table_exists(t) for t in
              ["u_bindiq_carriers", "u_bindiq_customers", "u_bindiq_policies"]}

    return {
        "reachable":           True,
        "instance":            SNOW_INSTANCE,
        "tables":              tables,
        "all_ready":           all(tables.values()),
        "message":             "Connected — all tables ready" if all(tables.values())
                               else "Connected — tables missing",
        "manual_instructions": MANUAL_SETUP_INSTRUCTIONS.format(instance=SNOW_INSTANCE),
        "background_script":   BACKGROUND_SCRIPT,
        "flow_instructions":   FLOW_DESIGNER_INSTRUCTIONS.format(instance=SNOW_INSTANCE),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    print(f"ServiceNow: {SNOW_INSTANCE}")
    if not ping():
        print("ERROR: Not reachable. Wake at https://developer.servicenow.com")
        sys.exit(1)

    print("Connected!")

    if args.check:
        print(json.dumps(check_status(), indent=2))
    else:
        print("\nCreating tables via background script...")
        r = create_tables_via_script()
        print(f"  Result: {'OK' if r['success'] else 'FAILED'}")
        print(f"  Output: {r['output']}")

        if not r["success"]:
            print("\n" + MANUAL_SETUP_INSTRUCTIONS.format(instance=SNOW_INSTANCE))
