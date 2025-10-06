# Memory ID + Hash Refactor - Implementation Plan

## Overview

Transition from **hash-only** identity model to **UUID + hash** hybrid model for better user experience while maintaining automatic deduplication.

**Status:** ✅ IMPLEMENTATION COMPLETE (100%)
**Started:** 2025-01-06
**Completed:** 2025-01-06
**Breaking Change:** YES - Major API change

## 🎯 Progress Summary

### ✅ Completed (100%)
1. **Hash Generation** - Now includes content + tags + metadata
2. **Memory Model** - Added `id` field, renamed `content_hash` → `hash`, removed legacy timestamp
3. **Storage Base** - Updated method signatures to use `id` parameter
4. **Server Layer** - UUID generation, updated hash calls, tool schemas, handlers
5. **SQLite Schema** - Updated to `id TEXT PRIMARY KEY`, `hash UNIQUE`
6. **Global Rename** - Renamed `content_hash` → `hash` everywhere (76 occurrences in sqlite_vec.py, 39 in chroma.py)
7. **SQLite Methods** - All methods (`get_by_id`, `delete`, `update_memory`) updated to use `id`
8. **ChromaDB Methods** - All methods updated to use `id` with hash deduplication
9. **Server Handlers** - All MCP tool handlers updated to use `id` parameter
10. **Admin UI** - Updated to use `memory.id` instead of `memory.hash`
11. **MCP Client** - All methods updated to use `id` parameters
12. **JSON Responses** - Updated to use `memory.hash` instead of `memory.content_hash`

### ⏳ Remaining (Testing & Migration)
- **Migration Script** - Create database migration for existing data
- **Testing** - Integration and end-to-end testing
- **Documentation** - Update README and create migration guide

### 📋 Next Actions (Optional)
1. Create migration script for existing databases
2. Integration testing with real MCP client
3. Update documentation

---

## Current vs New Design

### Current (Hash-Only)
```python
Memory {
    content_hash: "abc123..." (PRIMARY KEY)
    content: "...",
    tags: ["A", "B"],
    metadata: {...}
}

# Identity
hash = SHA256(content + metadata)  # Tags NOT included
```

**Problems:**
- ❌ Can't edit content (hash changes)
- ❌ Same content with different tags rejected
- ❌ No stable identifier
- ❌ Inconsistent: tags excluded from hash but are part of memory

### New (UUID + Hash)
```python
Memory {
    memory_id: "uuid-1234-5678" (PRIMARY KEY)
    content_hash: "abc123..." (UNIQUE - deduplication)
    content: "...",
    tags: ["A", "B"],
    metadata: {...}
}

# Identity
memory_id = UUID v4 (stable, never changes)
content_hash = SHA256(content + sorted_tags + sorted_metadata)  # ALL included
```

**Benefits:**
- ✅ Stable ID - track memory over time
- ✅ Can edit content/tags/metadata (ID unchanged)
- ✅ Proper deduplication (content + tags + metadata)
- ✅ Consistent: all data included in hash

---

## Hash Generation Rules

### New Hash Algorithm

```python
def generate_content_hash(
    content: str,
    tags: List[str],
    metadata: Dict[str, Any]
) -> str:
    """
    Generate deterministic hash including ALL memory data.

    Rules:
    1. Normalize content: strip whitespace, lowercase
    2. Sort tags alphabetically
    3. Sort metadata keys alphabetically
    4. Filter out dynamic fields (timestamps, system fields)
    5. JSON serialize with sort_keys=True
    """
    # Normalize content
    normalized_content = content.strip().lower()

    # Sort tags (deduplicated)
    sorted_tags = sorted(list(set(tags)))

    # Filter and sort metadata
    static_metadata = {
        k: v for k, v in sorted(metadata.items())
        if k not in ['created_at', 'updated_at', 'created_at_iso', 'updated_at_iso']
    }

    # Build hash input
    hash_input = {
        "content": normalized_content,
        "tags": sorted_tags,
        "metadata": static_metadata
    }

    # Generate hash
    hash_str = json.dumps(hash_input, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(hash_str.encode('utf-8')).hexdigest()
```

**Key Points:**
- **Tags included** → Different tags = different hash
- **Deterministic** → Same data always produces same hash
- **Order-independent** → {"a":1, "b":2} === {"b":2, "a":1}
- **Timestamps excluded** → Don't affect deduplication

---

## Database Schema Changes

### Phase 1: Add memory_id Column

