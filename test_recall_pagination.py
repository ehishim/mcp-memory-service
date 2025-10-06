#!/usr/bin/env python3
"""Test recall_memory pagination issue."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
from sentence_transformers import SentenceTransformer
import sqlite_vec
from sqlite_vec import serialize_float32

DB_PATH = '/Users/ehishim/Documents/last_mem/sqlite_vec.db'

print("=" * 80)
print("PAGINATION ISSUE TEST")
print("=" * 80)

conn = sqlite3.connect(DB_PATH)
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)

model = SentenceTransformer('all-MiniLM-L6-v2')

# Test query
test_query = "mau"
query_embedding = model.encode(test_query).tolist()

print(f"Query: '{test_query}'\n")

# Test with different k values
for k in [5, 10, 100, 10000]:
    cursor = conn.execute('''
        SELECT COUNT(*) FROM (
            SELECT m.id
            FROM memories m
            JOIN (
                SELECT rowid, distance
                FROM memory_embeddings
                WHERE content_embedding MATCH ? AND k = ?
                ORDER BY distance
            ) e ON m.rowid = e.rowid
        )
    ''', (serialize_float32(query_embedding), k))

    count = cursor.fetchone()[0]
    print(f"k={k:5d} → Returns {count:3d} results")

print("\n" + "=" * 80)
print("EXPLANATION:")
print("=" * 80)
print("When k=10000, vector search returns ALL 477 memories because the database")
print("only has 477 total memories. The 'k' parameter doesn't filter by relevance,")
print("it just limits the maximum number of results.")
print("\nThis means 'total_count' in recall_memory always shows 477 instead of the")
print("actual number of relevant results for the query.")

conn.close()
