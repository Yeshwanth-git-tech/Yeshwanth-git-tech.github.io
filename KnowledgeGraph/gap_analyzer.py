"""
BindIQ Agent 2 — Gap Analyzer
Takes customer profile + contract requirements and identifies coverage gaps.
Returns ranked carriers that can actually fulfill those gaps.

Maria's Problem:
  Contract: Whole Foods General Liability coverage requirement
  Current Policy: Legacy policy from 5 years ago, may be missing endorsements
  Question: What carriers can write this coverage? How fast? What cost?
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Set

sys.path.insert(0, str(Path(__file__).parent))
from carrier_capabilities import (
    CarrierCapability,
    get_carrier,
    can_write_in_state,
    can_quote_at_revenue,
    get_coverage_sla_hours,
    CARRIERS,
)

logger = logging.getLogger("gap_analyzer")


# ═════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class CoverageRequirement:
    """One specific insurance requirement from a contract."""
    requirement_id: str
    requirement_type: str  # e.g., "GL", "GB", "PL"
    description: str
    required_limit: str  # e.g., "1M/2M"
    required_endorsements: List[str] = None  # e.g., ["food_liability", "hired_equip"]
    notes: str = ""

    def __post_init__(self):
        if self.required_endorsements is None:
            self.required_endorsements = []


@dataclass
class CurrentPolicy:
    """Customer's existing insurance coverage."""
    policy_id: str
    carrier_id: str
    coverage_type: str  # e.g., "GL", "PL", "GB"
    current_limit: str  # e.g., "1M/2M"
    current_endorsements: List[str] = None
    expiry_date: str = ""
    notes: str = ""

    def __post_init__(self):
        if self.current_endorsements is None:
            self.current_endorsements = []


@dataclass
class CoverageGap:
    """One identified gap between contract requirements and current coverage."""
    gap_id: str
    gap_type: str  # "missing_coverage", "insufficient_limit", "missing_endorsement"
    requirement: CoverageRequirement
    current_policy: Optional[CurrentPolicy]
    description: str
    severity: str  # "critical", "important", "minor"


@dataclass
class CarrierOption:
    """A carrier that can solve one or more gaps."""
    carrier: CarrierCapability
    can_fulfill_coverage: bool
    can_provide_endorsements: List[str]  # empty = none available
    cannot_provide_endorsements: List[str]
    
    quote_speed_hours: int  # SLA
    quote_speed_label: str  # "Instant", "4 hours", "24 hours", etc.
    
    eligibility_issues: Set[str] = None  # reasons why this might not work
    recommendation: str = ""  # brief explanation
    
    match_score: float = 0.0  # 0-100

    def __post_init__(self):
        if self.eligibility_issues is None:
            self.eligibility_issues = set()


# ═════════════════════════════════════════════════════════════════════════════
# GAP DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def detect_gaps(
    current_policies: List[CurrentPolicy],
    contract_requirements: List[CoverageRequirement],
) -> List[CoverageGap]:
    """
    Compare current coverage vs contract requirements.
    Return list of gaps to address.
    """
    gaps: List[CoverageGap] = []
    gap_counter = 0
    
    for req in contract_requirements:
        # Find current policy matching this requirement type
        current = None
        for policy in current_policies:
            if policy.coverage_type == req.requirement_type:
                current = policy
                break
        
        # Check for missing coverage entirely
        if not current:
            gap_counter += 1
            gaps.append(CoverageGap(
                gap_id=f"gap_{gap_counter}",
                gap_type="missing_coverage",
                requirement=req,
                current_policy=None,
                description=f"Missing {req.requirement_type} coverage (required: {req.description})",
                severity="critical",
            ))
            continue
        
        # Check for insufficient limits
        req_limit_val = _parse_limit(req.required_limit)
        curr_limit_val = _parse_limit(current.current_limit)
        
        if curr_limit_val and req_limit_val and curr_limit_val < req_limit_val:
            gap_counter += 1
            gaps.append(CoverageGap(
                gap_id=f"gap_{gap_counter}",
                gap_type="insufficient_limit",
                requirement=req,
                current_policy=current,
                description=f"Insufficient {req.requirement_type} limit: "
                           f"have {current.current_limit}, need {req.required_limit}",
                severity="important",
            ))
        
        # Check for missing endorsements
        missing_endorsements = [
            e for e in req.required_endorsements
            if e not in current.current_endorsements
        ]
        if missing_endorsements:
            gap_counter += 1
            gaps.append(CoverageGap(
                gap_id=f"gap_{gap_counter}",
                gap_type="missing_endorsement",
                requirement=req,
                current_policy=current,
                description=f"Missing required endorsements: {', '.join(missing_endorsements)}",
                severity="important",
            ))
    
    return gaps


