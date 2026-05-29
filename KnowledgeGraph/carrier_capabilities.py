"""
BindIQ Agent 2 — Carrier Capabilities
Practical underwriting rules, endorsement capabilities, and SLAs for each carrier.
This is the knowledge base that makes gap analysis and rating possible.
"""

from dataclasses import dataclass
from typing import Dict, List, Set

# ═════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class GLLimit:
    """General Liability coverage limit."""
    per_occurrence: int
    aggregate: int
    description: str = ""

@dataclass
class Endorsement:
    """A specific endorsement this carrier can add."""
    code: str
    name: str
    cost_min: int  # dollars
    cost_max: int
    processing_hours: int  # SLA in hours
    requires_underwriter: bool = False
    applies_to_industries: List[str] = None  # empty = applies to all

@dataclass
class CarrierCapability:
    """Complete underwriting profile for one carrier."""
    carrier_id: str
    name: str
    am_best_rating: str
    
    # Underwriting appetites by tier
    auto_quote_limit: int  # max premium for instant quote
    auto_quote_hours: int  # SLA for auto quote response
    manual_quote_hours: int  # SLA for underwriter review
    processing_hours: int  # SLA for full binding
    
    # GL offerings
    gl_limits: List[GLLimit]
    default_gl_limit: GLLimit
    
    # Endorsements offered
    endorsements: Dict[str, Endorsement]  # keyed by code
    
    # Eligibility rules
    min_years_in_business: int
    min_annual_revenue: int
    
    # Industries they write
    focus_industries: Set[str]
    supported_industries: Set[str]  # superset of focus
    excluded_industries: Set[str]
    
    # States they're licensed
    licensed_states: Set[str]
    
    # Fields with defaults
    max_annual_revenue: int = None  # None = unlimited
    unlicensed_states: Set[str] = None  # None = all others
    supports_cg_2015: bool = True
    supports_cg_2010: bool = False
    supports_pnc: bool = True
    supports_wo_managed_care: bool = False
    allows_online_binding: bool = False
    requires_paper_signature: bool = True


# ═════════════════════════════════════════════════════════════════════════════
# CARRIER PROFILES
# ═════════════════════════════════════════════════════════════════════════════

