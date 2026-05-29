"""
BindIQ Agent 2 — Hybrid Scoring Engine
Scores and ranks carriers for a given customer using 3 components:

  30%  Semantic match     — cosine similarity: customer desc ↔ carrier appetite
  40%  Graph path score   — multi-hop: appetite + SIMILAR_TO success paths
  30%  Hard rules         — state licensed, AM Best, revenue / employee range

Also generates a plain-English explanation via Claude Haiku.

Usage:
  from scoring import score_customer
  rankings = score_customer(customer_id="maria_bakery_tx")

  # or standalone:
  python scoring.py --customer maria_bakery_tx
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE,
    WEIGHT_SEMANTIC, WEIGHT_GRAPH, WEIGHT_RULES,
    MIN_AM_BEST, ANTHROPIC_API_KEY, LOG_DIR,
)
from embeddings import cosine_similarity

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "scoring.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scoring")


# ═════════════════════════════════════════════════════════════════════════════
# COMPONENT 1 — SEMANTIC SCORE  (30%)
#
# Strategy (in priority order):
#
#   A) Triple-based (preferred): use Claude-extracted business context triples
#      to query (Carrier)-[:SPECIALIZES_IN {score}]->(Industry {id: industry})
#      directly. This gives semantically correct scores:
#        NEXT  × food_service  → 0.92   (correct)
#        Hiscox × food_service → 0.30   (correct — they're a tech/professional carrier)
#
#      Optionally adds a keyword bonus (+0–0.10) when the carrier's appetite_text
#      mentions the customer's specific business keywords (e.g. "bakery").
#
#   B) Graph-path fallback: if no context triples but customer has OPERATES_IN,
#      use that industry for the SPECIALIZES_IN lookup (no keyword bonus).
#
#   C) Embedding cosine fallback: original behaviour — only used when the graph
#      has neither OPERATES_IN nor context triples. Kept as last resort because
#      the text domains are different (customer description vs carrier marketing
#      text) which limits cosine similarity to ~0.25–0.35 for valid matches.
# ═════════════════════════════════════════════════════════════════════════════

def semantic_score(session, customer_id: str,
                   business_context: dict | None = None) -> dict[str, float]:
    """
    Returns {carrier_id: semantic_score_0_to_1}.

    business_context: dict from email_agent.extract_business_context()
        {industry, business_type, business_keywords, coverage_types, state}
    """
    # ── Path A/B: SPECIALIZES_IN graph query ──────────────────────────────────
    # Resolve the target industry:
    #   A — from Claude-extracted triples (most accurate)
    #   B — from OPERATES_IN relationship in graph
    industry_id = None

    if business_context and business_context.get("industry"):
        industry_id = business_context["industry"]
        logger.debug(f"Semantic: using extracted industry '{industry_id}' (Stage 4 triples)")
    else:
        # Fallback: read OPERATES_IN from graph
        row = session.run(
            "MATCH (cu:Customer {id: $cid})-[:OPERATES_IN]->(i:Industry) "
            "RETURN i.id AS iid LIMIT 1",
            cid=customer_id,
        ).single()
        if row:
            industry_id = row["iid"]
            logger.debug(f"Semantic: using OPERATES_IN industry '{industry_id}' (graph fallback)")

    if industry_id:
        spec_result = session.run(
            """
            MATCH (c:Carrier)-[r:SPECIALIZES_IN]->(i:Industry {id: $iid})
            RETURN c.id AS cid, r.score AS spec_score,
                   c.appetite_text AS appetite_text
            """,
            iid=industry_id,
        )

        business_keywords = (
            [kw.lower() for kw in business_context.get("business_keywords", [])]
            if business_context else []
        )

        scores: dict[str, float] = {}
        for row in spec_result:
            base = float(row["spec_score"] or 0.0)

            # Keyword bonus: carrier's appetite_text mentions the business keywords
            # e.g. NEXT appetite text contains "food service" → bonus for "bakery" query
            kw_bonus = 0.0
            if business_keywords and row["appetite_text"]:
                apt_lower = row["appetite_text"].lower()
                hits = sum(1 for kw in business_keywords if kw in apt_lower)
                kw_bonus = min(0.10, hits * 0.03)  # cap at +10%

            scores[row["cid"]] = round(min(1.0, base + kw_bonus), 4)

        if scores:
            logger.debug(
                f"Semantic scores via SPECIALIZES_IN ({industry_id}): "
                + ", ".join(f"{k}={v:.2f}" for k, v in
                            sorted(scores.items(), key=lambda x: -x[1])[:5])
            )
            return scores

    # ── Path C: embedding cosine fallback ─────────────────────────────────────
    logger.debug(
        f"Semantic: no industry resolved for {customer_id}, "
        "falling back to embedding cosine similarity"
    )
    result = session.run(
        """
        MATCH (cu:Customer {id: $cid})
        WHERE cu.description_embedding IS NOT NULL
        MATCH (c:Carrier)
        WHERE c.appetite_embedding IS NOT NULL
        RETURN c.id AS cid,
               cu.description_embedding AS cust_vec,
               c.appetite_embedding AS carr_vec
        """,
        cid=customer_id,
    )
    scores = {}
    for row in result:
        sim = cosine_similarity(row["cust_vec"], row["carr_vec"])
        scores[row["cid"]] = round(sim, 4)
    return scores


# ═════════════════════════════════════════════════════════════════════════════
# COMPONENT 2 — GRAPH PATH SCORE  (40%)
# Sub-components:
#   a) SPECIALIZES_IN score for the customer's industry
#   b) Similar customer success (SIMILAR_TO + INSURED_BY outcome=good)
#   c) Complaint ratio penalty
# ═════════════════════════════════════════════════════════════════════════════

def graph_score(session, customer_id: str) -> dict[str, float]:
    """Returns {carrier_id: graph_confidence_0_to_1}"""

    # a) Appetite (specialisation) score for this customer's industry
    result_appetite = session.run(
        """
        MATCH (cu:Customer {id: $cid})-[:OPERATES_IN]->(i:Industry)
        MATCH (c:Carrier)-[r:SPECIALIZES_IN]->(i)
        RETURN c.id AS cid, r.score AS appetite_score
        """,
        cid=customer_id,
    )
    appetite = {row["cid"]: float(row["appetite_score"] or 0) for row in result_appetite}

    # b) Similar customer success rate
    result_similar = session.run(
        """
        MATCH (cu:Customer {id: $cid})-[:SIMILAR_TO]-(peer:Customer)
        MATCH (peer)-[ins:INSURED_BY]->(c:Carrier)
        WHERE ins.outcome IN ['good', 'excellent']
        RETURN c.id AS cid, count(ins) AS success_count
        """,
        cid=customer_id,
    )
    # Normalise: 1 success = 0.5, 2+ = scale toward 1.0
    similar_success: dict[str, float] = {}
    for row in result_similar:
        n = int(row["success_count"])
        similar_success[row["cid"]] = min(1.0, n / 4.0)

    # c) Complaint ratio (lower is better; 1.0 = industry average)
    result_cr = session.run(
        """
        MATCH (c:Carrier)
        RETURN c.id AS cid, c.complaint_ratio_nat AS cr
        """
    )
    # Normalise: cr=0 → 1.0, cr=1 → 0.7, cr=2 → 0.4, cr≥3 → 0.0
    cr_scores: dict[str, float] = {}
    for row in result_cr:
        cr = float(row["cr"] or 1.0)
        cr_score = max(0.0, 1.0 - (cr * 0.3))
        cr_scores[row["cid"]] = round(cr_score, 4)

    # Combine sub-components: appetite 50%, similar 30%, cr_score 20%
    all_carrier_ids = set(appetite) | set(similar_success) | set(cr_scores)
    scores = {}
    for cid in all_carrier_ids:
        ap = appetite.get(cid, 0.0)
        ss = similar_success.get(cid, 0.0)
        cr = cr_scores.get(cid, 0.7)
        scores[cid] = round(ap * 0.50 + ss * 0.30 + cr * 0.20, 4)

    return scores


# ═════════════════════════════════════════════════════════════════════════════
# COMPONENT 3 — HARD RULES SCORE  (30%)
# Pass/fail gates with partial credit:
#   Licensed in customer's state       (+0.40)
#   AM Best rating ≥ A-                (+0.30)
#   Binding speed: fast/instant        (+0.20)
#   Insurify rating ≥ 4.0             (+0.10)
# ═════════════════════════════════════════════════════════════════════════════

def rules_score(session, customer_id: str) -> dict[str, float]:
    """Returns {carrier_id: rules_score_0_to_1}"""

    result = session.run(
        """
        MATCH (cu:Customer {id: $cid})
        MATCH (c:Carrier)
        OPTIONAL MATCH (c)-[lic:LICENSED_IN]->(s:State {code: cu.state})
        RETURN c.id AS cid,
               cu.state AS cust_state,
               lic IS NOT NULL AS licensed,
               c.am_best AS am_best,
               c.binding_speed_tier AS speed,
               c.insurify_rating AS ins_rating
        """,
        cid=customer_id,
    )

    scores = {}
    for row in result:
        cid = row["cid"]
        score = 0.0

        # Licensed in state (hard gate: if not licensed → 0)
        if row["licensed"]:
            score += 0.40
        else:
            scores[cid] = 0.0
            continue

        # AM Best rating
        am_rank = MIN_AM_BEST.get(str(row["am_best"] or ""), 0)
        if am_rank >= 2:   # A- or better
            score += 0.30
        elif am_rank == 1:  # B++
            score += 0.15

        # Binding speed
        speed = str(row["speed"] or "").lower()
        if speed in ("instant", "same_day", "fast"):
            score += 0.20
        elif speed == "standard":
            score += 0.10

        # Insurify rating
        ins = float(row["ins_rating"] or 0)
        if ins >= 4.0:
            score += 0.10
        elif ins >= 3.0:
            score += 0.05

        scores[cid] = round(score, 4)

    return scores


# ═════════════════════════════════════════════════════════════════════════════
# HYBRID SCORER
# ═════════════════════════════════════════════════════════════════════════════

def score_customer(
    customer_id: str,
    driver=None,
    top_n: int = 5,
    explain: bool = True,
    business_context: dict | None = None,
) -> list[dict]:
    """
    Score and rank all carriers for customer_id.
    Returns list of dicts (sorted by total_score desc), top_n results.

    Each dict:
      carrier_id, carrier_name, total_score (0-100),
      semantic_score, graph_score, rules_score,
      explanation (str, optional)
    """
    own_driver = driver is None
    if own_driver:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            # Fetch customer info
            customer_result = session.run(
                "MATCH (cu:Customer {id: $cid}) RETURN cu",
                cid=customer_id,
            ).single()
            if not customer_result:
                raise ValueError(f"Customer '{customer_id}' not found in Neo4j")
            customer = dict(customer_result["cu"])

            logger.info(
                f"Scoring carriers for: {customer.get('business_name', customer_id)} "
                f"[{customer.get('industry_id', '')} / {customer.get('state', '')}]"
            )

            # Compute 3 components
            sem   = semantic_score(session, customer_id,
                                   business_context=business_context)
            graph = graph_score(session, customer_id)
            rules = rules_score(session, customer_id)

            # Fetch carrier names
            names_result = session.run("MATCH (c:Carrier) RETURN c.id AS cid, c.name AS name")
            carrier_names = {r["cid"]: r["name"] for r in names_result}

            # Combine
            all_ids = set(sem) | set(graph) | set(rules)
            rankings = []
            for cid in all_ids:
                s  = sem.get(cid, 0.0)
                g  = graph.get(cid, 0.0)
                r  = rules.get(cid, 0.0)

                # Hard gate: if rules=0 (not licensed) → disqualified
                if r == 0.0:
                    continue

                total = round(
                    (s * WEIGHT_SEMANTIC + g * WEIGHT_GRAPH + r * WEIGHT_RULES) * 100, 1
                )
                rankings.append({
                    "carrier_id":    cid,
                    "carrier_name":  carrier_names.get(cid, cid),
                    "total_score":   total,
                    "semantic_score": round(s * 100, 1),
                    "graph_score":   round(g * 100, 1),
                    "rules_score":   round(r * 100, 1),
                    "explanation":   None,
                })

            rankings.sort(key=lambda x: x["total_score"], reverse=True)
            top = rankings[:top_n]

            # Generate LLM explanations for top results
            if explain and top:
                for item in top:
                    item["explanation"] = generate_explanation(customer, item, session)

        return top

    finally:
        if own_driver:
            driver.close()


# ═════════════════════════════════════════════════════════════════════════════
# LLM EXPLANATION (Claude Haiku)
# ═════════════════════════════════════════════════════════════════════════════

def generate_explanation(customer: dict, ranking: dict, session) -> str:
    """
    Use Claude Haiku to generate a 2-3 sentence plain-English explanation
    of WHY this carrier is recommended for this customer.
    Falls back to a template string if no API key is set.
    """
    if not ANTHROPIC_API_KEY:
        return _template_explanation(customer, ranking)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        # Fetch carrier details for context
        carr_result = session.run(
            "MATCH (c:Carrier {id: $cid}) RETURN c",
            cid=ranking["carrier_id"],
        ).single()
        carrier = dict(carr_result["c"]) if carr_result else {}

        prompt = f"""
