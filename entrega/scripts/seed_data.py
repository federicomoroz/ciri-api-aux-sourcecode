#!/usr/bin/env python3
"""
Seed script: Load Excel data into SQLite + index Qdrant.

Run from project root:
    python scripts/seed_data.py

Prerequisites:
    - Qdrant running at CB_QDRANT_URL (default: http://localhost:6333)
    - .env file with CB_ variables (or environment variables)
"""

import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.app.config import Settings
from api.app.data.loader import init_sqlite, load_excel
from api.app.rag.embedder import FastEmbedder
from api.app.rag.indexer import QdrantIndexer

try:
    from qdrant_client import QdrantClient
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install qdrant-client fastembed")
    sys.exit(1)


def seed():
    settings = Settings()

    print("=" * 60)
    print("CIRI Chargeback Agent — Data Seed")
    print("=" * 60)

    # Step 1: Load Excel
    print(f"\n[1/4] Loading Excel: {settings.data_file_path}")
    data = load_excel(settings.data_file_path)
    print(f"      Transactions: {len(data['transactions'])}")
    print(f"      Cases:        {len(data['cases'])}")
    print(f"      Policies:     {len(data['policies'])}")
    print(f"      Logs:         {len(data['logs'])}")

    # Step 2: SQLite
    print(f"\n[2/4] Initializing SQLite: {settings.sqlite_path}")
    os.makedirs(os.path.dirname(settings.sqlite_path) if os.path.dirname(settings.sqlite_path) else ".", exist_ok=True)
    init_sqlite(settings.sqlite_path, data)
    print("      Done.")

    # Step 3: Qdrant collections
    print(f"\n[3/4] Connecting to Qdrant: {settings.qdrant_url}")
    try:
        qdrant = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
        qdrant.get_collections()
        print("      Connected.")
    except Exception as e:
        print(f"      ERROR: Cannot connect to Qdrant: {e}")
        print("      Make sure Qdrant is running (or check CB_QDRANT_URL / CB_QDRANT_API_KEY)")
        sys.exit(1)

    # Step 4: Index
    print(f"\n[4/4] Loading embedding model: {settings.embedding_model}")
    embedder = FastEmbedder(settings.embedding_model)

    indexer = QdrantIndexer(qdrant, embedder)
    indexer.ensure_collections()
    print("      Collections created/verified.")

    n_policies = indexer.index_policies(data["policies"])
    print(f"      Indexed policies:         {n_policies}")

    n_cases = indexer.index_historical_cases(data["cases"], data["transactions"])
    print(f"      Indexed historical cases: {n_cases}")

    print("\n" + "=" * 60)
    print("✓ Seed completed successfully!")
    print(f"  policies:{len(data['policies'])} | historical_cases:{len(data['cases'])} | transactions:{len(data['transactions'])} | logs:{len(data['logs'])}")
    print("\nNext steps:")
    print("  1. Start the API:    docker-compose up api")
    print("  2. Check health:     curl http://localhost:8000/health")
    print("  3. Import n8n flow:  http://localhost:5678 → Import → n8n/workflow_ciri_agent.json")
    print("=" * 60)


if __name__ == "__main__":
    seed()