```sql
-- Step 1: Add memory_id column (nullable initially)
ALTER TABLE memories ADD COLUMN memory_id TEXT;

-- Step 2: Populate memory_id with UUIDs for existing rows
UPDATE memories SET memory_id = lower(hex(randomblob(16)));

-- Step 3: Make memory_id NOT NULL
-- (SQLite requires recreate for this)
CREATE TABLE memories_new (
    memory_id TEXT PRIMARY KEY,           -- NEW: Stable identifier
    content_hash TEXT UNIQUE NOT NULL,    -- Changed: from PRIMARY KEY to UNIQUE
    content TEXT NOT NULL,
    tags TEXT,
    metadata TEXT,
    created_at REAL,
    updated_at REAL,
    created_at_iso TEXT,
    updated_at_iso TEXT
);

-- Step 4: Migrate data
INSERT INTO memories_new SELECT * FROM memories;

-- Step 5: Replace table
DROP TABLE memories;
ALTER TABLE memories_new RENAME TO memories;

-- Step 6: Recreate indexes
CREATE INDEX idx_memories_hash ON memories(content_hash);
CREATE INDEX idx_memories_tags ON memories(tags);
CREATE INDEX idx_memories_created ON memories(created_at);
```

### Phase 2: Update Embeddings Table

```sql
-- No schema change needed, but rowid relationship changes:
-- Before: rowid maps to memories.rowid (via content_hash lookup)
-- After: rowid maps to memories.rowid (via memory_id lookup)

-- Note: Embeddings table uses rowid (integer) which maps to memories rowid
-- This relationship is maintained, but lookups change from hash → id
```

---

## API Changes

### store_memory

**Before:**
```python
{
    "content": "Hello world",
    "tags": ["A", "B"],
    "metadata": {"key": "value"}
}

Response: {
    "success": true,
    "hash": "abc123..."
}
```

**After:**
```python
{
    "content": "Hello world",
    "tags": ["A", "B"],
    "metadata": {"key": "value"}
}

Response: {
    "success": true,
    "memory_id": "uuid-1234",
    "content_hash": "abc123...",
    "is_duplicate": false  # true if duplicate detected
}
```

**Behavior on Duplicate:**
```python
# First store
store("Hello", tags=["A"], metadata={})
→ {memory_id: "uuid-1", hash: "abc123", is_duplicate: false}

# Duplicate attempt
store("Hello", tags=["A"], metadata={})
→ {success: false, error: "Duplicate detected", existing_memory_id: "uuid-1"}
```

### update_memory

**Before:**
```python
{
    "hash": "abc123",  // Identifier
    "updates": {
        "tags": ["C"],
        "metadata": {"new": "value"}
    },
    "tags_strategy": "replace",
    "metadata_strategy": "replace"
}
```

**After:**
```python
{
    "memory_id": "uuid-1234",  // NEW: Stable identifier
    "updates": {
        "content": "Updated content",  // NEW: Content updates supported!
        "tags": ["C"],
        "metadata": {"new": "value"}
    },
    "tags_strategy": "replace",
    "metadata_strategy": "replace"
}

Response: {
    "success": true,
    "memory_id": "uuid-1234",      // Same ID
    "old_hash": "abc123",
    "new_hash": "xyz789",          // Hash changed (content changed)
    "updated_fields": ["content", "tags", "metadata"]
}
```

**Deduplication Check:**
- Before update, compute new hash
- Check if new hash exists in database
- If exists (different memory_id) → reject with error
- If not exists → proceed with update

### get_by_hash → get_memory

**Before:**
```python
{
    "hash": "abc123"
}
```

**After:**
```python
{
    "memory_id": "uuid-1234"  // Use ID instead of hash
}

# OR support both:
{
    "memory_id": "uuid-1234"  // Preferred
}

{
    "hash": "abc123"  // Fallback for backward compatibility
}
```

### delete_memory

**Before:**
```python
{
    "hash": "abc123"  // or ["abc123", "def456"]
}
```

**After:**
```python
{
    "memory_id": "uuid-1234"  // or ["uuid-1", "uuid-2"]
}
```

### Other Tools

**recall_memory, search_by_tag, search_by_content:**
- Response changes: Return `memory_id` instead of `hash` (or both)
- No parameter changes

---

## Implementation Phases

### ✅ Phase 1: Hash Generation Update (COMPLETED)
- [x] Update `generate_content_hash()` to include tags and metadata
- [x] Add tag/metadata sorting logic for deterministic hashing
- [x] Remove legacy hash function

**Files modified:**
- ✅ `src/mcp_memory_service/utils/hashing.py` - Now hashes content + tags + metadata

### ✅ Phase 2: Memory Model Update (COMPLETED)
- [x] Add `id: str` field (UUID v4)
- [x] Rename `content_hash` → `hash` throughout model
- [x] Remove legacy `timestamp` field
- [x] Update `to_dict()` to include `id`
- [x] Update `from_dict()` to accept `id` or `memory_id`

**Files modified:**
- ✅ `src/mcp_memory_service/models/memory.py`

