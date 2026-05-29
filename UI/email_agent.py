"""
BindIQ — Email Intelligence Agent
3-stage pipeline to detect and parse insurance requirements from emails.

Stage 1: Embedding similarity filter (sentence-transformers, no API cost)
          - Embed incoming email against a reference corpus
          - Positive: "certificate of insurance required for vendors"
          - Negative: "your order has shipped"
          - If similarity > threshold -> proceed to Stage 2

Stage 2: Claude LLM classification (reasoning layer)
          - "Does this email require insurance action? Why?"
          - Returns: yes/no + confidence + brief reasoning
          - Only fires when Stage 1 passes (saves API calls)

Stage 3: Claude structured extraction (if Stage 2 = yes)
          - Extract GL limit, endorsements, additional insured, deadline, etc.
          - Returns machine-readable dict for the gap analyzer

This approach handles novel contract formats that keyword matching would miss,
because the embedding model understands semantic meaning, not just word patterns.
"""

import os
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("email_agent")

# ── Reference corpus (used for Stage 1 embedding similarity) ──────────────────
# These anchor the embedding space — "what insurance requirement emails look like"

POSITIVE_CORPUS = [
    "Certificate of insurance required before you can begin work",
    "Please upload your COI to our vendor portal for approval",
    "General liability coverage of $2 million per occurrence required",
    "Additional insured endorsement CG 20 15 must be included on the policy",
    "Your insurance carrier must be rated AM Best A- or better",
    "Proof of commercial general liability insurance needed for vendor registration",
    "Insurance requirements: $1M per occurrence, Whole Foods as additional insured",
    "You must provide a certificate of insurance naming us as additional insured",
    "Policy must include primary and non-contributory language",
    "Please have your broker send us an updated certificate of insurance",
    "Vendor agreement requires commercial general liability with $2M limits",
    "Upload insurance documentation to EXIGIS before first delivery",
    "Congratulations on your vendor contract, insurance verification required",
    "Your broker should list our company as an additional insured party",
    "We require 30 days written notice of policy cancellation",
]

NEGATIVE_CORPUS = [
    "Your order has been shipped and will arrive in 3-5 business days",
    "Meeting invitation for quarterly business review tomorrow at 2pm",
    "Invoice attached for services rendered in January",
    "Password reset confirmation for your account",
    "Thank you for your purchase, here is your receipt",
    "Newsletter: new products and special offers this month",
    "Delivery confirmation: your package has been delivered",
    "Reminder: your subscription renews next week",
    "Hi, just following up on our conversation from yesterday",
    "Your application has been received and is under review",
]


@dataclass
class AgentResult:
    """Full result from the email intelligence agent."""

    # Stage 1 results
    embedding_similarity: float = 0.0
    stage1_passed: bool = False

    # Stage 2 results
    is_insurance_requirement: bool = False
    classification_confidence: float = 0.0
    classification_reasoning: str = ""
    stage2_passed: bool = False

    # Stage 3 results (only if stage2 passed)
    extracted: dict = field(default_factory=dict)
    extraction_source: str = ""  # "claude" | "regex" | "static"

    # Stage 4 results — business context triples for KG semantic scoring
    # Keys: industry, business_type, business_keywords, coverage_types, state
    business_context: dict = field(default_factory=dict)

    # Overall
    overall_confidence: float = 0.0
    signals: list = field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1 — EMBEDDING SIMILARITY FILTER
# ═════════════════════════════════════════════════════════════════════════════

_model = None
_pos_vecs = None
_neg_vecs = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformer model...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _get_reference_vecs():
    global _pos_vecs, _neg_vecs
    if _pos_vecs is None:
        model = _get_model()
        _pos_vecs = model.encode(POSITIVE_CORPUS, normalize_embeddings=True)
        _neg_vecs = model.encode(NEGATIVE_CORPUS, normalize_embeddings=True)
    return _pos_vecs, _neg_vecs


