"""
BindIQ Quick Start — Agent 2 with Existing KG Master Data

Usage:
  # Run with existing kg_master.json (no DataExtractor needed)
  python quick_start_agent2.py
  
  # Or run just the Maria demo
  python quick_start_agent2.py --demo maria
  
  # Load kg_master into Neo4j and run graph queries
  python quick_start_agent2.py --load-neo4j
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

try:
    from neo4j_loader import get_driver, create_schema, load_agent1_data
except ImportError:
    get_driver = None
    create_schema = None
    load_agent1_data = None

from config import AGENT1_DIR, LOG_DIR
from gap_analyzer import analyze_coverage_gaps, print_gap_analysis
from gap_analyzer import CoverageRequirement, CurrentPolicy
from maria_demo import run_maria_scenario

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "quick_start.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("quick_start")


# ═════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

def cmd_status():
    """Show what kg_master files are available."""
    logger.info("Checking for kg_master files...")
    
    files = sorted(AGENT1_DIR.glob("kg_master_*.json"), reverse=True)
    if not files:
        print("\n[NO KG_MASTER FILES FOUND]")
        print(f"   Expected location: {AGENT1_DIR}")
        print("\n   To generate kg_master, run Agent 1:")
        print("   $ cd DataExtractor")
        print("   $ python run_all.py")
        return False
    
    print(f"\n[FOUND {len(files)} KG_MASTER FILE(S)]:")
    for f in files[:5]:
        size_kb = f.stat().st_size / 1024
        mod_time = datetime.fromtimestamp(f.stat().st_mtime)
        print(f"   * {f.name} ({size_kb:.0f} KB, {mod_time:%Y-%m-%d %H:%M})")
    
    return True


def cmd_inspect():
    """Inspect the kg_master data structure."""
    logger.info("Loading kg_master data...")
    
    if not load_agent1_data:
        print("\n[NOTE] neo4j module not installed, but gap analyzer works fine!")
    
    try:
        # Load directly if neo4j_loader not available
        files = sorted(AGENT1_DIR.glob("kg_master_*.json"), reverse=True)
        if not files:
            print(f"\n[ERROR] No kg_master_*.json files found!")
            return False
        
        data = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load: {e}")
        return False
    
    print(f"\n[KG_MASTER DATA STRUCTURE]")
    
    if "tables" in data:
        tables = data["tables"]
        print(f"   Tables found: {list(tables.keys())}")
        
        for table_name, table_info in tables.items():
            if isinstance(table_info, dict) and "data" in table_info:
                num = len(table_info["data"])
                print(f"\n   * {table_name}: {num} records")
                if num > 0:
                    # Show first record structure
                    first = table_info["data"][0]
                    print(f"     Keys: {list(first.keys())}")
                    if num > 1:
                        print(f"     Sample: {first}")
            else:
                print(f"\n   * {table_name}: {table_info}")
    else:
        print(f"   Raw data keys: {list(data.keys())}")
        
        for table_name, table_data in data.items():
            if isinstance(table_data, list):
                num = len(table_data)
                print(f"\n   * {table_name}: {num} records")
                if num > 0:
                    # Show first record structure
                    first = table_data[0]
                    print(f"     Keys: {list(first.keys())}")
                    if num > 1:
                        print(f"     Sample: {first}")
    
    return True


def cmd_load_neo4j():
    """Load kg_master into Neo4j."""
    logger.info("Loading kg_master into Neo4j...")
    
    if not get_driver:
        print("\n❌ neo4j module not installed.")
        print("   Install with: pip install neo4j")
        return False
    
    try:
        driver = get_driver()
        create_schema(driver)
        
        # Load directly if load_agent1_data not available
        files = sorted(AGENT1_DIR.glob("kg_master_*.json"), reverse=True)
        if not files:
            print(f"\n❌ No kg_master_*.json files found!")
            return False
        
        data = json.loads(files[0].read_text(encoding="utf-8"))
        
        # Use the existing neo4j_loader to load tables
        from neo4j_loader import (
            load_carriers,
            load_industries,
            load_states,
            load_specializes_in,
            load_licensed_in,
            load_operates_in,
            load_insured_by,
        )
        
        with driver.session() as session:
            load_carriers(session, data.get("kg_table_1_carrier_identity", []))
            load_industries(session, data.get("kg_table_4_appetite", []))
            load_states(session, data.get("kg_table_5_state_presence", []))
            load_specializes_in(session, data.get("kg_table_4_appetite", []))
            load_licensed_in(session, data.get("kg_table_5_state_presence", []))
            load_operates_in(session, data.get("kg_table_2_customer_profile", []))
            load_insured_by(session, data.get("kg_table_3_coverage_profile", []))
        
        logger.info("✅ Successfully loaded kg_master into Neo4j")
        driver.close()
        return True
    
    except Exception as e:
        logger.error(f"Failed to load into Neo4j: {e}", exc_info=True)
        return False


def cmd_demo_maria():
    """Run the Maria demo scenario."""
    logger.info("Running Maria's Whole Foods scenario...")
    try:
        run_maria_scenario()
        return True
    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        return False


def cmd_list_carriers():
    """List all available carriers and their capabilities."""
    from carrier_capabilities import CARRIERS
    
    print(f"\n[AVAILABLE CARRIERS] ({len(CARRIERS)} total):\n")
    print(f"{'Carrier':<20} {'Rating':<15} {'Auto Quote':<15} {'Quote SLA':<12}")
    print("-" * 62)
    
    for carrier_id, cap in sorted(CARRIERS.items()):
        auto_quote = f"${cap.auto_quote_limit/1e6:.1f}M"
        quote_sla = f"{cap.auto_quote_hours:.1f}h"
        print(f"{cap.name:<20} {cap.am_best_rating:<15} {auto_quote:<15} {quote_sla:<12}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="BindIQ Quick Start — Agent 2 with Existing KG Data"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "inspect", "load-neo4j", "demo", "carriers"],
        help="Command to run",
    )
    parser.add_argument(
        "demo_name",
        nargs="?",
        default="maria",
        help="Demo name (e.g., 'maria')",
    )
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print(f"BindIQ Agent 2 — Quick Start")
    print("="*80)
    
    success = False
    
    if args.command == "status":
        success = cmd_status()
    
    elif args.command == "inspect":
        success = cmd_status() and cmd_inspect()
    
    elif args.command == "load-neo4j":
        success = cmd_status() and cmd_load_neo4j()
    
    elif args.command == "demo":
        if "maria" in args.demo_name.lower():
            success = cmd_demo_maria()
        else:
            print(f"Unknown demo: {args.demo_name}")
            print("Available: maria")
            success = False
    
    elif args.command == "carriers":
        cmd_list_carriers()
        success = True
    
    print("\n" + "="*80)
    if success:
        print("✅ Command completed successfully")
    else:
        print("❌ Command failed")
    print("="*80 + "\n")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
