#!/usr/bin/env python3
"""Test the complete pagination fix for recall_memory."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
from mcp_memory_service.storage.sqlite_vec import SqliteVecMemoryStorage

async def test_pagination():
    print("=" * 80)
    print("TESTING COMPLETE PAGINATION FIX")
    print("=" * 80)

    storage = SqliteVecMemoryStorage('/Users/ehishim/Documents/last_mem/sqlite_vec.db')
    await storage.initialize()

    tests = [
        ("Semantic search with limit", "mau", 3, 0),
        ("Semantic search with offset", "mau", 3, 3),
        ("Wildcard with limit", "*", 5, 0),
        ("Wildcard without limit (all)", "*", None, 0),
    ]

    for test_name, query, limit, offset in tests:
        print(f"\n### {test_name}")
        print(f"Query: '{query}', Limit: {limit}, Offset: {offset}")

        results, total_count = await storage.recall(
            query=query if query != "*" else None,
            limit=limit,
            offset=offset
        )

        print(f"✅ Returned {len(results)} results, Total: {total_count}")

        # Show first 2 results
        for i, result in enumerate(results[:2], 1):
            score_str = f" (Score: {result.relevance_score:.4f})" if result.relevance_score else ""
            print(f"  {i}.{score_str} {result.memory.content[:60]}...")

        # Validation
        if limit is not None:
            if len(results) > limit:
                print(f"  ❌ ERROR: Returned {len(results)} but limit was {limit}")
            else:
                print(f"  ✅ Pagination working: {len(results)} <= {limit}")

        if query != "*":
            # For semantic search, check relevance
            has_scores = all(r.relevance_score is not None for r in results)
            print(f"  {'✅' if has_scores else '❌'} Relevance scores present: {has_scores}")

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_pagination())