def stage1_embedding_filter(email_text: str, threshold: float = 0.42) -> tuple[float, bool]:
    """
    Embed the email and compare against the reference corpus.

    Returns (similarity_score, passed).
    Score = max similarity to any positive example - 0.5 * max similarity to any negative.
    """
    try:
        model = _get_model()
        pos_vecs, neg_vecs = _get_reference_vecs()

        email_vec = model.encode([email_text[:1000]], normalize_embeddings=True)[0]

        pos_sims = np.dot(pos_vecs, email_vec)   # cosine (vecs are normalised)
        neg_sims = np.dot(neg_vecs, email_vec)

        max_pos = float(pos_sims.max())
        max_neg = float(neg_sims.max())
        top3_pos = float(np.sort(pos_sims)[-3:].mean())

        # Score = average of top-3 positive matches, penalised by negative
        score = top3_pos - (max_neg * 0.3)
        score = max(0.0, min(1.0, score))

        passed = score >= threshold
        logger.debug(f"Stage 1: score={score:.3f} (pos={max_pos:.3f} neg={max_neg:.3f}) passed={passed}")
        return round(score, 3), passed

    except Exception as e:
        logger.warning(f"Stage 1 embedding failed ({e}), defaulting to pass")
        return 0.5, True     # fail-open: let later stages decide


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — LLM CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════

