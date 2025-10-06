# Delete API Reference - MCP Memory Service

**Last Updated**: 2025-10-06
**Version**: 2.0 (Optimized)

---

## Overview

The MCP Memory Service provides **2 deletion methods** that cover all use cases:

1. **`delete_memory`** - Delete by hash (single or bulk)
2. **`delete_by_tag`** - Delete by tags (AND/OR logic)

---

## 1. delete_memory

**Purpose**: Delete one or more memories by their hash identifier

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `hash` | string OR array | ✅ Yes | Single hash or array of hashes to delete |

### Examples

**Single Deletion**:
```json
{
  "hash": "a1b2c3d4e5f6..."
}
```

**Bulk Deletion**:
```json
{
  "hash": [
    "hash1...",
    "hash2...",
    "hash3..."
  ]
}
```

### Response Format

**Single deletion success**:
```
Successfully deleted memory with hash 'a1b2c3d4...'
```

**Single deletion failure**:
```
Memory with hash a1b2c3d4... not found
```

**Bulk deletion**:
```
Deleted 8 of 10 memories
Failed to delete 2: hash7..., hash9...
```

### Use Cases

- Delete specific memories after retrieval
- Bulk cleanup after search operations
- Remove memories by exact identifier
- Combine with `recall_by_timeframe` for date-based cleanup

---

## 2. delete_by_tag

**Purpose**: Delete memories by tags with AND/OR filtering logic

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tags` | array | ✅ Yes | - | List of tags to match |
| `match_all` | boolean | ❌ No | `false` | If true, requires ALL tags (AND); if false, requires ANY tag (OR) |

### Examples

**Delete with ANY tag (OR logic - default)**:
```json
{
  "tags": ["temporary", "outdated", "draft"],
  "match_all": false
}
```
Deletes memories that have `temporary` OR `outdated` OR `draft`

**Delete with ALL tags (AND logic)**:
```json
{
  "tags": ["urgent", "important", "archived"],
  "match_all": true
}
```
Deletes memories that have `urgent` AND `important` AND `archived`

### Response Format

**Success**:
```
Successfully deleted 15 memories with any tag: temporary, outdated, draft
```

**No matches**:
```
No memories found with all tags: urgent, important, archived
```

### Use Cases

- Cleanup temporary/draft memories
- Remove tagged test data
- Bulk deletion by category
- Project-specific cleanup (e.g., delete all `PROJECT:old-project` memories)

---

## Common Workflows

### 1. Time-Based Cleanup (2-step process)

**Goal**: Delete all memories from January 2024

```python
# Step 1: Search for memories in timeframe
results = recall_by_timeframe(
    start_date="2024-01-01",
    end_date="2024-01-31"
)

# Step 2: Extract hashes and delete
hashes = [memory.hash for memory in results]
delete_memory(hash=hashes)
```

**Rationale**: Deliberate 2-step process prevents accidental bulk deletions

---

### 2. Conditional Tag-Based Cleanup

**Goal**: Delete temporary memories older than a certain date

```python
# Step 1: Find temporary memories in date range
results = recall_by_timeframe(
    start_date="2024-01-01",
    end_date="2024-06-30"
)

# Step 2: Filter by tag and delete
temp_hashes = [m.hash for m in results if "temporary" in m.tags]
delete_memory(hash=temp_hashes)
```

---

### 3. Multi-Project Cleanup

**Goal**: Delete all memories from multiple old projects

```python
# Option 1: OR logic (any project)
delete_by_tag(
    tags=["PROJECT:legacy-app", "PROJECT:deprecated-service"],
    match_all=false
)

# Option 2: Search then selective delete
results = search_by_tag(
    tags=["PROJECT:legacy-app", "PROJECT:deprecated-service"],
    match_all=false
)
# Review results, then delete
delete_memory(hash=[m.hash for m in results])
```

---

## Migration from Old API

### Removed Tools

| Old Tool | New Approach |
|----------|--------------|
| `delete_by_tags` | Use `delete_by_tag` (exact same) |
| `delete_by_all_tags` | Use `delete_by_tag(match_all=true)` |
| `delete_by_timeframe` | `recall_by_timeframe()` + `delete_memory()` |
| `delete_before_date` | `recall_by_timeframe()` + `delete_memory()` |

### Parameter Changes

| Old | New | Notes |
|-----|-----|-------|
| `content_hash` | `hash` | Simplified parameter name |
| Single string only | String OR array | Added bulk support |

---

## Safety Best Practices

1. **Preview Before Deletion**: Use search/recall tools to preview what will be deleted
2. **Start with AND Logic**: When using tags, `match_all=true` is more restrictive
3. **Use Bulk Carefully**: Review hash lists before bulk deletion
4. **Tag Strategically**: Proper tagging enables safe, targeted deletions
5. **Timeframe in 2 Steps**: Search first, review results, then delete

---

## Performance Considerations

- **Single hash deletion**: O(1) - Direct lookup
- **Bulk hash deletion**: O(n) - Linear with number of hashes
- **Tag-based deletion (OR)**: O(m) - Linear with memories matching any tag
- **Tag-based deletion (AND)**: O(m × t) - Filtered to exact tag matches

**Recommendation**: For large bulk deletions (>100 memories), consider batching into smaller groups.