def _parse_limit(limit_str: str) -> int:
    """Parse limit string like '1M/2M' to numeric value (use per-occurrence)."""
    if not limit_str:
        return 0
    try:
        # Extract first part (per-occurrence)
        part = limit_str.split('/')[0].strip()
        if 'M' in part:
            return int(part.replace('M', '')) * 1_000_000
        elif 'K' in part:
            return int(part.replace('K', '')) * 1_000
        else:
            return int(part)
    except:
        return 0


# ═════════════════════════════════════════════════════════════════════════════
# CARRIER MATCHING
# ═════════════════════════════════════════════════════════════════════════════

def find_carriers_for_gap(
    gap: CoverageGap,
    customer_state: str,
    customer_industry: str,
    customer_revenue: int,
    existing_carrier_id: Optional[str] = None,
) -> List[CarrierOption]:
    """
    Find all carriers that can solve a specific gap.
    Returns list ranked by match quality.
    """
    options: List[CarrierOption] = []
    
    for carrier_profile in CARRIERS.values():
        # Hard requirement: must be licensed in state
        if not can_write_in_state(carrier_profile, customer_state):
            continue
        
        # Hard requirement: must handle revenue range
        if not can_quote_at_revenue(carrier_profile, customer_revenue):
            continue
        
        # Hard requirement: must write in customer's industry
        if customer_industry.lower() not in carrier_profile.supported_industries:
            continue
        
        # Build option
        option = _build_carrier_option(
            carrier_profile, gap, customer_state,
            existing_carrier_id=existing_carrier_id
        )
        if option:
            options.append(option)
    
    # Rank by match score (best first)
    options.sort(key=lambda o: o.match_score, reverse=True)
    return options


def _build_carrier_option(
    carrier: CarrierCapability,
    gap: CoverageGap,
    customer_state: str,
    existing_carrier_id: Optional[str] = None,
) -> Optional[CarrierOption]:
    """Build a single CarrierOption for this carrier + gap combo."""
    
    eligibility_issues: Set[str] = set()
    
    # Check years in business requirement
    # (TODO: get from customer profile)
    
    # Check if carrier can provide the coverage
    can_fulfill = False
    if gap.requirement.requirement_type == "GL":
        can_fulfill = True  # Most carriers write GL
    elif gap.requirement.requirement_type == "GB":
        can_fulfill = True
    elif gap.requirement.requirement_type == "PL":
        can_fulfill = carrier.supports_pnc and "PL" in carrier.supports_pnc
    
    if not can_fulfill:
        return None
    
    # Which endorsements can this carrier provide?
    required_endorsed = gap.requirement.required_endorsements or []
    can_provide = []
    cannot_provide = []
    
    for endorsement_needed in required_endorsed:
        if endorsement_needed in carrier.endorsements:
            can_provide.append(endorsement_needed)
        else:
            cannot_provide.append(endorsement_needed)
    
    # Estimate quote speed (SLA)
    # Use average premium for determinism
    estimated_premium = 2_500  # typical small GL
    quote_speed_hours = get_coverage_sla_hours(
        carrier,
        estimated_premium,
        needs_underwriter=len(cannot_provide) > 0
    )
    
    quote_speed_label = _hours_to_label(quote_speed_hours)
    
    # Build recommendation text
    recommendation = ""
    if len(cannot_provide) == 0:
        recommendation = f"✓ Can provide all required coverage in {quote_speed_label}"
    elif len(cannot_provide) == 1:
        recommendation = (
            f"Can provide coverage, but missing 1 endorsement ({cannot_provide[0]}). "
            f"May need alternative."
        )
    else:
        recommendation = (
            f"Can provide base coverage with CG/GB, but missing "
            f"{len(cannot_provide)} endorsements. Consider alternative carriers."
        )
    
    # Calculate match score (0-100)
    match_score = _calculate_match_score(
        carrier,
        gap,
        can_provide,
        cannot_provide,
        quote_speed_hours,
        is_existing=carrier.carrier_id == existing_carrier_id,
    )
    
    return CarrierOption(
        carrier=carrier,
        can_fulfill_coverage=can_fulfill,
        can_provide_endorsements=can_provide,
        cannot_provide_endorsements=cannot_provide,
        quote_speed_hours=quote_speed_hours,
        quote_speed_label=quote_speed_label,
        eligibility_issues=eligibility_issues,
        recommendation=recommendation,
        match_score=match_score,
    )


def _hours_to_label(hours: float) -> str:
    """Convert SLA hours to user-friendly label."""
    if hours < 0.5:
        return "15 minutes"
    elif hours < 1:
        return "30 minutes"
    elif hours < 4:
        return f"{int(hours)} hour{'s' if hours > 1 else ''}"
    elif hours < 24:
        return f"{int(hours)} hour{'s' if hours > 1 else ''}"
    elif hours < 72:
        return f"{int(hours // 24)} day{'s' if hours > 24 else ''}"
    else:
        return f"{int(hours // 24)} days"