CARRIERS: Dict[str, CarrierCapability] = {
    # ─────────────────────────────────────────────────────────────────────────
    # TIER 1: Premier carriers with wide appetite
    # ─────────────────────────────────────────────────────────────────────────
    
    "chubb": CarrierCapability(
        carrier_id="chubb",
        name="Chubb Group",
        am_best_rating="A+ (Superior)",
        
        # Underwriting SLAs
        auto_quote_limit=5_000_000,  # Very high auto quote limit
        auto_quote_hours=1,  # 1-hour turnaround
        manual_quote_hours=24,
        processing_hours=48,
        
        # GL offerings
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "Standard 1M/2M"),
            GLLimit(2_000_000, 4_000_000, "2M/4M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        # Endorsements
        endorsements={
            "DAMAGE_TO_RENTED": Endorsement(
                "CDE", "Damage to Rented Premises", 75, 200, 0, False,
                ["food_service", "cleaning", "landscaping"]
            ),
            "LIQUOR_LIABILITY": Endorsement(
                "LIQ", "Liquor Liability", 300, 1000, 0, True,
                ["food_service", "bars_restaurants"]
            ),
            "FOOD_CONTAMINATION": Endorsement(
                "FOOD", "Food Contamination Coverage", 500, 2000, 24, True,
                ["food_service"]
            ),
        },
        
        # Eligibility
        min_years_in_business=2,
        min_annual_revenue=250_000,
        
        # Industries
        focus_industries={"food_service", "restaurants", "catering", "cleaning"},
        supported_industries={
            "food_service", "restaurants", "catering", "bars_lounges",
            "cleaning", "landscaping", "construction", "transportation"
        },
        excluded_industries={"cannabis", "explosives", "mining"},
        
        # States
        licensed_states={
            "CA", "FL", "TX", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "TN", "MA", "IN", "WA", "UT", "CO", "VA", "MO", "MN",
            "NJ", "MD", "WI", "LA", "SC", "NV", "AR", "OR", "OK", "DEF"
        },
    ),
    
    "hartford": CarrierCapability(
        carrier_id="hartford",
        name="The Hartford",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=3_000_000,
        auto_quote_hours=2,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
            GLLimit(500_000, 1_000_000, "500K/1M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={
            "DAMAGED_RENTED": Endorsement(
                "DRP", "Damage to Rented Property", 100, 250, 4, False
            ),
            "FOOD_LIABILITY": Endorsement(
                "FOOD", "Food Liability", 400, 1500, 24, True,
                ["food_service", "restaurants"]
            ),
        },
        
        min_years_in_business=1,
        min_annual_revenue=150_000,
        
        focus_industries={"food_service", "cleaning", "landscaping"},
        supported_industries={
            "food_service", "restaurants", "catering", "cleaning",
            "landscaping", "construction", "retail", "services"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "FL", "TX", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "CO", "VA", "MA", "IN", "WA", "NJ", "MD", "MO", "MN"
        },
    ),
    
    "travelers": CarrierCapability(
        carrier_id="travelers",
        name="Travelers",
        am_best_rating="A+ (Superior)",
        
        auto_quote_limit=4_000_000,
        auto_quote_hours=2,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
            GLLimit(2_000_000, 4_000_000, "2M/4M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={
            "DAMAGE_RENTED": Endorsement(
                "DRNT", "Damage to Rented Property", 100, 300, 4, False
            ),
        },
        
        min_years_in_business=2,
        min_annual_revenue=300_000,
        
        focus_industries={"food_service", "restaurants", "cleaning"},
        supported_industries={
            "food_service", "restaurants", "catering", "cleaning",
            "landscaping", "construction", "services", "retail"
        },
        excluded_industries={"cannabis", "marijuana", "mining"},
        
        licensed_states={
            "CA", "FL", "TX", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "TN", "VA", "WA", "CO", "MA", "IN", "NJ", "MD", "MO"
        },
    ),
    
    # ─────────────────────────────────────────────────────────────────────────
    # TIER 2: Strong carriers with good appetite for food service
    # ─────────────────────────────────────────────────────────────────────────
    
    "progressive": CarrierCapability(
        carrier_id="progressive",
        name="Progressive Insurance",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=1_500_000,
        auto_quote_hours=4,
        manual_quote_hours=48,
        processing_hours=72,
        
        gl_limits=[
            GLLimit(500_000, 1_000_000, "500K/1M"),
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={
            "FOOD_PRODUCTS": Endorsement(
                "FOOD", "Food Products Liability", 300, 1200, 48, True,
                ["food_service"]
            ),
        },
        
        min_years_in_business=1,
        min_annual_revenue=200_000,
        
        focus_industries={"food_service", "restaurants"},
        supported_industries={
            "food_service", "restaurants", "catering", "cleaning",
            "landscaping", "retail", "services"
        },
        excluded_industries={"cannabis", "mining", "explosive"},
        
        licensed_states={
            "CA", "FL", "TX", "NY", "IL", "PA", "OH", "MI", "NC", "GA",
            "AZ", "VA", "WA", "CO", "MO", "TN", "IN", "NJ", "MA"
        },
    ),
    
    "nationwide": CarrierCapability(
        carrier_id="nationwide",
        name="Nationwide",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=1_000_000,
        auto_quote_hours=6,
        manual_quote_hours=48,
        processing_hours=72,
        
        gl_limits=[
            GLLimit(500_000, 1_000_000, "500K/1M"),
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={
            "FOOD_PREP": Endorsement(
                "FP", "Food Prep Liability", 250, 1000, 48, True,
                ["food_service", "restaurants"]
            ),
        },
        
        min_years_in_business=1,
        min_annual_revenue=150_000,
        
        focus_industries={"food_service", "cleaning"},
        supported_industries={
            "food_service", "restaurants", "bars_lounges", "cleaning",
            "landscaping", "retail", "services"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "FL", "TX", "NY", "IL", "PA", "OH", "MI", "NC", "GA",
            "AZ", "VA", "CO", "WA", "MA", "MO", "IN", "NJ", "TN"
        },
    ),
    
    "hiscox": CarrierCapability(
        carrier_id="hiscox",
        name="Hiscox Ventures",
        am_best_rating="B+ (Good)",
        
        auto_quote_limit=500_000,
        auto_quote_hours=8,
        manual_quote_hours=72,
        processing_hours=120,
        
        gl_limits=[
            GLLimit(500_000, 1_000_000, "500K/1M"),
        ],
        default_gl_limit=GLLimit(500_000, 1_000_000),
        
        endorsements={
            "FOOD_SERVICE_ADD": Endorsement(
                "FSA", "Food Service Addition", 200, 800, 72, True,
                ["food_service"]
            ),
        },
        
        min_years_in_business=1,
        min_annual_revenue=100_000,
        
        focus_industries={"food_service", "restaurants", "small_services"},
        supported_industries={
            "food_service", "restaurants", "catering", "cleaning",
            "landscaping_small", "retail", "small_services"
        },
        excluded_industries={"cannabis", "mining", "large_operations"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI"
        },
    ),
    
    # ─────────────────────────────────────────────────────────────────────────
    # TIER 3: Niche/Specialty carriers
    # ─────────────────────────────────────────────────────────────────────────
    
    "liberty_mutual": CarrierCapability(
        carrier_id="liberty_mutual",
        name="Liberty Mutual",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=2_000_000,
        auto_quote_hours=2,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={},
        
        min_years_in_business=2,
        min_annual_revenue=250_000,
        
        focus_industries={"construction", "transportation"},
        supported_industries={
            "construction", "transportation", "food_service",
            "cleaning", "services"
        },
        excluded_industries={"cannabis", "mining", "explosives"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "VA", "WA", "CO", "MA", "MO", "AZ", "NJ", "TN", "MD"
        },
    ),
    
    "markel": CarrierCapability(
        carrier_id="markel",
        name="Markel",
        am_best_rating="A- (Good)",
        
        auto_quote_limit=1_000_000,
        auto_quote_hours=12,
        manual_quote_hours=48,
        processing_hours=96,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
            GLLimit(500_000, 1_000_000, "500K/1M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={},
        
        min_years_in_business=2,
        min_annual_revenue=200_000,
        
        focus_industries={"food_service", "artisan_food"},
        supported_industries={
            "food_service", "restaurants", "catering", "artisan_food",
            "specialty_foods", "cleaning"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA", "NC", "MI"
        },
    ),
    
    "cna": CarrierCapability(
        carrier_id="cna",
        name="CNA",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=2_000_000,
        auto_quote_hours=4,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={},
        
        min_years_in_business=2,
        min_annual_revenue=300_000,
        
        focus_industries={"food_service", "restaurants", "commercial"},
        supported_industries={
            "food_service", "restaurants", "catering", "commercial",
            "cleaning", "construction"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI",
            "VA", "WA", "CO", "MA", "NJ"
        },
    ),
    
    "next": CarrierCapability(
        carrier_id="next",
        name="Next Insurance",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=2_000_000,
        auto_quote_hours=0.25,  # 15 minutes (!!)
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
            GLLimit(2_000_000, 4_000_000, "2M/4M"),  # available for revenue > $1M
        ],
        default_gl_limit=GLLimit(2_000_000, 4_000_000),

        endorsements={
            "DAMAGE_RENTED_PREM": Endorsement(
                "DRP", "Damage to Rented Premises", 50, 150, 4, False
            ),
            "CG_2015": Endorsement(
                "CG2015", "Additional Insured - Vendors (CG 20 15)", 75, 150, 2, False,
                ["food_service", "retail", "cleaning", "landscaping"]
            ),
        },

        min_years_in_business=1,
        min_annual_revenue=25_000,

        focus_industries={"food_service", "cleaning", "landscaping"},
        supported_industries={
            "food_service", "restaurants", "bars_lounges", "cleaning",
            "landscaping", "retail", "services", "small_general"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "VA", "WA", "CO", "TN", "MA", "IN", "NJ", "MO"
        },
        
        allows_online_binding=True,
        requires_paper_signature=False,
    ),
    
    "simply_business": CarrierCapability(
        carrier_id="simply_business",
        name="Simply Business",
        am_best_rating="A (Excellent)",
        
        auto_quote_limit=500_000,
        auto_quote_hours=1,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(500_000, 1_000_000, "500K/1M"),
        ],
        default_gl_limit=GLLimit(500_000, 1_000_000),
        
        endorsements={},
        
        min_years_in_business=0,  # No minimum!
        min_annual_revenue=10_000,
        
        focus_industries={"food_service", "small_business"},
        supported_industries={
            "food_service", "restaurants", "cleaning", "retail",
            "services", "small_business", "startups"
        },
        excluded_industries={"cannabis", "mining", "high_risk"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "VA", "CO", "WA", "MA", "MO"
        },
        
        allows_online_binding=True,
        requires_paper_signature=False,
    ),
    
    "zurich": CarrierCapability(
        carrier_id="zurich",
        name="Zurich",
        am_best_rating="A+ (Superior)",
        
        auto_quote_limit=3_000_000,
        auto_quote_hours=2,
        manual_quote_hours=24,
        processing_hours=48,
        
        gl_limits=[
            GLLimit(1_000_000, 2_000_000, "1M/2M"),
            GLLimit(2_000_000, 4_000_000, "2M/4M"),
        ],
        default_gl_limit=GLLimit(1_000_000, 2_000_000),
        
        endorsements={},
        
        min_years_in_business=2,
        min_annual_revenue=250_000,
        
        focus_industries={"food_service", "restaurants", "construction"},
        supported_industries={
            "food_service", "restaurants", "catering", "construction",
            "cleaning", "landscaping", "commercial", "services"
        },
        excluded_industries={"cannabis", "mining"},
        
        licensed_states={
            "CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "MI", "NC",
            "AZ", "VA", "WA", "CO", "MA", "MO", "NJ", "IN", "TN"
        },
    ),
}

# ═════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def get_carrier(carrier_id: str) -> CarrierCapability | None:
    """Get carrier profile by ID."""
    return CARRIERS.get(carrier_id.lower())

def can_write_in_state(carrier: CarrierCapability, state_code: str) -> bool:
    """Check if carrier is licensed in state."""
    return state_code.upper() in carrier.licensed_states

def can_quote_at_revenue(carrier: CarrierCapability, annual_revenue: int) -> bool:
    """Check if carrier's revenue range covers this business."""
    if annual_revenue < carrier.min_annual_revenue:
        return False
    if carrier.max_annual_revenue and annual_revenue > carrier.max_annual_revenue:
        return False
    return True

def get_coverage_sla_hours(
    carrier: CarrierCapability,
    estimated_premium: int,
    needs_underwriter: bool = False,
) -> int:
    """Get SLA in hours for a specific quote/binding."""
    if estimated_premium <= carrier.auto_quote_limit and not needs_underwriter:
        return carrier.auto_quote_hours
    if needs_underwriter:
        return carrier.manual_quote_hours
    return carrier.processing_hours
