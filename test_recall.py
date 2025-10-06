#!/usr/bin/env python3
"""Test recall_memory functionality on the production database."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
import asyncio
from sentence_transformers import SentenceTransformer
import sqlite_vec
from sqlite_vec import serialize_float32

DB_PATH = '/Users/ehishim/Documents/last_mem/sqlite_vec.db'

print("=" * 80)
print("RECALL_MEMORY DIAGNOSTIC TEST")
print("=" * 80)
print(f"Database: {DB_PATH}\n")

# Connect to database
conn = sqlite3.connect(DB_PATH)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)

# Check counts
print("[1/5] Database counts...")
mem_count = conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
print(f"✅ Memories: {mem_count}")

# Check embedding table
try:
    emb_count = conn.execute('SELECT COUNT(*) FROM memory_embeddings').fetchone()[0]
    print(f"✅ Embeddings: {emb_count}")
except Exception as e:
    print(f"❌ Embeddings error: {e}")
    sys.exit(1)

# Check rowid alignment
print("\n[2/5] Checking rowid alignment...")
try:
    aligned = conn.execute('''
        SELECT COUNT(*)
        FROM memories m
        JOIN memory_embeddings e ON m.rowid = e.rowid
    ''').fetchone()[0]
    print(f"✅ Aligned rowids: {aligned}/{mem_count}")

    if aligned != mem_count:
        print(f"⚠️  WARNING: Only {aligned} out of {mem_count} memories have aligned embeddings!")
except Exception as e:
    print(f"❌ Alignment check failed: {e}")

# Load embedding model
print("\n[3/5] Loading embedding model...")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("✅ Model loaded: all-MiniLM-L6-v2")

# Test semantic search
print("\n[4/5] Testing semantic search...")
test_query = "mau"
print(f"Query: '{test_query}'")

# Generate query embedding
query_embedding = model.encode(test_query).tolist()
print(f"✅ Query embedding generated ({len(query_embedding)} dimensions)")

# Test vector search
print("\n[5/5] Running vector search...")
try:
    cursor = conn.execute('''
        SELECT m.id, m.content, e.distance
        FROM memories m
        JOIN (
            SELECT rowid, distance
            FROM memory_embeddings
            WHERE content_embedding MATCH ? AND k = ?
            ORDER BY distance
        ) e ON m.rowid = e.rowid
        ORDER BY e.distance
        LIMIT 5
    ''', (serialize_float32(query_embedding), 10))

    results = cursor.fetchall()
    print(f"✅ Found {len(results)} results\n")

    for i, (id_val, content, distance) in enumerate(results, 1):
        print(f"{i}. ID: {id_val[:8]}...")
        print(f"   Distance: {distance:.4f}")
        print(f"   Content: {content[:100]}...")
        print()

except Exception as e:
    print(f"❌ Vector search failed: {e}")
    import traceback
    traceback.print_exc()

conn.close()

print("=" * 80)
print("TEST COMPLETE")
print("=" * 80)