def _calculate_match_score(
    carrier: CarrierCapability,
    gap: CoverageGap,
    can_provide: List[str],
    cannot_provide: List[str],
    quote_speed_hours: float,
    is_existing: bool = False,
) -> float:
    """
    Calculate match score 0-100 based on:
    - Can fulfill all requirements (base 50)
    - Endorsements available (+20 per all)
    - Speed (+15 for instant, +10 for <24h, +5 for <72h)
    - Existing carrier bonus (+10)
    - AM Best rating (+5 for A+, +3 for A, +0 for B+)
    """
    score = 50.0
    
    # Missing coverage = disqualify
    if not (gap.gap_type == "insufficient_limit" or gap.gap_type == "missing_endorsement"):
        return 0.0
    
    # Endorsements
    total_required = len(can_provide) + len(cannot_provide)
    if total_required > 0:
        coverage_pct = len(can_provide) / total_required
        score += 20 * coverage_pct
    else:
        score += 20  # No special endorsements needed
    
    # Speed
    if quote_speed_hours <= 0.25:
        score += 15
    elif quote_speed_hours <= 4:
        score += 10
    elif quote_speed_hours <= 24:
        score += 8
    elif quote_speed_hours <= 72:
        score += 5
    else:
        score += 2
    
    # Existing carrier bonus (lower switching cost)
    if is_existing:
        score += 10
    
    # AM Best rating
    if carrier.am_best_rating and "A+" in carrier.am_best_rating:
        score += 5
    elif carrier.am_best_rating and carrier.am_best_rating.startswith("A"):
        score += 3
    
    return min(100.0, score)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def analyze_coverage_gaps(
    customer_profile: Dict,
    current_policies: List[CurrentPolicy],
    contract_requirements: List[CoverageRequirement],
) -> Dict:
    """
    Complete gap analysis for a customer.
    
    Args:
        customer_profile: dict with keys: state, industry, annual_revenue, customer_name
        current_policies: list of CurrentPolicy objects
        contract_requirements: list of CoverageRequirement objects
    
    Returns:
        dict with keys:
        - gaps: list of CoverageGap objects
        - gap_solutions: dict mapping gap_id -> list of CarrierOption objects
        - summary: dict with analysis summary
    """
    # Detect gaps
    gaps = detect_gaps(current_policies, contract_requirements)
    
    # For each gap, find carrier solutions
    gap_solutions = {}
    for gap in gaps:
        solutions = find_carriers_for_gap(
            gap,
            customer_profile.get("state", "CA"),
            customer_profile.get("industry", "food_service"),
            customer_profile.get("annual_revenue", 500_000),
            existing_carrier_id=None,  # TODO: get from customer profile
        )
        gap_solutions[gap.gap_id] = solutions
    
    # Summary
    summary = {
        "total_gaps": len(gaps),
        "critical_gaps": sum(1 for g in gaps if g.severity == "critical"),
        "important_gaps": sum(1 for g in gaps if g.severity == "important"),
        "carriers_recommended": len(set(
            opt.carrier.carrier_id
            for solutions in gap_solutions.values()
            for opt in solutions[:1]  # Top 1 per gap
        )),
    }
    
    return {
        "gaps": gaps,
        "gap_solutions": gap_solutions,
        "summary": summary,
    }


def print_gap_analysis(analysis: Dict):
    """Pretty print the gap analysis results."""
    gaps = analysis["gaps"]
    gap_solutions = analysis["gap_solutions"]
    summary = analysis["summary"]
    
    print("\n" + "="*80)
    print("COVERAGE GAP ANALYSIS")
    print("="*80)
    
    print(f"\nSUMMARY: {summary['total_gaps']} gaps found")
    print(f"  - {summary['critical_gaps']} CRITICAL")
    print(f"  - {summary['important_gaps']} Important")
    
    for gap in gaps:
        print(f"\n[GAP: {gap.gap_id.upper()}] {gap.description}")
        print(f"   Type: {gap.gap_type} | Severity: {gap.severity.upper()}")
        
        if gap.current_policy:
            print(f"   Current: {gap.current_policy.carrier_id} - {gap.current_policy.current_limit}")
        
        solutions = gap_solutions.get(gap.gap_id, [])
        if solutions:
            print(f"\n   TOP CARRIER SOLUTIONS:")
            for i, opt in enumerate(solutions[:3], 1):
                print(f"\n     {i}. {opt.carrier.name}")
                print(f"        Coverage: {opt.recommendation}")
                print(f"        Speed: {opt.quote_speed_label}")
                if opt.can_provide_endorsements:
                    print(f"        Endorsements: {', '.join(opt.can_provide_endorsements)}")
                if opt.cannot_provide_endorsements:
                    print(f"        Missing: {', '.join(opt.cannot_provide_endorsements)}")
                print(f"        Score: {opt.match_score:.0f}/100")
        else:
            print(f"   WARNING: No carriers found that can solve this gap.")