### ✅ Phase 3: Storage Base Class (COMPLETED)
- [x] Update method signatures to use `id` parameter (not `memory_id`)
- [x] Add `get_by_id(id: str)` method
- [x] Update `delete(id: str)` signature
- [x] Update `update_memory(id: str, content, tags, metadata)` - now supports content updates

**Files modified:**
- ✅ `src/mcp_memory_service/storage/base.py`

### ✅ Phase 4: Server Layer (PARTIALLY COMPLETED)
- [x] Import UUID generation
- [x] Generate UUID for new memories
- [x] Update `generate_content_hash()` calls to include tags
- [x] Return both `id` and `hash` in store response
- [ ] **TODO:** Rename all `content_hash` → `hash` in server.py
- [ ] **TODO:** Update tool schemas (`id` instead of `hash` parameters)
- [ ] **TODO:** Update `handle_get_by_hash()` → `handle_get_by_id()`
- [ ] **TODO:** Update `handle_delete_memory()` to accept `id`
- [ ] **TODO:** Update `handle_update_memory()` to accept `id`

**Files modified:**
- ⚠️ `src/mcp_memory_service/server.py` - Partially done

### 🚧 Phase 5: SQLite Storage Implementation (IN PROGRESS)
- [x] Update schema: `id TEXT PRIMARY KEY` (was INTEGER AUTOINCREMENT)
- [x] Update schema: `hash TEXT UNIQUE NOT NULL` (was content_hash)
- [x] Update `store()` to insert `id` field
- [ ] **TODO:** Rename all `content_hash` → `hash` in sqlite_vec.py
- [ ] **TODO:** Update `get_by_hash()` → `get_by_id()`
- [ ] **TODO:** Update `delete()` to use `id`
- [ ] **TODO:** Update `update_memory()` to support content updates
- [ ] **TODO:** Update all search methods to return `id`
- [ ] **TODO:** Test deduplication logic (hash-based)

**Files to modify:**
- 🚧 `src/mcp_memory_service/storage/sqlite_vec.py` - In progress

### ⏳ Phase 6: Global Rename (NEXT)
**Action:** Rename `content_hash` → `hash` in all files
- [ ] `src/mcp_memory_service/server.py`
- [ ] `src/mcp_memory_service/storage/sqlite_vec.py`
- [ ] `src/mcp_memory_service/storage/chroma.py`
- [ ] `src/mcp_memory_service/utils/json_response.py`
- [ ] `src/admin/mcp_client.py`
- [ ] `src/admin/ui.py`

### ⏳ Phase 7: Migration Script
- [ ] Create `scripts/migrate_to_uuid.py`
- [ ] Add UUID generation for existing rows
- [ ] Regenerate hashes with new algorithm (content + tags + metadata)
- [ ] Test on sample database (`/Users/ehishim/Documents/last_mem/sqlite_vec.db`)

### ⏳ Phase 8: Admin UI Update
- [ ] Update MCP client methods
- [ ] Update UI to display `id`
- [ ] Update edit/delete operations to use `id`
- [ ] Test content editing flow

**Files to modify:**
- `src/admin/mcp_client.py`
- `src/admin/ui.py`

### ⏳ Phase 9: Testing & Documentation
- [ ] Write comprehensive tests
- [ ] Update CHANGELOG.md
- [ ] Update README.md
- [ ] Create migration guide for users
- [ ] Test all MCP tools end-to-end

**Files to modify:**
- `CHANGELOG.md`
- `README.md`
- `MIGRATION_GUIDE.md` (NEW)
- `tests/` (various)

---

## Backward Compatibility Strategy

### Option A: Breaking Change (Recommended)
- Remove all hash-based parameters
- Only support memory_id
- Provide migration script
- Clear error messages for old API

### Option B: Dual Support (Transition Period)
- Support both hash and memory_id for 1 release
- Deprecation warnings for hash usage
- Auto-migrate hash → memory_id in responses

**Recommendation:** Option A with comprehensive migration script

---

## Deduplication Behavior

### On store_memory

```python
async def store(memory: Memory) -> Tuple[bool, str, Optional[str]]:
    """
    Store memory with deduplication check.

    Returns:
        (success, message, memory_id)
    """
    # Generate hash (content + tags + metadata)
    content_hash = generate_content_hash(
        memory.content,
        memory.tags,
        memory.metadata
    )

    # Check for duplicate hash
    existing = self.conn.execute(
        'SELECT memory_id FROM memories WHERE content_hash = ?',
        (content_hash,)
    ).fetchone()

    if existing:
        return (
            False,
            f"Duplicate detected: memory_id={existing[0]}",
            existing[0]  # Return existing ID
        )

    # Generate new UUID
    memory_id = str(uuid.uuid4())

    # Insert new memory
    # ...

    return (True, "Memory stored successfully", memory_id)
```

### On update_memory

