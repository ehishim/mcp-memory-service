#!/usr/bin/env python3
"""
Database Migration Script - Rebuild with Current Format
Reads old backup and rebuilds database exactly as if memories were stored
naturally through current store() method.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
import json
import uuid
from datetime import datetime
from sentence_transformers import SentenceTransformer
import sqlite_vec
from sqlite_vec import serialize_float32 as serialize_f32

# Import the actual hashing function from the codebase
from mcp_memory_service.utils.hashing import generate_content_hash

# Paths
OLD_DB = '/Users/ehishim/Documents/last_mem/sqlite_vec.db.backup'
NEW_DB = '/Users/ehishim/Documents/last_mem/sqlite_vec.db'

print("=" * 80)
print("DATABASE MIGRATION - Rebuild with Current Format")
print("=" * 80)
print(f"Source: {OLD_DB}")
print(f"Target: {NEW_DB}")
print()

# Backup current database
import shutil
backup_path = f"{NEW_DB}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
print(f"Creating backup: {backup_path}")
shutil.copy(NEW_DB, backup_path)

# Read old database - extract only essential data
print("\n[1/5] Reading old database...")
old_conn = sqlite3.connect(OLD_DB)
old_cursor = old_conn.execute('''
    SELECT content, tags, metadata, created_at, updated_at, created_at_iso, updated_at_iso
    FROM memories
    ORDER BY id
''')

memories = []
for row in old_cursor.fetchall():
    content, tags_str, metadata_str, created_at, updated_at, created_at_iso, updated_at_iso = row

    # Parse metadata
    metadata = json.loads(metadata_str) if metadata_str else {}

    # Clean metadata: remove 'tags' and 'type' properties
    # These are now stored separately (tags column) or deprecated (type)
    cleaned_metadata = {k: v for k, v in metadata.items() if k not in ['tags', 'type']}

    # Parse tags from tags column
    tags_list = [t.strip() for t in tags_str.split(',') if t.strip()] if tags_str else []

    memories.append({
        'content': content,
        'tags': tags_list,
        'metadata': cleaned_metadata,  # Cleaned metadata
        'created_at': created_at,
        'updated_at': updated_at,
        'created_at_iso': created_at_iso,
        'updated_at_iso': updated_at_iso
    })

print(f"✅ Read {len(memories)} memories")
print(f"   Metadata cleaned: removed 'tags' and 'type' properties")
old_conn.close()

# Create fresh database using current schema
print("\n[2/5] Creating fresh database...")
new_conn = sqlite3.connect(NEW_DB)
new_conn.enable_load_extension(True)
sqlite_vec.load(new_conn)
new_conn.enable_load_extension(False)

# Drop everything
new_conn.execute('DROP TABLE IF EXISTS memories')
try:
    new_conn.execute('DROP TABLE IF EXISTS vec_memories')
    new_conn.execute('DROP TABLE IF EXISTS memory_embeddings')
except:
    pass

# Create tables exactly as initialize() does
new_conn.execute('''
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        hash TEXT UNIQUE NOT NULL,
        content TEXT NOT NULL,
        tags TEXT,
        metadata TEXT,
        created_at REAL,
        updated_at REAL,
        created_at_iso TEXT,
        updated_at_iso TEXT
    )
''')

new_conn.execute('''
    CREATE VIRTUAL TABLE memory_embeddings USING vec0(
        content_embedding FLOAT[384]
    )
''')

new_conn.execute('CREATE INDEX idx_hash ON memories(hash)')
new_conn.execute('CREATE INDEX idx_created_at ON memories(created_at)')
print("✅ Schema created")

# Load embedding model
print("\n[3/5] Loading embedding model...")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("✅ Loaded: all-MiniLM-L6-v2")

# Store memories exactly as store() would (with deduplication)
print("\n[4/5] Storing memories...")
stored_count = 0
skipped_count = 0
seen_hashes = set()

for i, mem in enumerate(memories, 1):
    # Generate hash using actual codebase function with cleaned metadata
    content_hash = generate_content_hash(
        mem['content'],
        mem['tags'],
        mem['metadata']  # Uses cleaned metadata (no 'tags' or 'type')
    )

    # Skip duplicates (same as store() does)
    if content_hash in seen_hashes:
        skipped_count += 1
        continue

    seen_hashes.add(content_hash)

    # Generate UUID (like store() does)
    memory_id = str(uuid.uuid4())

    # Prepare tags string (like store() does)
    tags_str = ",".join(mem['tags']) if mem['tags'] else ""

    # Prepare metadata string (like store() does)
    # If metadata is empty after cleaning, save as "{}"
    metadata_str = json.dumps(mem['metadata']) if mem['metadata'] else "{}"

    # Generate embedding (like store() does)
    embedding = model.encode(mem['content']).tolist()

    # Insert into memories (like store() does)
    new_conn.execute('''
        INSERT INTO memories (
            id, hash, content, tags, metadata,
            created_at, updated_at, created_at_iso, updated_at_iso
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        memory_id,
        content_hash,
        mem['content'],
        tags_str,
        metadata_str,
        mem['created_at'],
        mem['updated_at'],
        mem['created_at_iso'],
        mem['updated_at_iso']
    ))

    # Insert into embeddings (like store() does)
    new_conn.execute('''
        INSERT INTO memory_embeddings (content_embedding)
        VALUES (?)
    ''', (serialize_f32(embedding),))

    stored_count += 1

    if stored_count % 100 == 0:
        print(f"  Stored {stored_count}/{len(memories)} memories...")

new_conn.commit()
print(f"✅ Stored {stored_count} memories")
if skipped_count > 0:
    print(f"   Skipped {skipped_count} duplicates (same hash after metadata cleaning)")

# Verify
print("\n[5/5] Verifying...")
mem_count = new_conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
emb_count = new_conn.execute('SELECT COUNT(*) FROM memory_embeddings').fetchone()[0]

# Verify rowid alignment
aligned = new_conn.execute('''
    SELECT COUNT(*)
    FROM memories m, memory_embeddings e
    WHERE m.rowid = e.rowid
''').fetchone()[0]

# Verify metadata cleaning
sample = new_conn.execute('SELECT metadata FROM memories LIMIT 10').fetchall()
print("\n  Metadata verification (first 10):")
clean_count = 0
dirty_count = 0
empty_count = 0
for row in sample:
    meta = json.loads(row[0])
    has_bad = 'tags' in meta or 'type' in meta
    if has_bad:
        dirty_count += 1
        print(f"    ❌ DIRTY: {meta}")
    elif not meta:
        empty_count += 1
        print(f"    ✅ EMPTY: {{}}")
    else:
        clean_count += 1
        print(f"    ✅ CLEAN: {meta}")

new_conn.close()

print("\n" + "=" * 80)
if dirty_count == 0 and mem_count == emb_count == aligned:
    print("MIGRATION SUCCESSFUL!")
else:
    print("MIGRATION COMPLETED WITH ISSUES!")
print("=" * 80)
print(f"✅ {mem_count} memories")
print(f"✅ {emb_count} embeddings")
print(f"✅ {aligned} rowids aligned")
print(f"✅ Metadata: {clean_count} clean, {empty_count} empty, {dirty_count} dirty")
print(f"✅ Backup: {backup_path}")
print("\nDatabase rebuilt exactly as current code would create it.")
print("Restart MCP server to test!")
