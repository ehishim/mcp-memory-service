#!/usr/bin/env python3
"""Test the fixed recall_memory with proper semantic search."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
from mcp_memory_service.storage.sqlite_vec import SqliteVecMemoryStorage

async def test_recall():
    print("=" * 80)
    print("TESTING FIXED RECALL_MEMORY")
    print("=" * 80)

    # Initialize storage
    storage = SqliteVecMemoryStorage('/Users/ehishim/Documents/last_mem/sqlite_vec.db')
    await storage.initialize()

    # Test semantic search with "mau"
    query = "mau"
    limit = 5
    offset = 0

    print(f"\nQuery: '{query}'")
    print(f"Limit: {limit}, Offset: {offset}\n")

    # Calculate n_results as the fix does
    actual_n_results = min(4096, (limit or 100) + offset)
    print(f"Using n_results={actual_n_results} (respects 4096 limit)\n")

    # Call recall with proper n_results
    results = await storage.recall(
        query=query,
        n_results=actual_n_results,
        start_timestamp=None,
        end_timestamp=None
    )

    print(f"✅ Semantic search returned {len(results)} results\n")

    for i, result in enumerate(results[:5], 1):
        print(f"{i}. Score: {result.relevance_score:.4f}")
        print(f"   ID: {result.memory.id[:8]}...")
        print(f"   Content: {result.memory.content[:80]}...")
        print()

    # Verify these are actually relevant results, not just time-sorted
    if len(results) > 0:
        # Check if results contain "mau" or related terms
        relevant_count = sum(1 for r in results[:10] if 'mau' in r.memory.content.lower())
        print(f"Relevance check: {relevant_count}/10 results contain 'mau'")

        if relevant_count >= 3:
            print("✅ PASS: Semantic search is working correctly!")
        else:
            print("❌ FAIL: Results don't seem semantically relevant")

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_recall())