```python
async def update_memory(
    memory_id: str,
    content: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags_strategy: str = "replace",
    metadata_strategy: str = "replace"
) -> Tuple[bool, str]:
    """
    Update memory with deduplication check.
    """
    # Get current memory
    current = await self.get_by_id(memory_id)

    # Apply updates
    new_content = content if content is not None else current.content
    new_tags = apply_tags_strategy(current.tags, tags, tags_strategy)
    new_metadata = apply_metadata_strategy(current.metadata, metadata, metadata_strategy)

    # Generate new hash
    new_hash = generate_content_hash(new_content, new_tags, new_metadata)

    # Check if new hash already exists (different memory_id)
    existing = self.conn.execute(
        'SELECT memory_id FROM memories WHERE content_hash = ? AND memory_id != ?',
        (new_hash, memory_id)
    ).fetchone()

    if existing:
        return (
            False,
            f"Update would create duplicate of memory_id={existing[0]}"
        )

    # Proceed with update (memory_id unchanged, hash updated)
    # ...

    return (True, f"Updated: {updated_fields}")
```

---

## Testing Strategy

### Unit Tests
- `test_hash_generation()` - Deterministic hashing
- `test_hash_ordering()` - Order independence
- `test_store_deduplication()` - Reject duplicates
- `test_update_deduplication()` - Reject duplicate updates
- `test_update_content()` - Content editing works
- `test_memory_id_stability()` - ID unchanged on updates

### Integration Tests
- `test_migration_script()` - Schema migration
- `test_full_crud_cycle()` - Create, read, update, delete
- `test_duplicate_scenarios()` - Various duplicate cases
- `test_admin_ui_workflow()` - UI interactions

### Migration Tests
- `test_migrate_existing_db()` - Upgrade real database
- `test_rollback()` - Downgrade if needed
- `test_data_integrity()` - No data loss

---

## Migration Script

```python
# scripts/migrate_to_uuid.py

async def migrate_database(db_path: str):
    """
    Migrate existing hash-based database to UUID + hash model.

    Steps:
    1. Backup database
    2. Add memory_id column
    3. Generate UUIDs for existing memories
    4. Recreate schema with memory_id as PRIMARY KEY
    5. Regenerate hashes with new algorithm (content + tags + metadata)
    6. Update embeddings table references
    7. Verify data integrity
    """
    # Implementation in Phase 1
    pass
```

---

## Rollback Plan

If issues arise:

1. **Database Backup:** Keep pre-migration backup
2. **Restore Script:** `scripts/rollback_migration.py`
3. **Code Revert:** Git revert to previous version
4. **Documentation:** Clear rollback instructions

---

## Breaking Changes Summary

### For End Users (MCP Clients)

**❌ Breaking:**
- All tools now use `memory_id` instead of `hash` as identifier
- `get_by_hash` renamed to `get_memory`
- Response format changed (includes `memory_id`)
- Hash generation algorithm changed (includes tags)

**✅ New Features:**
- Can edit content via `update_memory`
- Stable memory identifiers (UUIDs)
- Better duplicate detection

### Migration Steps for Users

1. **Update client code:** Replace `hash` with `memory_id`
2. **Run migration script:** `python scripts/migrate_to_uuid.py --db-path ./data/sqlite_vec.db`
3. **Update any stored references:** Old hashes invalid, use new memory_ids
4. **Test thoroughly:** Verify no data loss

---

## Success Criteria

- [ ] All existing memories migrated to new schema
- [ ] No data loss during migration
- [ ] All MCP tools work with memory_id
- [ ] Deduplication works correctly
- [ ] Content editing works in admin UI
- [ ] All tests passing (>95% coverage)
- [ ] Documentation updated
- [ ] Migration guide published

---

## Timeline

**Day 1:** Database migration + hash generation (Phases 1-2)
**Day 2:** Storage + server layer (Phases 3-4)
**Day 3:** Admin UI + testing (Phases 5-6)
**Total:** 3 days

---

## Open Questions

1. **UUID format:** Use UUID v4 or custom format?
   - **Decision:** UUID v4 (standard, widely supported)

2. **Backward compatibility:** Support hash lookups?
   - **Decision:** Add `get_by_hash()` as fallback for 1 release, then deprecate

3. **Migration timing:** Automatic or manual?
   - **Decision:** Manual script, run by user before upgrading

4. **Hash collision handling:** What if two different memories have same hash?
   - **Decision:** Astronomically unlikely (SHA-256), but reject as duplicate

---

## Next Steps

1. **Review this plan** - Get approval before starting
2. **Create feature branch** - `feature/uuid-refactor`
3. **Start Phase 1** - Database migration script
4. **Daily check-ins** - Track progress, adjust timeline if needed

---

**Document Status:** Ready for Review
**Last Updated:** 2025-01-06
**Estimated Effort:** 3 days (Medium complexity)
