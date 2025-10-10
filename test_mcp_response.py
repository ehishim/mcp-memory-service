#!/usr/bin/env python3
"""Test what MCP server returns for recall_memory"""
import sys
sys.path.insert(0, 'src')

import asyncio
from admin.mcp_client import MCPHttpClient
import json

async def test():
    client = MCPHttpClient('http://mevault:8030/mcp', 'Y2xhdWRlOmJlc3RfcGFzc3dvcmRfOTk5X21lbQ==')

    print("=" * 80)
    print("Testing recall_memory MCP call...")
    print("=" * 80)

    result = await client.call_tool('recall_memory', {
        'query': 'mau',
        'limit': 3
    })

    print("\nFull response:")
    print(json.dumps(result, indent=2))

    if 'memories' in result:
        print(f"\n\nFound {len(result['memories'])} memories")
        for i, mem in enumerate(result['memories'][:3], 1):
            print(f"\n{i}. Memory fields: {list(mem.keys())}")
            if 'relevance_score' in mem:
                print(f"   ✓ Has relevance_score: {mem['relevance_score']}")
            else:
                print(f"   ✗ NO relevance_score field!")
            print(f"   Content: {mem.get('content', '')[:60]}...")

asyncio.run(test())
