# Admin UI JSON Responses & Pagination Implementation Plan

**Date:** 2025-10-06
**Previous Work:** Admin UI refactored to use HTTP/MCP communication (see `ADMIN_UI_REFACTOR_PLAN.md`)
**Current Issue:** MCP server returns plain text responses instead of structured JSON, blocking admin UI functionality

---

## Problem Statement

### Current Behavior (Broken)
```
MCP Server Response:
{
  "content": [{
    "type": "text",
    "text": "Found 2 memories:\n\n1. Memory content...\n2. Memory content..."
  }],
  "isError": false
}
```

Admin client `_parse_memories()` cannot parse this formatted text string.

### Required Behavior
```json
{
  "content": [{
    "type": "text",
    "text": "{\"success\": true, \"memories\": [...], \"pagination\": {...}}"
  }]
}
```

Admin client receives structured JSON that can be parsed into Memory objects.

---

## Requirements Summary

### 1. JSON Response Format ✅
**Goal:** All MCP tools return properly structured JSON instead of formatted text

**Affected Tools:**
- `recall_memory` - Semantic search
- `search_by_tag` - Tag filtering
- `search_by_content` - Text search
- `get_by_hash` - Single memory retrieval
- `update_memory` - Update operations
- `delete_memory` - Delete operations
- `delete_by_tag` - Batch delete
- `check_memory_health` - System health
- `backup_memory` - Backup operations

### 2. Pagination Support ✅
**Goal:** Add limit/offset parameters to search tools

**Affected Tools:**
- `recall_memory` (semantic search)
- `search_by_tag` (tag filtering)
- `search_by_content` (text search)

**Pagination Behavior:**
- No limit/offset → Return all results (backward compatible)
- Limit only → offset defaults to 0
- Limit + offset → Return paginated subset
- Always return pagination metadata (total, has_more, next_offset)

### 3. Document Ingestion ✅
**Decision:** Remove from MCP server entirely

**Rationale:**
- Document ingestion is admin/utility functionality
- Not core to memory storage/retrieval
- Can be handled by admin UI or separate script
- Reduces MCP server complexity

**Impact:**
- Remove `ingest_document` tool (~120 lines)
- Remove `ingest_directory` tool (~130 lines)
- Remove from tools list in `list_tools()`
- Total removal: ~250 lines from server.py

### 4. Installation Scripts ✅
**Goal:** Separate MCP and Admin UI installation

**Changes:**
- Create `install_admin.py` for admin UI setup
- Rename `install.py` → `install_mcp.py`
- Update all documentation references

---

## JSON Response Schemas

### Standard Success Response
```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

### Standard Error Response
```json
{
  "success": false,
  "data": null,
  "error": "Error message description"
}
```

### Paginated Search Response (recall_memory, search_by_tag, search_by_content)
```json
{
  "success": true,
  "memories": [
    {
      "hash": "abc123def456",
      "content": "Memory content text",
      "tags": ["tag1", "tag2"],
      "metadata": {
        "key1": "value1",
        "key2": "value2"
      },
      "created_at": "2025-10-06T12:34:56.789Z",
      "relevance_score": 0.95
    }
  ],
  "pagination": {
    "total": 100,
    "limit": 25,
    "offset": 0,
    "has_more": true,
    "next_offset": 25
  }
}
```

### Single Memory Response (get_by_hash)
```json
{
  "success": true,
  "memory": {
    "hash": "abc123def456",
    "content": "Memory content text",
    "tags": ["tag1", "tag2"],
    "metadata": {...},
    "created_at": "2025-10-06T12:34:56.789Z"
  }
}
```

### Operation Result Response (update_memory, delete_memory, delete_by_tag)
```json
{
  "success": true,
  "affected_count": 5,
  "message": "Successfully deleted 5 memories matching tags: ['project', 'deprecated']"
}
```

### Health Check Response (check_memory_health)
```json
{
  "success": true,
  "health": {
    "total_memories": 1234,
    "database_size_mb": 45.2,
    "embedding_model": "all-MiniLM-L6-v2",
    "storage_backend": "sqlite_vec",
    "database_path": "/app/data/sqlite_vec.db",
    "wal_checkpoint_status": "ok",
    "last_backup": "2025-10-06T10:00:00Z"
  }
}
```

### Backup Response (backup_memory)
```json
{
  "success": true,
  "backup": {
    "backup_path": "/app/data/backups/backup_20251006_123456.db",
    "backup_size_mb": 42.8,
    "created_at": "2025-10-06T12:34:56.789Z",
    "wal_checkpoint_performed": true
  }
}
```

---

## Implementation Plan

### Phase 1: Storage Layer (sqlite_vec.py)

**File:** `src/mcp_memory_service/storage/sqlite_vec.py`

#### 1.1 Add Pagination to semantic_search
```python
async def semantic_search(
    self,
    query: str,
    n_results: int = 5,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None
) -> tuple[List[Memory], int]:
    """
    Semantic search with optional pagination

    Args:
        query: Search query text
        n_results: Number of results (used when limit not provided)
        limit: Maximum results to return (overrides n_results)
        offset: Number of results to skip
        filters: Optional filtering criteria

    Returns:
        (memories, total_count) tuple
    """
    # Generate query embedding
    query_embedding = await self.embed_query(query)

    # Build base query with vector similarity
    base_query = """
        SELECT
            hash, content, tags, metadata, created_at,
            vec_distance_L2(embedding, ?) as distance
        FROM memories
        WHERE 1=1
    """

    # Add filters if provided
    params = [query_embedding]
    if filters:
        # Add filter conditions
        pass

    # Get total count
    count_query = f"SELECT COUNT(*) FROM ({base_query})"
    async with self._get_connection() as conn:
        cursor = await conn.execute(count_query, params)
        total_count = (await cursor.fetchone())[0]

    # Apply pagination
    actual_limit = limit if limit is not None else n_results
    actual_offset = offset if offset is not None else 0

    paginated_query = f"""
        {base_query}
        ORDER BY distance ASC
        LIMIT ? OFFSET ?
    """
    params.extend([actual_limit, actual_offset])

    # Execute and parse results
    async with self._get_connection() as conn:
        cursor = await conn.execute(paginated_query, params)
        rows = await cursor.fetchall()

    memories = [self._row_to_memory(row) for row in rows]
    return memories, total_count
