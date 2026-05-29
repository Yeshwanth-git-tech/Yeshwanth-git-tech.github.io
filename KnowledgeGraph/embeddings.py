"""
BindIQ Agent 2 — Embeddings
Generates sentence-transformer embeddings for:
  1. Carrier appetite descriptions (industry specialization text)
  2. Customer business descriptions

Stores embeddings as Neo4j node properties so the scoring engine can
compute cosine similarity without re-running the model each time.

Model: all-MiniLM-L6-v2  (384-dim, fast, good quality for short texts)

Run standalone:
  python embeddings.py
"""

import json
import logging
import sys
from pathlib import Path

import numpy as np
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE,
    EMBEDDING_MODEL, LOG_DIR,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "embeddings.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("embeddings")


# ── Lazy model load ───────────────────────────────────────────────────────────
_model = None

def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("  Model ready")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalised embeddings for a list of texts."""
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return vecs


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two pre-normalised embedding vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    dot = float(np.dot(va, vb))
    return max(0.0, min(1.0, dot))   # clamp to [0, 1]


# ═════════════════════════════════════════════════════════════════════════════
# CARRIER APPETITE TEXT
# ═════════════════════════════════════════════════════════════════════════════

# Human-readable appetite descriptions per carrier (used as embedding seed text)
CARRIER_APPETITE_TEXT = {
    "hartford": (
        "The Hartford specialises in manufacturing, construction, and professional services. "
        "Strong workers comp, GL, and equipment breakdown coverage. "
        "Preferred for mid-market businesses needing broad commercial coverage."
    ),
    "progressive": (
        "Progressive Commercial excels in commercial auto and logistics / transportation. "
        "Best for trucking, delivery fleets, and businesses with significant vehicle exposure."
    ),
    "next": (
        "NEXT Insurance focuses on small businesses: cleaning services, landscaping, "
        "food service, retail, and contractors. Fast digital quoting, same-day COI, "
        "ideal for sole proprietors and micro-businesses."
    ),
    "travelers": (
        "Travelers is a leading carrier for construction, manufacturing, and real estate. "
        "Strong builders risk, umbrella, and surety bond capabilities. "
        "Preferred for large contractors and real estate portfolios."
    ),
    "chubb": (
        "Chubb specialises in technology, professional services, and healthcare. "
        "Best for high-value tech companies, E&O, D&O, and cyber liability. "
        "Premium carrier for complex risk profiles."
    ),
    "nationwide": (
        "Nationwide covers food service, retail, and agriculture. "
        "Good GL and property bundled as BOP for small-to-mid restaurants and retail shops."
    ),
    "hiscox": (
        "Hiscox specialises in technology, professional services, consulting, and media. "
        "Strong E&O and cyber coverage for IT firms, agencies, and consultants. "
        "Internationally backed specialty insurer."
    ),
    "markel": (
        "Markel is a specialty carrier for construction, manufacturing, and logistics. "
        "Hard-to-place risks, contractor liability, and surplus lines expertise."
    ),
    "simply_business": (
        "Simply Business is a digital marketplace aggregating multiple carriers. "
        "Best for cleaning services, landscaping, and food service. "
        "Fastest quotes for micro-businesses needing basic GL."
    ),
    "liberty_mutual": (
        "Liberty Mutual covers construction, manufacturing, and real estate. "
        "Broad commercial lines including property, GL, and umbrella. "
        "Large account capabilities with risk engineering services."
    ),
    "zurich": (
        "Zurich Commercial specialises in manufacturing, construction, and food manufacturing. "
        "Global Fortune 500 relationships, complex product liability, and recall insurance."
    ),
    "cna": (
        "CNA Financial focuses on professional services, healthcare, and technology. "
        "Strong professional liability, malpractice, E&O, and cyber for regulated industries."
    ),
}


def build_carrier_appetite_texts(session) -> list[tuple[str, str]]:
    """
    Pull carrier appetite from Neo4j SPECIALIZES_IN relationships to build
    dynamic appetite text, then blend with the static seed text above.
    Returns [(carrier_id, combined_text), ...]
    """
    result = session.run(
        """
        MATCH (c:Carrier)-[r:SPECIALIZES_IN]->(i:Industry)
        WHERE r.score >= 0.5
        RETURN c.id AS cid, c.name AS cname,
               collect(i.name + ' (score:' + toString(round(r.score*100)/100) + ')') AS industries
        ORDER BY c.id
        """
    )
    texts = []
    for row in result:
        cid = row["cid"]
        cname = row["cname"]
        industry_list = ", ".join(row["industries"])
        # Combine dynamic graph data with curated seed text
        seed = CARRIER_APPETITE_TEXT.get(cid, f"{cname} commercial insurance carrier.")
        combined = f"{seed} Key industries: {industry_list}."
        texts.append((cid, combined))

    # Add any carriers not yet in graph (fallback to seed only)
    graph_ids = {t[0] for t in texts}
    for cid, seed in CARRIER_APPETITE_TEXT.items():
        if cid not in graph_ids:
            texts.append((cid, seed))

    return texts


def store_carrier_embeddings(session, carrier_texts: list[tuple[str, str]]) -> int:
    if not carrier_texts:
        return 0

    ids, texts = zip(*carrier_texts)
    vecs = embed(list(texts))

    count = 0
    for cid, vec in zip(ids, vecs):
        session.run(
            """
            MATCH (c:Carrier {id: $cid})
            SET c.appetite_embedding    = $vec,
                c.appetite_text         = $text,
                c.embedding_model       = $model
            """,
            cid=cid,
            vec=vec.tolist(),
            text=dict(carrier_texts)[cid],
            model=EMBEDDING_MODEL,
        )
        count += 1
    return count


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOMER EMBEDDINGS
# ═════════════════════════════════════════════════════════════════════════════

def store_customer_embeddings(session) -> int:
    """
    Embed every Customer node that has a description and lacks an embedding.
    """
    result = session.run(
        """
        MATCH (cu:Customer)
        WHERE cu.description IS NOT NULL
          AND cu.description <> ''
          AND cu.description_embedding IS NULL
        RETURN cu.id AS cid, cu.description AS desc
        """
    )
    rows = [(r["cid"], r["desc"]) for r in result]

    if not rows:
        logger.info("  No new customer embeddings needed")
        return 0

    ids, texts = zip(*rows)
    vecs = embed(list(texts))

    count = 0
    for cid, vec in zip(ids, vecs):
        session.run(
            """
            MATCH (cu:Customer {id: $cid})
            SET cu.description_embedding = $vec,
                cu.embedding_model       = $model
            """,
            cid=cid,
            vec=vec.tolist(),
            model=EMBEDDING_MODEL,
        )
        count += 1
    return count


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOMER SIMILARITY GRAPH
# ═════════════════════════════════════════════════════════════════════════════

SIMILARITY_THRESHOLD = 0.70   # only create edge if cosine ≥ 0.70

def build_customer_similarity_edges(session) -> int:
    """
    For every pair of customers with embeddings, compute cosine similarity
    and create (Customer)-[:SIMILAR_TO {score}]->(Customer) if ≥ threshold.
    Runs in Python (not Cypher) because Neo4j Community doesn't have gds.
    """
    result = session.run(
        """
        MATCH (cu:Customer)
        WHERE cu.description_embedding IS NOT NULL
        RETURN cu.id AS cid, cu.description_embedding AS vec
        """
    )
    customers = [(r["cid"], r["vec"]) for r in result]

    if len(customers) < 2:
        return 0

    count = 0
    n = len(customers)
    for i in range(n):
        for j in range(i + 1, n):
            cid_a, vec_a = customers[i]
            cid_b, vec_b = customers[j]
            score = cosine_similarity(vec_a, vec_b)
            if score >= SIMILARITY_THRESHOLD:
                session.run(
                    """
                    MATCH (a:Customer {id: $a}), (b:Customer {id: $b})
                    MERGE (a)-[r:SIMILAR_TO]->(b)
                    SET r.score = $score
                    MERGE (b)-[r2:SIMILAR_TO]->(a)
                    SET r2.score = $score
                    """,
                    a=cid_a, b=cid_b, score=round(score, 4),
                )
                count += 1

    return count


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    logger.info("=" * 60)
    logger.info("BindIQ Agent 2 — Embeddings")
    logger.info("=" * 60)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            # 1. Carrier appetite embeddings
            logger.info("Building carrier appetite embeddings...")
            carrier_texts = build_carrier_appetite_texts(session)
            n_carriers = store_carrier_embeddings(session, carrier_texts)
            logger.info(f"  Carrier embeddings stored: {n_carriers}")

            # 2. Customer description embeddings
            logger.info("Building customer description embeddings...")
            n_customers = store_customer_embeddings(session)
            logger.info(f"  Customer embeddings stored: {n_customers}")

            # 3. Customer similarity graph
            logger.info("Building customer similarity edges...")
            n_edges = build_customer_similarity_edges(session)
            logger.info(f"  SIMILAR_TO edges created: {n_edges}")

        result = {
            "status": "ok",
            "carriers_embedded": n_carriers,
            "customers_embedded": n_customers,
            "similarity_edges": n_edges,
        }
        logger.info("  Embeddings complete")
        return result

    finally:
        driver.close()


if __name__ == "__main__":
    result = run()
    print(f"\nEmbeddings done. Status: {result['status']}")
    print(f"  Carriers: {result['carriers_embedded']} embeddings")
    print(f"  Customers: {result['customers_embedded']} embeddings")
    print(f"  Similarity edges: {result['similarity_edges']}")