def stage2_llm_classify(email_text: str) -> tuple[bool, float, str]:
    """
    Ask Claude: "Does this email require insurance action?"
    Returns (is_insurance_req, confidence_0_to_1, reasoning).
    Falls back to heuristic if no API key.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _heuristic_classify(email_text)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an insurance broker's assistant. Analyze this email and determine if it contains an insurance requirement that needs action.

EMAIL:
{email_text[:2000]}

Answer with a JSON object containing:
- "requires_insurance_action": true or false
- "confidence": 0.0 to 1.0
- "reasoning": 1-2 sentences explaining why
- "signals": list of specific phrases that indicate an insurance requirement (or empty list)

Examples of emails that REQUIRE action:
- Vendor contracts requiring certificates of insurance
- Retailer portals needing COI upload
- Contracts specifying GL limits, additional insured, AM Best rating

Examples that do NOT require action:
- General business emails, invoices, newsletters, shipping notifications

Return only valid JSON."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)

        return (
            bool(result.get("requires_insurance_action", False)),
            float(result.get("confidence", 0.5)),
            result.get("reasoning", ""),
        )

    except Exception as e:
        logger.warning(f"Stage 2 LLM failed ({e}), falling back to heuristic")
        return _heuristic_classify(email_text)


def _heuristic_classify(email_text: str) -> tuple[bool, float, str]:
    """
    Heuristic classification when Claude is unavailable.
    Scores based on weighted keyword presence.
    """
    text = email_text.lower()

    strong_signals = [
        ("certificate of insurance", 0.25),
        ("additional insured",        0.20),
        ("general liability",         0.15),
        ("coi",                       0.10),
        ("cg 20 15",                  0.20),
        ("cg2015",                    0.15),
        ("am best",                   0.10),
        ("per occurrence",            0.15),
        ("vendor portal",             0.10),
        ("exigis",                    0.20),
        ("upload.*insurance",         0.15),
        ("coverage.*required",        0.15),
    ]

    score = 0.0
    fired = []
    for pattern, weight in strong_signals:
        if re.search(pattern, text):
            score += weight
            fired.append(pattern)

    score = min(1.0, score)
    is_req = score >= 0.25
    reasoning = (
        f"Detected {len(fired)} insurance signal(s): {', '.join(fired[:3])}"
        if fired else "No insurance requirement signals found"
    )
    return is_req, round(score, 2), reasoning


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3 — STRUCTURED EXTRACTION
# ═════════════════════════════════════════════════════════════════════════════

def stage3_extract(email_text: str) -> tuple[dict, str]:
    """
    Full structured extraction of insurance requirements.
    Returns (extracted_dict, source).
    """
    # Import here to avoid circular dependency
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from requirement_extractor import extract

    result = extract(email_text, use_static_demo=True)
    source = result.pop("source", "unknown") if "source" in result else "unknown"
    return result, source


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 4 — BUSINESS CONTEXT TRIPLE EXTRACTION
# Extracts structured KG-matchable triples from the email so scoring.py can
# do a direct SPECIALIZES_IN graph lookup instead of unreliable embedding cosine.
# ═════════════════════════════════════════════════════════════════════════════

# Valid industry labels must match Neo4j Industry node IDs
VALID_INDUSTRIES = {
    "food_service", "construction", "manufacturing", "technology", "healthcare",
    "logistics_transport", "real_estate", "professional_services", "retail",
    "cleaning_services", "landscaping", "food_manufacturing",
}

# Keyword → industry heuristic (used when Claude is unavailable)
_INDUSTRY_KW = {
    "food_service":        ["bakery", "restaurant", "catering", "kitchen", "pastry", "bread",
                            "food truck", "cafe", "coffee", "grocery", "deli", "food service"],
    "food_manufacturing":  ["food processing", "food manufacturing", "food plant", "snack",
                            "cannery", "bottling", "food production"],
    "construction":        ["contractor", "construction", "building", "builder", "renovation",
                            "remodel", "general contractor", "subcontractor"],
    "technology":          ["software", "saas", "tech", "it consulting", "cyber", "app",
                            "digital", "startup", "cloud", "ai", "data"],
    "healthcare":          ["medical", "clinic", "healthcare", "hospital", "physician",
                            "dentist", "therapy", "patient"],
    "cleaning_services":   ["cleaning", "janitorial", "sanitation", "maid", "housekeeping"],
    "landscaping":         ["landscaping", "lawn", "garden", "grounds", "mowing", "tree"],
    "logistics_transport": ["delivery", "logistics", "trucking", "transport", "fleet",
                            "freight", "courier", "shipping", "warehouse"],
    "retail":              ["retail", "store", "shop", "merchandise", "e-commerce", "seller"],
    "real_estate":         ["property management", "real estate", "landlord", "tenant",
                            "commercial lease", "apartment"],
    "professional_services": ["consulting", "accounting", "law firm", "legal", "marketing",
                               "advertising", "hr", "staffing", "management consulting"],
    "manufacturing":       ["manufacturing", "machining", "fabrication", "assembly", "plant",
                            "cnc", "factory", "production line"],
}


def extract_business_context(email_text: str) -> dict:
    """
    Stage 4: Use Claude to extract structured business-context triples from the email.

    Returns dict with:
        industry          str   — one of VALID_INDUSTRIES (for SPECIALIZES_IN lookup)
        business_type     str   — plain description (e.g. "artisan bakery")
        business_keywords list  — 3-5 keywords for appetite-text keyword bonus
        coverage_types    list  — ["general_liability", "product_liability", ...]
        state             str   — two-letter state code or ""
        compliance_context str  — brief description of what triggered the need

    These triples are passed to scoring.py's semantic_score() so it can query
    (Carrier)-[:SPECIALIZES_IN {score}]->(Industry {id: industry}) directly,
    giving semantically correct scores without embedding cosine mismatch.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _heuristic_business_context(email_text)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an insurance data extraction system.

Extract business context from this email to match against an insurance carrier knowledge graph.

EMAIL:
{email_text[:2000]}

Return a JSON object with EXACTLY these fields:
- "industry": the single best matching industry from this list ONLY:
  [food_service, construction, manufacturing, technology, healthcare,
   logistics_transport, real_estate, professional_services, retail,
   cleaning_services, landscaping, food_manufacturing]
- "business_type": 2-4 word description (e.g. "artisan bakery", "IT consulting firm")
- "business_keywords": list of 3-5 lowercase keywords that describe this business type
  (e.g. ["bakery", "food production", "artisan bread", "restaurant supply"])
- "coverage_types": list of insurance coverage types needed
  (e.g. ["general_liability", "product_liability", "commercial_property"])
