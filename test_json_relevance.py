#!/usr/bin/env python3
"""Test if recall_memory returns relevance_score in JSON."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import asyncio
import json
from mcp_memory_service.server import MCPMemoryServer

async def test_json_output():
    print("=" * 80)
    print("TESTING JSON OUTPUT WITH RELEVANCE SCORES")
    print("=" * 80)

    # Create server instance
    server = MCPMemoryServer()

    # Mock arguments for recall_memory
    arguments = {
        "query": "mau",
        "limit": 3,
        "offset": 0
    }

    print(f"\nCalling handle_recall_memory with:")
    print(f"  query: {arguments['query']}")
    print(f"  limit: {arguments['limit']}")
    print(f"  offset: {arguments['offset']}\n")

    # Call the handler
    result = await server.handle_recall_memory(arguments)

    # Extract JSON from TextContent
    if result and len(result) > 0:
        json_text = result[0].text
        data = json.loads(json_text)

        print("JSON Response Structure:")
        print(f"  success: {data.get('success')}")
        print(f"  pagination.total: {data.get('pagination', {}).get('total')}")
        print(f"  pagination.limit: {data.get('pagination', {}).get('limit')}")
        print(f"  memories count: {len(data.get('memories', []))}\n")

        # Check if relevance_score is included
        if data.get('memories'):
            print("First memory fields:")
            first_mem = data['memories'][0]
            for key in sorted(first_mem.keys()):
                value = first_mem[key]
                if key == 'relevance_score':
                    print(f"  ✅ {key}: {value}")
                elif key == 'content':
                    print(f"  {key}: {str(value)[:60]}...")
                else:
                    print(f"  {key}: {value}")

            # Check all memories have relevance_score
            has_relevance = all('relevance_score' in m for m in data['memories'])
            print(f"\n{'✅' if has_relevance else '❌'} All memories have relevance_score: {has_relevance}")

            if has_relevance:
                print("\nRelevance scores for all results:")
                for i, mem in enumerate(data['memories'], 1):
                    print(f"  {i}. {mem['relevance_score']:.4f} - {mem['content'][:60]}...")
        else:
            print("❌ No memories in response!")

    print("\n" + "=" * 80)

if __name__ == "__main__":
    asyncio.run(test_json_output())