```

#### 1.2 Add Pagination to search_by_tags
```python
async def search_by_tags(
    self,
    tags: List[str],
    match_all: bool = False,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> tuple[List[Memory], int]:
    """
    Search by tags with pagination

    Args:
        tags: List of tags to search for
        match_all: If True, match all tags (AND). If False, match any tag (OR)
        limit: Maximum results to return
        offset: Number of results to skip

    Returns:
        (memories, total_count) tuple
    """
    if not tags:
        return [], 0

    # Build query based on match_all
    if match_all:
        # AND logic: memory must have all tags
        placeholders = " AND ".join([f"tags LIKE ?" for _ in tags])
        where_clause = f"WHERE {placeholders}"
        params = [f"%{tag}%" for tag in tags]
    else:
        # OR logic: memory must have at least one tag
        placeholders = " OR ".join([f"tags LIKE ?" for _ in tags])
        where_clause = f"WHERE {placeholders}"
        params = [f"%{tag}%" for tag in tags]

    # Get total count
    count_query = f"SELECT COUNT(*) FROM memories {where_clause}"
    async with self._get_connection() as conn:
        cursor = await conn.execute(count_query, params)
        total_count = (await cursor.fetchone())[0]

    # Apply pagination if provided
    query = f"SELECT * FROM memories {where_clause} ORDER BY created_at DESC"
    if limit is not None:
        query += f" LIMIT {limit}"
        if offset is not None:
            query += f" OFFSET {offset}"

    # Execute and parse
    async with self._get_connection() as conn:
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

    memories = [self._row_to_memory(row) for row in rows]
    return memories, total_count
```

#### 1.3 Add Pagination to search_by_content
```python
async def search_by_content(
    self,
    search_text: str,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> tuple[List[Memory], int]:
    """
    Substring search in memory content with pagination

    Args:
        search_text: Text to search for in content
        limit: Maximum results to return (default 10 if not specified)
        offset: Number of results to skip

    Returns:
        (memories, total_count) tuple
    """
    where_clause = "WHERE content LIKE ?"
    params = [f"%{search_text}%"]

    # Get total count
    count_query = f"SELECT COUNT(*) FROM memories {where_clause}"
    async with self._get_connection() as conn:
        cursor = await conn.execute(count_query, params)
        total_count = (await cursor.fetchone())[0]

    # Apply pagination
    actual_limit = limit if limit is not None else 10
    actual_offset = offset if offset is not None else 0

    query = f"""
        SELECT * FROM memories {where_clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([actual_limit, actual_offset])

    # Execute and parse
    async with self._get_connection() as conn:
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()

    memories = [self._row_to_memory(row) for row in rows]
    return memories, total_count
```

**Estimated Time:** 2 hours

---

### Phase 2: Server Layer (server.py)

**File:** `src/mcp_memory_service/server.py`

#### 2.1 Add JSON Response Helper
```python
import json
from typing import Dict, Any

def create_json_response(data: Dict[str, Any]) -> list[types.TextContent]:
    """
    Convert dict to JSON string wrapped in TextContent

    Args:
        data: Dictionary to serialize (must be JSON-serializable)

    Returns:
        List containing single TextContent with JSON string
    """
    return [types.TextContent(
        type="text",
        text=json.dumps(data, indent=2, ensure_ascii=False)
    )]

def create_success_response(data: Dict[str, Any]) -> list[types.TextContent]:
    """Create standard success response"""
    return create_json_response({
        "success": True,
        **data
    })

def create_error_response(error: str) -> list[types.TextContent]:
    """Create standard error response"""
    return create_json_response({
        "success": False,
        "data": None,
        "error": error
    })
```

#### 2.2 Update Tool Definitions (list_tools)

**Add pagination parameters to search tools:**
```python
# In list_tools() method around line 434

# recall_memory tool
types.Tool(
    name="recall_memory",
    description="Semantic search with natural language time filtering",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query with optional time expressions"
            },
            "n_results": {
                "type": "integer",
                "description": "Number of results (default 5, used when limit not provided)",
                "default": 5
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (overrides n_results for pagination)"
            },
            "offset": {
                "type": "integer",
                "description": "Number of results to skip (for pagination, default 0)"
            }
        },
        "required": ["query"]
    }
),

# search_by_tag tool
types.Tool(
    name="search_by_tag",
    description="Filter memories by tags with AND/OR logic",
    inputSchema={
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tags to search for"
            },
            "match_all": {
                "type": "boolean",
                "description": "If true, match all tags (AND). If false, match any tag (OR)",
                "default": False
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return"
            },
            "offset": {
                "type": "integer",
                "description": "Number of results to skip (default 0)"
            }
        },
        "required": ["tags"]
    }
),

# search_by_content tool
types.Tool(
    name="search_by_content",
    description="Substring text search in memory content",
    inputSchema={
        "type": "object",
        "properties": {
            "search_text": {
                "type": "string",
                "description": "Text to search for in content"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 10)"
            },
            "offset": {
                "type": "integer",
                "description": "Number of results to skip (default 0)"
            }
        },
        "required": ["search_text"]
    }
)

# REMOVE: ingest_document tool
# REMOVE: ingest_directory tool
```

#### 2.3 Update handle_recall_memory
```python
async def handle_recall_memory(self, arguments: dict) -> list[types.TextContent]:
    """Semantic search with pagination support"""
    try:
        query = arguments.get("query")
        n_results = arguments.get("n_results", 5)
        limit = arguments.get("limit")  # Optional
        offset = arguments.get("offset")  # Optional

        if not query:
            return create_error_response("Query parameter is required")

        # Call storage with pagination
        results, total_count = await self.storage.semantic_search(
            query=query,
            n_results=n_results,
            limit=limit,
            offset=offset
        )

        # Convert memories to dicts
        memories_data = []
        for memory in results:
            mem_dict = {
                "hash": memory.hash,
                "content": memory.content,
                "tags": memory.tags,
                "metadata": memory.metadata,
                "created_at": memory.created_at.isoformat() if memory.created_at else None
            }
            # Add relevance_score if available
            if hasattr(memory, 'relevance_score'):
                mem_dict["relevance_score"] = memory.relevance_score
            memories_data.append(mem_dict)

        # Build pagination metadata
        actual_limit = limit if limit is not None else n_results
        actual_offset = offset if offset is not None else 0
        has_more = (actual_offset + len(results)) < total_count
        next_offset = actual_offset + len(results) if has_more else None

        response = {
            "success": True,
            "memories": memories_data,
            "pagination": {
                "total": total_count,
                "limit": actual_limit,
                "offset": actual_offset,
                "has_more": has_more,
                "next_offset": next_offset
            }
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in recall_memory: {e}")
        return create_error_response(str(e))
```

#### 2.4 Update handle_search_by_tag
```python
async def handle_search_by_tag(self, arguments: dict) -> list[types.TextContent]:
    """Tag filtering with pagination support"""
    try:
        tags = arguments.get("tags", [])
        match_all = arguments.get("match_all", False)
        limit = arguments.get("limit")
        offset = arguments.get("offset")

        if not tags:
            return create_error_response("Tags parameter is required")

        # Call storage with pagination
        results, total_count = await self.storage.search_by_tags(
            tags=tags,
            match_all=match_all,
            limit=limit,
            offset=offset
        )

        # Convert to dicts
        memories_data = [
            {
                "hash": m.hash,
                "content": m.content,
                "tags": m.tags,
                "metadata": m.metadata,
                "created_at": m.created_at.isoformat() if m.created_at else None
            }
            for m in results
        ]

        # Build pagination metadata
        actual_offset = offset if offset is not None else 0
        has_more = limit and (actual_offset + len(results)) < total_count
        next_offset = actual_offset + len(results) if has_more else None

        response = {
            "success": True,
            "memories": memories_data,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": actual_offset,
                "has_more": has_more,
                "next_offset": next_offset
            }
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in search_by_tag: {e}")
        return create_error_response(str(e))
```

#### 2.5 Update handle_search_by_content
```python
async def handle_search_by_content(self, arguments: dict) -> list[types.TextContent]:
    """Content search with pagination support"""
    try:
        search_text = arguments.get("search_text")
        limit = arguments.get("limit", 10)
        offset = arguments.get("offset", 0)

        if not search_text:
            return create_error_response("search_text parameter is required")

        # Call storage with pagination
        results, total_count = await self.storage.search_by_content(
            search_text=search_text,
            limit=limit,
            offset=offset
        )

        # Convert to dicts
        memories_data = [
            {
                "hash": m.hash,
                "content": m.content,
                "tags": m.tags,
                "metadata": m.metadata,
                "created_at": m.created_at.isoformat() if m.created_at else None
            }
            for m in results
        ]

        # Build pagination metadata
        has_more = (offset + len(results)) < total_count
        next_offset = offset + len(results) if has_more else None

        response = {
            "success": True,
            "memories": memories_data,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "next_offset": next_offset
            }
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in search_by_content: {e}")
        return create_error_response(str(e))
```

#### 2.6 Update handle_get_by_hash
```python
async def handle_get_by_hash(self, arguments: dict) -> list[types.TextContent]:
    """Retrieve single memory by hash"""
    try:
        hash_value = arguments.get("hash")

        if not hash_value:
            return create_error_response("hash parameter is required")

        memory = await self.storage.get_by_hash(hash_value)

        if not memory:
            return create_error_response(f"Memory not found: {hash_value}")

        response = {
            "success": True,
            "memory": {
                "hash": memory.hash,
                "content": memory.content,
                "tags": memory.tags,
                "metadata": memory.metadata,
                "created_at": memory.created_at.isoformat() if memory.created_at else None
            }
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in get_by_hash: {e}")
        return create_error_response(str(e))
```

#### 2.7 Update Operation Handlers (update, delete, delete_by_tag)
```python
async def handle_update_memory(self, arguments: dict) -> list[types.TextContent]:
    """Update memory content/tags/metadata"""
    try:
        hash_value = arguments.get("hash")
        content = arguments.get("content")
        tags = arguments.get("tags")
        metadata = arguments.get("metadata")

        if not hash_value:
            return create_error_response("hash parameter is required")

        # Perform update
        success = await self.storage.update_memory(
            hash_value=hash_value,
            content=content,
            tags=tags,
            metadata=metadata
        )

        if success:
            response = {
                "success": True,
                "affected_count": 1,
                "message": f"Memory {hash_value} updated successfully"
            }
        else:
            return create_error_response(f"Failed to update memory: {hash_value}")

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in update_memory: {e}")
        return create_error_response(str(e))

async def handle_delete_memory(self, arguments: dict) -> list[types.TextContent]:
    """Delete memory by hash (single or array)"""
    try:
        hash_value = arguments.get("hash")

        if not hash_value:
            return create_error_response("hash parameter is required")

        # Handle single hash or array
        if isinstance(hash_value, list):
            deleted_count = 0
            for h in hash_value:
                if await self.storage.delete_memory(h):
                    deleted_count += 1

            response = {
                "success": True,
                "affected_count": deleted_count,
                "message": f"Deleted {deleted_count} of {len(hash_value)} memories"
            }
        else:
            success = await self.storage.delete_memory(hash_value)
            response = {
                "success": success,
                "affected_count": 1 if success else 0,
                "message": f"Memory {hash_value} deleted" if success else f"Failed to delete {hash_value}"
            }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in delete_memory: {e}")
        return create_error_response(str(e))

async def handle_delete_by_tag(self, arguments: dict) -> list[types.TextContent]:
    """Delete memories by tags"""
    try:
        tags = arguments.get("tags", [])
        match_all = arguments.get("match_all", False)

        if not tags:
            return create_error_response("tags parameter is required")

        # Get memories to delete
        memories, _ = await self.storage.search_by_tags(tags, match_all)

        # Delete each memory
        deleted_count = 0
        for memory in memories:
            if await self.storage.delete_memory(memory.hash):
                deleted_count += 1

        response = {
            "success": True,
            "affected_count": deleted_count,
            "message": f"Deleted {deleted_count} memories matching tags: {tags}"
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in delete_by_tag: {e}")
        return create_error_response(str(e))
```

#### 2.8 Update Health Check and Backup Handlers
```python
async def handle_check_database_health(self, arguments: dict) -> list[types.TextContent]:
    """Get system health and statistics"""
    try:
        # Gather health metrics
        total_memories = await self.storage.count_memories()
        db_size = await self.storage.get_database_size()

        health_data = {
            "success": True,
            "health": {
                "total_memories": total_memories,
                "database_size_mb": round(db_size / (1024 * 1024), 2),
                "embedding_model": "all-MiniLM-L6-v2",  # From config
                "storage_backend": "sqlite_vec",
                "database_path": str(self.storage.db_path),
                "wal_checkpoint_status": "ok",
                "last_backup": None  # Add if tracking backups
            }
        }

        return create_json_response(health_data)

    except Exception as e:
        logger.error(f"Error in check_database_health: {e}")
        return create_error_response(str(e))

async def handle_backup_memory(self, arguments: dict) -> list[types.TextContent]:
    """Create memory backup with WAL checkpoint"""
    try:
        backup_path = await self.storage.create_backup()
        backup_size = backup_path.stat().st_size if backup_path.exists() else 0

        response = {
            "success": True,
            "backup": {
                "backup_path": str(backup_path),
                "backup_size_mb": round(backup_size / (1024 * 1024), 2),
                "created_at": datetime.now().isoformat(),
                "wal_checkpoint_performed": True
            }
        }

        return create_json_response(response)

    except Exception as e:
        logger.error(f"Error in backup_memory: {e}")
        return create_error_response(str(e))
```

#### 2.9 Remove Document Ingestion Handlers
```python
# DELETE these methods (lines ~1253-1503):
# - async def handle_ingest_document(...)
# - async def handle_ingest_directory(...)

# REMOVE from call_tool() routing (around line 626-633):
# elif tool_name == "ingest_document":
#     return await self.handle_ingest_document(arguments)
# elif tool_name == "ingest_directory":
#     return await self.handle_ingest_directory(arguments)
```

**Estimated Time:** 3-4 hours

---

### Phase 3: Client Layer (mcp_client.py)

**File:** `src/admin/mcp_client.py`

#### 3.1 Update call_tool to Parse JSON
```python
async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute MCP tool and return result

    Args:
        name: Tool name (e.g., 'recall_memory')
        arguments: Tool arguments as dict

    Returns:
        Parsed JSON result dict

    Raises:
        Exception: If tool execution fails or returns error
    """
    await self._ensure_session()

    request = {
        "jsonrpc": "2.0",
        "id": self._next_id(),
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments
        }
    }

    try:
        async with self._session.post(self.base_url, json=request) as response:
            response.raise_for_status()
            result = await response.json()

            if "error" in result:
                raise Exception(f"MCP tool error: {result['error']}")

            # Extract the actual tool result
            tool_result = result.get("result", {})

            # Parse content - expecting JSON string in TextContent
            if "content" in tool_result and isinstance(tool_result["content"], list):
                for item in tool_result["content"]:
                    if item.get("type") == "text":
                        try:
                            # Parse JSON from text content
                            parsed = json.loads(item["text"])

                            # Check for error in response
                            if not parsed.get("success", True):
                                error_msg = parsed.get("error", "Unknown error")
                                raise Exception(f"Tool execution failed: {error_msg}")

                            return parsed

                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse JSON response: {item['text']}")
                            raise Exception(f"Invalid JSON response from tool {name}: {e}")

            # Fallback if no content found
            logger.warning(f"Unexpected result format from tool {name}")
            return tool_result

    except Exception as e:
        logger.error(f"Failed to call tool {name}: {e}")
        raise
```

#### 3.2 Update _parse_memories for New Format
```python
def _parse_memories(self, result: Dict[str, Any]) -> List[Memory]:
    """
    Parse MCP tool result into Memory objects

    Args:
        result: JSON response from tool (already parsed)

    Returns:
        List of Memory objects
    """
    memories = []

    # Check success status
    if not result.get("success", False):
        logger.error(f"Tool call failed: {result.get('error')}")
        return []

    # Extract memories list from response
    if "memories" in result:
        memory_list = result["memories"]
    elif "memory" in result:
        # Single memory response (get_by_hash)
        memory_list = [result["memory"]]
    else:
        logger.warning(f"No memories found in result: {result}")
        return []

    # Parse each memory dict into Memory object
    for mem_data in memory_list:
        try:
            if isinstance(mem_data, dict):
                # Ensure tags is a list
                if "tags" in mem_data and isinstance(mem_data["tags"], str):
                    mem_data["tags"] = [t.strip() for t in mem_data["tags"].split(",") if t.strip()]

                # Create Memory object using from_dict
                memory = Memory.from_dict(mem_data)
                memories.append(memory)

        except Exception as e:
            logger.error(f"Failed to parse memory: {e}, data: {mem_data}")
            continue

    return memories
```

#### 3.3 Update Wrapper Methods with Pagination
```python
async def recall_memory(
    self,
    query: str,
    n_results: int = 5,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> tuple[List[Memory], Dict[str, Any]]:
    """
    Semantic search with natural language time filtering

    Args:
        query: Search query
        n_results: Number of results (default 5)
        limit: Maximum results for pagination (overrides n_results)
        offset: Skip N results (for pagination)

    Returns:
        (memories, pagination_metadata) tuple
    """
    args = {
        'query': query,
        'n_results': n_results
    }

    if limit is not None:
        args['limit'] = limit
    if offset is not None:
        args['offset'] = offset

    result = await self.call_tool('recall_memory', args)
    memories = self._parse_memories(result)
    pagination = result.get('pagination', {})

    return memories, pagination

async def search_by_tag(
    self,
    tags: List[str],
    match_all: bool = False,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> tuple[List[Memory], Dict[str, Any]]:
    """
    Filter memories by tags with AND/OR logic

    Args:
        tags: List of tags to search for
        match_all: If True, match all tags (AND). If False, match any tag (OR)
        limit: Maximum results for pagination
        offset: Skip N results (for pagination)

    Returns:
        (memories, pagination_metadata) tuple
    """
    args = {
        'tags': tags,
        'match_all': match_all
    }

    if limit is not None:
        args['limit'] = limit
    if offset is not None:
        args['offset'] = offset

    result = await self.call_tool('search_by_tag', args)
    memories = self._parse_memories(result)
    pagination = result.get('pagination', {})

    return memories, pagination

async def search_by_content(
    self,
    search_text: str,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> tuple[List[Memory], Dict[str, Any]]:
    """
    Substring text search in memory content

    Args:
        search_text: Text to search for
        limit: Maximum results (default 10)
        offset: Skip N results (default 0)

    Returns:
        (memories, pagination_metadata) tuple
    """
    args = {
        'search_text': search_text
    }

    if limit is not None:
        args['limit'] = limit
    if offset is not None:
        args['offset'] = offset

    result = await self.call_tool('search_by_content', args)
    memories = self._parse_memories(result)
    pagination = result.get('pagination', {})

    return memories, pagination

async def get_by_hash(self, hash: str) -> Optional[Memory]:
    """
    Retrieve specific memory by hash

    Returns:
        Memory object or None if not found
    """
    result = await self.call_tool('get_by_hash', {'hash': hash})
    memories = self._parse_memories(result)
    return memories[0] if memories else None

# update_memory, delete_memory, delete_by_tag remain similar
# but should check result["success"] and return result["affected_count"]
```

**Estimated Time:** 2 hours

---

### Phase 4: UI Layer (ui.py)

**File:** `src/admin/ui.py`

#### 4.1 Update Search Functions with Server-Side Pagination
```python
# In semantic search section (around line 150)
if search_query:
    try:
        # Calculate offset from current page
        offset = (st.session_state.current_page - 1) * st.session_state.page_size

        # Call with server-side pagination
        memories, pagination = asyncio.run(st.session_state.client.recall_memory(
            query=search_query,
            n_results=100,  # Keep high for backward compat
            limit=st.session_state.page_size,
            offset=offset
        ))

        # Use server pagination metadata
        total_count = pagination.get('total', 0)
        has_more = pagination.get('has_more', False)

        st.success(f"Found {total_count} memories (showing {len(memories)})")

        # Display memories
        for memory in memories:
            # ... existing display code ...

        # Pagination controls
        display_pagination_controls(total_count, st.session_state.page_size)

    except Exception as e:
        st.error(f"Search failed: {e}")

# Similar updates for tag search and content search
```

#### 4.2 Add Pagination Control Helper
```python
def display_pagination_controls(total_count: int, page_size: int):
    """Display pagination controls with page navigation"""
    if total_count == 0:
        return

    total_pages = (total_count + page_size - 1) // page_size
    current_page = st.session_state.current_page

    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 2])

    with col1:
        st.metric("Total Results", total_count)

    with col2:
        if st.button("⏮️ First", disabled=current_page <= 1):
            st.session_state.current_page = 1
            st.rerun()

    with col3:
        if st.button("← Prev", disabled=current_page <= 1):
            st.session_state.current_page -= 1
            st.rerun()

    with col4:
        if st.button("Next →", disabled=current_page >= total_pages):
            st.session_state.current_page += 1
            st.rerun()

    with col5:
        st.metric("Page", f"{current_page} / {total_pages}")
```

#### 4.3 Update Error Handling
```python
# Update all try/except blocks to handle new error format
try:
    result = asyncio.run(st.session_state.client.delete_memory(hash))
    if result.get("success"):
        st.success(result.get("message", "Operation successful"))
    else:
        st.error(result.get("error", "Operation failed"))
except Exception as e:
    st.error(f"Error: {e}")
```

**Estimated Time:** 1-2 hours

---

### Phase 5: Installation Scripts

#### 5.1 Create install_admin.py

**File:** `install_admin.py` (new file in project root)

```python
#!/usr/bin/env python3
"""
MCP Memory Service - Admin UI Installer
Sets up Streamlit-based admin interface with HTTP client dependencies
"""

import sys
import subprocess
import platform
from pathlib import Path

def check_python_version():
    """Ensure Python 3.9+"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print("❌ Python 3.9 or higher is required")
        print(f"   Current version: {sys.version}")
        sys.exit(1)
    print(f"✅ Python {version.major}.{version.minor}.{version.micro}")

def create_venv():
    """Create virtual environment for admin UI"""
    venv_path = Path("venv-admin")

    if venv_path.exists():
        print("⚠️  venv-admin already exists, skipping creation")
        return venv_path

    print("📦 Creating venv-admin virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", "venv-admin"], check=True)
    print("✅ Virtual environment created")
    return venv_path

def get_pip_path(venv_path: Path) -> Path:
    """Get pip executable path for the platform"""
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    else:
        return venv_path / "bin" / "pip"

def install_dependencies(venv_path: Path):
    """Install admin UI dependencies"""
    pip_path = get_pip_path(venv_path)

    print("\n📥 Installing admin UI dependencies...")
    subprocess.run([
        str(pip_path), "install", "-r", "requirements-admin.txt"
    ], check=True)
    print("✅ Dependencies installed")

def validate_installation(venv_path: Path):
    """Verify streamlit is installed"""
    python_path = venv_path / "bin" / "python" if platform.system() != "Windows" else venv_path / "Scripts" / "python.exe"

    print("\n🔍 Validating installation...")
    result = subprocess.run([
        str(python_path), "-c", "import streamlit; print(streamlit.__version__)"
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ Streamlit {result.stdout.strip()} installed successfully")
    else:
        print("❌ Streamlit installation validation failed")
        sys.exit(1)

def print_usage():
    """Print usage instructions"""
    print("\n" + "="*60)
    print("🎉 Admin UI installation complete!")
    print("="*60)
    print("\nUsage:")
    print("  ./run_admin.sh                          # Connect to localhost:8030")
    print("  ./run_admin.sh http://mevault:8030/mcp  # Connect to remote server")
    print("\nEnvironment variable:")
    print("  export MCP_SERVER_URL=http://localhost:8030/mcp")
    print("  ./run_admin.sh")
    print("\n" + "="*60)

def main():
    print("🧠 MCP Memory Service - Admin UI Installer")
    print("="*60)

    check_python_version()
    venv_path = create_venv()
    install_dependencies(venv_path)
    validate_installation(venv_path)
    print_usage()

if __name__ == "__main__":
    main()
```

#### 5.2 Rename install.py to install_mcp.py

**Command:**
```bash
git mv install.py install_mcp.py
```

**Update self-references in install_mcp.py:**
```python
# Change all references from "install.py" to "install_mcp.py"
# Example:
print("🧠 MCP Memory Service - MCP Server Installer")
# ... update help text, error messages, etc.
```

#### 5.3 Update Documentation

**README.md:**
```markdown
## Installation

### MCP Server
```bash
python3 install_mcp.py
```

### Admin UI
```bash
python3 install_admin.py
./run_admin.sh http://localhost:8030/mcp
```
```

**ADMIN_UI.md:**
```markdown
## Setup

1. Install admin UI dependencies:
   ```bash
   python3 install_admin.py
   ```

2. Start admin UI:
   ```bash
   ./run_admin.sh http://localhost:8030/mcp
   ```

3. Access at http://localhost:8501
```

**Estimated Time:** 1 hour

---

## Testing Plan

### Phase 6: Comprehensive Testing

#### 6.1 Storage Layer Tests
```bash
# Test pagination in sqlite_vec.py
python3 -m pytest tests/test_sqlite_vec_pagination.py -v

# Manual testing:
# - semantic_search with limit/offset
# - search_by_tags with limit/offset
# - search_by_content with limit/offset
# - Verify total_count accuracy
```

#### 6.2 Server Layer Tests
```bash
# Test JSON responses
python3 scripts/test_mcp_json_responses.py

# Test cases:
# - All tools return valid JSON
# - Error responses have correct format
# - Pagination metadata is accurate
# - No more document ingestion tools
```

#### 6.3 Client Layer Tests
```bash
# Test MCP client parsing
python3 -m pytest tests/test_mcp_client.py -v

# Test cases:
# - Parse success responses
# - Parse error responses
# - Handle pagination metadata
# - Extract memories correctly
```

#### 6.4 UI Integration Tests
```bash
# Start MCP server
python3 scripts/run_memory_server.py

# Start admin UI
./run_admin.sh

# Manual tests:
# - Connect to MCP server
# - Semantic search with pagination
# - Tag search with pagination
# - Content search with pagination
# - Page navigation (first, prev, next, last)
# - CRUD operations (create, read, update, delete)
# - Health check displays correct JSON
# - Backup creates proper response
```

#### 6.5 Error Scenarios
- MCP server not running → Connection error displayed
- Invalid query → Error response parsed correctly
- Network timeout → Graceful error handling
- Large result sets → Pagination works correctly
- Empty result sets → No pagination controls shown

**Estimated Time:** 2-3 hours

---

## Implementation Checklist

### Phase 1: Storage Layer (2 hours)
- [ ] Add pagination to `semantic_search()` in sqlite_vec.py
- [ ] Add pagination to `search_by_tags()` in sqlite_vec.py
- [ ] Add pagination to `search_by_content()` in sqlite_vec.py
- [ ] Add `_get_total_count()` helper method
- [ ] Test pagination with various limit/offset values
- [ ] Verify total_count accuracy

### Phase 2: Server Layer (3-4 hours)
- [ ] Add `create_json_response()` helper function
- [ ] Add `create_success_response()` helper function
- [ ] Add `create_error_response()` helper function
- [ ] Update tool definitions in `list_tools()` (add limit/offset)
- [ ] Update `handle_recall_memory()` for JSON + pagination
- [ ] Update `handle_search_by_tag()` for JSON + pagination
- [ ] Update `handle_search_by_content()` for JSON + pagination
- [ ] Update `handle_get_by_hash()` for JSON
- [ ] Update `handle_update_memory()` for JSON
- [ ] Update `handle_delete_memory()` for JSON
- [ ] Update `handle_delete_by_tag()` for JSON
- [ ] Update `handle_check_database_health()` for JSON
- [ ] Update `handle_backup_memory()` for JSON
- [ ] Remove `handle_ingest_document()` (~120 lines)
- [ ] Remove `handle_ingest_directory()` (~130 lines)
- [ ] Remove ingestion tools from `list_tools()`
- [ ] Remove ingestion routing from `call_tool()`
- [ ] Test all tools return valid JSON

### Phase 3: Client Layer (2 hours)
- [ ] Update `call_tool()` to parse JSON responses
- [ ] Add error checking for `success: false` responses
- [ ] Update `_parse_memories()` for new JSON format
- [ ] Update `recall_memory()` to return pagination metadata
- [ ] Update `search_by_tag()` to return pagination metadata
- [ ] Update `search_by_content()` to return pagination metadata
- [ ] Update `get_by_hash()` for new JSON format
- [ ] Update operation methods to check `success` field
- [ ] Test JSON parsing with various response formats

### Phase 4: UI Layer (1-2 hours)
- [ ] Update semantic search to use server-side pagination
- [ ] Update tag search to use server-side pagination
- [ ] Update content search to use server-side pagination
- [ ] Add `display_pagination_controls()` helper function
- [ ] Remove client-side pagination logic (array slicing)
- [ ] Use server's total_count for page calculations
- [ ] Update error handling to check `success` field
- [ ] Test pagination navigation (first, prev, next, last)
- [ ] Test with various page sizes (25, 50, 100)

### Phase 5: Installation Scripts (1 hour)
- [ ] Create `install_admin.py` in project root
- [ ] Add Python version check (3.9+)
- [ ] Add venv-admin creation
- [ ] Add requirements-admin.txt installation
- [ ] Add streamlit validation
- [ ] Add usage instructions printing
- [ ] Make executable: `chmod +x install_admin.py`
- [ ] Rename `install.py` to `install_mcp.py`
- [ ] Update self-references in install_mcp.py
- [ ] Update README.md installation section
- [ ] Update ADMIN_UI.md setup section
- [ ] Update any other docs mentioning install.py

### Phase 6: Testing (2-3 hours)
- [ ] Test storage layer pagination
- [ ] Test server JSON responses for all tools
- [ ] Test client JSON parsing
- [ ] Test UI pagination controls
- [ ] Test CRUD operations end-to-end
- [ ] Test error scenarios
- [ ] Test with empty result sets
- [ ] Test with large result sets (1000+ memories)
- [ ] Verify no SQLite lock conflicts
- [ ] Verify WAL checkpoint works with new code

---

## File Modifications Summary

### Modified Files
1. **`src/mcp_memory_service/storage/sqlite_vec.py`**
   - Add pagination parameters to 3 search methods
   - Add `_get_total_count()` helper
   - Return (memories, total_count) tuples
   - ~150 lines modified

2. **`src/mcp_memory_service/server.py`**
   - Add JSON response helpers (3 functions)
   - Update 10 tool handlers for JSON responses
   - Update tool definitions in `list_tools()`
   - Remove document ingestion handlers (~250 lines)
   - ~500 lines modified/removed

3. **`src/admin/mcp_client.py`**
   - Update `call_tool()` to parse JSON
   - Update `_parse_memories()` for new format
   - Add pagination return values to 3 methods
   - ~100 lines modified

4. **`src/admin/ui.py`**
   - Update all search operations for server-side pagination
   - Add `display_pagination_controls()` helper
   - Remove client-side pagination logic
   - Update error handling
   - ~150 lines modified

### New Files
1. **`install_admin.py`**
   - Admin UI installer script
   - ~150 lines new

### Renamed Files
1. **`install.py` → `install_mcp.py`**
   - MCP server installer
   - Minor updates to self-references

### Updated Documentation
1. **`README.md`** - Installation instructions
2. **`ADMIN_UI.md`** - Admin UI setup
3. **`CLAUDE.md`** - If referencing install.py

---

## Benefits of This Refactor

### ✅ Fixes Admin UI Blocking Issue
- MCP client can now parse responses correctly
- No more "Error executing tool" messages
- Admin UI becomes fully functional

### ✅ Improves Performance
- Server-side pagination reduces memory usage
- Network transfer reduced for large result sets
- Better scalability for 1000+ memories

### ✅ Better API Design
- Consistent JSON response format across all tools
- Standard error handling
- Pagination metadata included where needed
- Easy to extend with new tools

### ✅ Cleaner Codebase
- Removes document ingestion complexity (~250 lines)
- Separates concerns (MCP vs Admin functionality)
- Standard response helpers reduce duplication
- Easier to maintain and test

### ✅ Developer Experience
- Separate installers for MCP server vs Admin UI
- Clear error messages in JSON format
- Pagination metadata helps build better UIs
- Consistent response parsing across clients

---

## Rollback Plan

If issues arise, rollback is straightforward:

1. **Git Reset**: `git checkout HEAD~1 -- <file>` for each modified file
2. **Keep Backups**: Store current versions before changes
3. **Incremental Rollback**: Can rollback individual phases if needed
4. **No Breaking Changes**: New pagination parameters are optional (backward compatible)

**Minimal Risk**: Changes are isolated to response formatting and pagination, core storage logic unchanged.

---

## Estimated Total Time

| Phase | Task | Time |
|-------|------|------|
| 1 | Storage layer pagination | 2 hours |
| 2 | Server layer JSON responses | 3-4 hours |
| 3 | Client layer JSON parsing | 2 hours |
| 4 | UI layer server-side pagination | 1-2 hours |
| 5 | Installation scripts | 1 hour |
| 6 | Testing and validation | 2-3 hours |
| **Total** | | **11-14 hours** |

**Priority:** High (blocks admin UI functionality)
**Risk:** Low (backward compatible, clean rollback path)
**Complexity:** Medium (multiple layers, but well-defined changes)

---

## Next Steps

1. **Start with Phase 1** (Storage layer) - Foundation for everything else
2. **Then Phase 2** (Server layer) - Enables JSON responses
3. **Then Phase 3** (Client layer) - Can parse new responses
4. **Then Phase 4** (UI layer) - Uses server-side pagination
5. **Then Phase 5** (Installation) - Final polish
6. **End with Phase 6** (Testing) - Comprehensive validation

**Recommended Approach:** Implement one phase completely, test thoroughly, then move to next phase. This allows catching issues early and provides natural rollback points.

---

**Status:** Ready for implementation
**Created:** 2025-10-06
**Session Continuation:** Use this plan to implement all 4 requirements systematically