You are a commercial insurance broker explaining a carrier recommendation to a small business owner.

Customer:
- Business: {customer.get('business_name', '')}
- Industry: {customer.get('industry_id', '').replace('_', ' ').title()}
- State: {customer.get('state', '')}
- Description: {customer.get('description', '')}
- Coverage needs: {customer.get('coverage_needs', '')}

Recommended carrier: {ranking['carrier_name']}
- Match score: {ranking['total_score']}/100
  - Industry specialisation: {ranking['semantic_score']}/100
  - Track record / graph: {ranking['graph_score']}/100
  - Eligibility rules: {ranking['rules_score']}/100
- AM Best: {carrier.get('am_best', '')}
- Binding speed: {carrier.get('binding_speed_tier', '')}

Write exactly 2 sentences explaining why {ranking['carrier_name']} is a strong fit.
Be specific about the industry, coverage, and any time-sensitive context.
Do not use bullet points. Do not mention the score numbers.
""".strip()

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.debug(f"LLM explanation failed ({e}), using template")
        return _template_explanation(customer, ranking)


def _template_explanation(customer: dict, ranking: dict) -> str:
    industry = customer.get("industry_id", "").replace("_", " ").title()
    state    = customer.get("state", "")
    biz      = customer.get("business_name", "your business")
    carrier  = ranking["carrier_name"]
    score    = ranking["total_score"]
    return (
        f"{carrier} is our top recommendation for {biz} with a match score of {score}/100. "
        f"They are licensed in {state}, have strong expertise in {industry}, "
        f"and offer fast coverage binding to meet your timeline."
    )


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Score carriers for a customer")
    parser.add_argument("--customer", required=True, help="Customer ID in Neo4j")
    parser.add_argument("--top", type=int, default=5, help="Number of top carriers to return")
    parser.add_argument("--no-explain", action="store_true", help="Skip LLM explanations")
    args = parser.parse_args()

    rankings = score_customer(
        customer_id=args.customer,
        top_n=args.top,
        explain=not args.no_explain,
    )

    print(f"\nTop {len(rankings)} carriers for customer '{args.customer}':\n")
    for i, r in enumerate(rankings, 1):
        print(f"  {i}. {r['carrier_name']:25s}  {r['total_score']:5.1f}/100  "
              f"(sem={r['semantic_score']:.0f} graph={r['graph_score']:.0f} rules={r['rules_score']:.0f})")
        if r.get("explanation"):
            # Indent explanation
            for line in r["explanation"].split(". "):
                if line.strip():
                    print(f"       {line.strip()}.")


if __name__ == "__main__":
    main()
