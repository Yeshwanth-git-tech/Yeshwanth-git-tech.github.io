"""
BindIQ Agent 2 — Maria's Coverage Gap Scenario (Demo)

Shows how the gap analyzer works with a real example:
Maria runs a catering business that just got a contract from Whole Foods.
Her current GL policy is insufficient. We need to find carriers that can help.
"""

import logging
from gap_analyzer import (
    analyze_coverage_gaps,
    print_gap_analysis,
    CoverageRequirement,
    CurrentPolicy,
)

logger = logging.getLogger("maria_demo")


# ═════════════════════════════════════════════════════════════════════════════
# MARIA'S PROFILE
# ═════════════════════════════════════════════════════════════════════════════

MARIA_PROFILE = {
    "customer_name": "Maria's Catering & Food Service, LLC",
    "customer_id": "cust_maria_001",
    "state": "CA",  # California
    "industry": "food_service",  # Food Service
    "annual_revenue": 750_000,  # $750K revenue
    "years_in_business": 3,
}

MARIA_CURRENT_POLICIES = [
    CurrentPolicy(
        policy_id="pol_maria_gl_001",
        carrier_id="next",  # She has Next Insurance
        coverage_type="GL",
        current_limit="500K/1M",  # Low GL limit
        current_endorsements=[],  # No special endorsements
        expiry_date="2026-06-15",
        notes="Basic policy from 3 years ago, no coverage enhancements",
    ),
]

WHOLE_FOODS_REQUIREMENTS = [
    CoverageRequirement(
        requirement_id="req_wf_001",
        requirement_type="GL",
        description="General Liability Coverage",
        required_limit="1M/2M",  # Whole Foods requires 1M/2M minimum
        required_endorsements=[
            "DAMAGE_TO_RENTED",  # CDE - for facility damage
            "LIQUOR_LIABILITY",  # For catering with alcohol service
            "FOOD_CONTAMINATION",  # For food safety incidents
        ],
        notes="Standard requirement for Whole Foods approved vendors",
    ),
    CoverageRequirement(
        requirement_id="req_wf_002",
        requirement_type="GB",
        description="General Business Liability",
        required_limit="500K/1M",
        required_endorsements=[],
        notes="Standard GL addon",
    ),
]


# ═════════════════════════════════════════════════════════════════════════════
# RUN DEMO
# ═════════════════════════════════════════════════════════════════════════════

def run_maria_scenario():
    """Analyze Maria's coverage gaps and find solutions."""
    
    print("\n" + "="*80)
    print("MARIA'S WHOLE FOODS CONTRACT SCENARIO")
    print("="*80)
    
    print(f"\n[CUSTOMER PROFILE]")
    print(f"   Name: {MARIA_PROFILE['customer_name']}")
    print(f"   State: {MARIA_PROFILE['state']}")
    print(f"   Industry: {MARIA_PROFILE['industry']}")
    print(f"   Revenue: ${MARIA_PROFILE['annual_revenue']:,}")
    print(f"   Years in Business: {MARIA_PROFILE['years_in_business']}")
    
    print(f"\n[CURRENT COVERAGE]")
    for policy in MARIA_CURRENT_POLICIES:
        print(f"   * {policy.carrier_id}: {policy.coverage_type} - {policy.current_limit}")
        if policy.current_endorsements:
            print(f"     Endorsements: {', '.join(policy.current_endorsements)}")
        else:
            print(f"     Endorsements: None")
    
    print(f"\n[WHOLE FOODS REQUIREMENTS]")
    for req in WHOLE_FOODS_REQUIREMENTS:
        print(f"   * {req.requirement_type}: {req.required_limit}")
        if req.required_endorsements:
            print(f"     Endorsements: {', '.join(req.required_endorsements)}")
    
    # Run gap analysis
    print(f"\n[ANALYZING GAPS...]")
    analysis = analyze_coverage_gaps(
        MARIA_PROFILE,
        MARIA_CURRENT_POLICIES,
        WHOLE_FOODS_REQUIREMENTS,
    )
    
    # Display results
    print_gap_analysis(analysis)
    
    # Recommendations
    print(f"\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)
    
    gaps = analysis["gaps"]
    gap_solutions = analysis["gap_solutions"]
    summary = analysis["summary"]
    
    if summary["critical_gaps"] > 0:
        print(f"\n[CRITICAL ACTION REQUIRED]")
        print(f"   Maria has {summary['critical_gaps']} critical gap(s) that must be fixed")
        print(f"   before signing the Whole Foods contract.")
    
    print(f"\n[ACTION PLAN]")
    
    # For each gap, recommend top carrier
    for i, gap in enumerate(gaps, 1):
        solutions = gap_solutions.get(gap.gap_id, [])
        if solutions:
            top_carrier = solutions[0]
            print(f"\n   {i}. {gap.description}")
            print(f"      → {top_carrier.carrier.name}")
            print(f"      → {top_carrier.recommendation}")
            print(f"      → Quote available in: {top_carrier.quote_speed_label}")
            
            if top_carrier.can_provide_endorsements:
                print(f"      → Will add: {', '.join(top_carrier.can_provide_endorsements)}")
            
            if top_carrier.cannot_provide_endorsements:
                print(f"      → Cannot add: {', '.join(top_carrier.cannot_provide_endorsements)}")
                print(f"         (Consider alternative or request waiver from WF)")
    
    # Top alternative if existing carrier can't solve
    print(f"\n[IF STAYING WITH EXISTING CARRIER ({MARIA_CURRENT_POLICIES[0].carrier_id})]:")
    existing_solutions = []
    for gap_id, solutions in gap_solutions.items():
        existing = [s for s in solutions if s.carrier.carrier_id == MARIA_CURRENT_POLICIES[0].carrier_id]
        if existing:
            existing_solutions.append((gap_id, existing[0]))
    
    if existing_solutions:
        print(f"   -> Possible, but would need to add endorsements")
        for gap_id, opt in existing_solutions:
            if opt.cannot_provide_endorsements:
                print(f"   -> {opt.carrier.name} cannot provide: {', '.join(opt.cannot_provide_endorsements)}")
    else:
        print(f"   -> Not recommended. Carrier cannot fulfill all requirements.")
    
    print(f"\n[TIMELINE]:")
    # Get min and max hours from all solutions
    all_hours = []
    for solutions in gap_solutions.values():
        if solutions:
            all_hours.append(solutions[0].quote_speed_hours)
    
    if all_hours:
        min_hours = min(all_hours)
        max_hours = max(all_hours)
        if min_hours < 1:
            print(f"   → Fastest: Quote in 15 minutes, binding in 1-2 days")
        else:
            print(f"   → Typical: Quote in {int(min_hours)}-{int(max_hours)} hours, binding in 1-2 days")
    else:
        print(f"   → See carrier recommendations above")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_maria_scenario()