- "state": two-letter US state code if mentioned (e.g. "TX"), or ""
- "compliance_context": one sentence describing what triggered the insurance need

Return ONLY valid JSON. No markdown, no explanation."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)

        # Validate and normalise the industry field
        industry = result.get("industry", "")
        if industry not in VALID_INDUSTRIES:
            industry = _heuristic_industry(email_text)
        result["industry"] = industry

        logger.info(
            f"Stage 4 context: industry={result['industry']} "
            f"type={result.get('business_type','?')} "
            f"state={result.get('state','?')}"
        )
        return result

    except Exception as e:
        logger.warning(f"Stage 4 extraction failed ({e}), using heuristic")
        return _heuristic_business_context(email_text)


def _heuristic_industry(text: str) -> str:
    """Keyword-based industry classification fallback."""
    text_lower = text.lower()
    best, best_hits = "professional_services", 0
    for ind, kws in _INDUSTRY_KW.items():
        hits = sum(1 for kw in kws if kw in text_lower)
        if hits > best_hits:
            best_hits, best = hits, ind
    return best


def _heuristic_business_context(email_text: str) -> dict:
    """Full heuristic fallback when Claude is unavailable."""
    industry = _heuristic_industry(email_text)
    text_lower = email_text.lower()
    kws = [kw for kw in _INDUSTRY_KW.get(industry, []) if kw in text_lower][:5]

    # State detection (simple)
    state = ""
    for code in ["TX", "CA", "IN", "OH", "FL", "NY"]:
        if f" {code} " in email_text or f",{code}" in email_text:
            state = code
            break

    return {
        "industry":           industry,
        "business_type":      industry.replace("_", " "),
        "business_keywords":  kws,
        "coverage_types":     ["general_liability"],
        "state":              state,
        "compliance_context": "vendor/retailer contract requiring insurance",
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN AGENT
# ═════════════════════════════════════════════════════════════════════════════

def run(email_subject: str, email_body: str,
        stage1_threshold: float = 0.38) -> AgentResult:
    """
    Run the full 3-stage email intelligence pipeline.

    Args:
        email_subject: Subject line of the email
        email_body:    Full body text
        stage1_threshold: Embedding similarity threshold (lower = more sensitive)

    Returns:
        AgentResult with full analysis
    """
    result = AgentResult()

    # Combine subject + body for analysis
    full_text = f"Subject: {email_subject}\n\n{email_body}"

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    logger.info("Stage 1: Embedding similarity filter...")
    sim_score, s1_passed = stage1_embedding_filter(full_text, threshold=stage1_threshold)
    result.embedding_similarity = sim_score
    result.stage1_passed = s1_passed
    result.signals.append(f"Embedding similarity: {sim_score:.2f}")

    if not s1_passed:
        logger.info(f"Stage 1 FAILED (score={sim_score:.2f}) - not an insurance email")
        result.overall_confidence = sim_score * 0.5
        return result

    logger.info(f"Stage 1 PASSED (score={sim_score:.2f})")

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    logger.info("Stage 2: LLM classification...")
    is_req, confidence, reasoning = stage2_llm_classify(full_text)
    result.is_insurance_requirement = is_req
    result.classification_confidence = confidence
    result.classification_reasoning = reasoning
    result.stage2_passed = is_req
    result.signals.append(f"LLM: {'YES' if is_req else 'NO'} ({confidence:.0%}) — {reasoning}")

    if not is_req:
        logger.info(f"Stage 2: Not an insurance requirement ({confidence:.0%})")
        result.overall_confidence = (sim_score * 0.3 + confidence * 0.7)
        return result

    logger.info(f"Stage 2 PASSED: insurance requirement confirmed ({confidence:.0%})")

    # ── Stage 3 ───────────────────────────────────────────────────────────────
    logger.info("Stage 3: Structured extraction...")
    extracted, source = stage3_extract(full_text)
    result.extracted = extracted
    result.extraction_source = source

    signals = []
    if extracted.get("gl_limit"):
        signals.append(f"GL ${extracted['gl_limit']:,}")
    if extracted.get("endorsements"):
        signals.append(f"Endorsements: {', '.join(extracted['endorsements'])}")
    if extracted.get("additional_insured"):
        signals.append(f"Add'l insured: {extracted['additional_insured']}")
    if extracted.get("deadline"):
        signals.append(f"Deadline: {extracted['deadline']}")
    result.signals.extend(signals)

    result.overall_confidence = round(
        sim_score * 0.2 + confidence * 0.4 + (0.4 if extracted.get("gl_limit") else 0.1),
        2
    )
    logger.info(f"Stage 3 complete. Overall confidence: {result.overall_confidence:.0%}")

    # ── Stage 4 ───────────────────────────────────────────────────────────────
    # Extract business-context triples for KG-based semantic scoring.
    # These replace unreliable embedding cosine with direct SPECIALIZES_IN lookup.
    logger.info("Stage 4: Business context triple extraction...")
    ctx = extract_business_context(full_text)
    result.business_context = ctx
    result.signals.append(
        f"Context triples: industry={ctx.get('industry','?')} "
        f"type={ctx.get('business_type','?')}"
    )

    return result


# ═════════════════════════════════════════════════════════════════════════════
# BATCH INBOX PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def process_inbox(emails: list[dict]) -> list[dict]:
    """
    Process a list of emails from the inbox.
    Each email: {"subject": str, "body": str, "from": str, "date": str}
    Returns list of flagged emails with AgentResult attached.
    """
    flagged = []
    for email in emails:
        result = run(
            email_subject=email.get("subject", ""),
            email_body=email.get("body", ""),
        )
        if result.stage2_passed:
            flagged.append({**email, "agent_result": result})
            logger.info(
                f"FLAGGED: '{email.get('subject', '')[:60]}' "
                f"confidence={result.overall_confidence:.0%}"
            )
    return flagged


# ═════════════════════════════════════════════════════════════════════════════
# CLI / QUICK TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "KnowledgeGraph"))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "KnowledgeGraph" / ".env")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    test_emails = [
        {
            "subject": "Congratulations! Whole Foods Vendor Contract — Action Required",
            "body": (
                "Hi Maria, Congratulations on your vendor contract. "
                "Before your first delivery on March 14, you must upload a "
                "Certificate of Insurance. Requirements: General Liability $2,000,000 "
                "per occurrence. Additional Insured: Whole Foods Market Inc. (CG 20 15). "
                "AM Best A- or better. Register at exigis.com/wholefoods."
            ),
        },
        {
            "subject": "Your order #12345 has shipped",
            "body": "Great news! Your order has shipped and will arrive in 3-5 business days.",
        },
        {
            "subject": "New supplier agreement attached",
            "body": (
                "Please review the attached supplier agreement. "
                "Note that section 8.3 requires proof of commercial insurance "
                "naming our company as an additional insured party."
            ),
        },
    ]

    print("\n" + "=" * 60)
    print("Email Intelligence Agent — Test Run")
    print("=" * 60)

    for i, email in enumerate(test_emails, 1):
        print(f"\n--- Email {i}: '{email['subject'][:50]}' ---")
        result = run(email["subject"], email["body"])
        print(f"  Stage 1 (embedding): {result.embedding_similarity:.2f}  passed={result.stage1_passed}")
        print(f"  Stage 2 (LLM):       {'YES' if result.is_insurance_requirement else 'NO'}  "
              f"confidence={result.classification_confidence:.0%}")
        if result.stage2_passed:
            print(f"  Stage 3 (extract):   GL=${result.extracted.get('gl_limit', 'n/a'):,}"
                  if result.extracted.get('gl_limit') else
                  f"  Stage 3 (extract):   GL=n/a")
        print(f"  Overall confidence:  {result.overall_confidence:.0%}")
        print(f"  Signals: {result.signals}")
