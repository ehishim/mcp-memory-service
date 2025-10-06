# Changelog

All notable changes to the MCP Memory Service project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed - Admin UI Complete Integration (2025-01-06)

#### Admin UI HTTP MCP Client Fixes
- **Fixed** asyncio event loop handling for Streamlit compatibility
  - Replaced all `asyncio.run()` calls with `run_async()` helper
  - Creates/manages event loops safely within Streamlit's execution model
  - Resolves "Event loop is closed" errors

- **Fixed** aiohttp session lifecycle management
  - Create fresh session for each operation to avoid event loop conflicts
  - Close sessions in finally blocks after `list_tools()` and `call_tool()`
  - Removed timeout configuration (conflicts with Streamlit event loop wrapper)

- **Fixed** API response field mapping
  - Map API `hash` field to internal `content_hash` field
  - Maintains clean separation: API uses short names, internal uses descriptive names

- **Fixed** ISO timestamp parsing
  - Added `_parse_iso_timestamp()` static method to Memory model
  - Converts ISO string timestamps from API to float timestamps
  - Handles `created_at` and `updated_at` fields automatically

- **Fixed** metadata field extraction
  - Changed from building metadata from remaining fields to direct extraction
  - `metadata = data.get("metadata", {})` instead of filtering all fields
  - Prevents API fields (hash, tags) from leaking into metadata

#### Admin UI Configuration
- **Added** Bearer token authentication support
  - `run_admin.sh` now supports `-s` (server URL) and `-a` (auth token) flags
  - Environment variables: `MCP_SERVER_URL` and `MCP_AUTH_TOKEN`
  - Password-masked token input in UI
  - Authorization header automatically added to all requests

- **Added** SSE (Server-Sent Events) response parsing
  - MCP over HTTP requires SSE format responses
  - Client parses `data:` field from SSE responses
  - Accept header includes both `application/json` and `text/event-stream`

#### MCP Server Response Format Cleanup
- **Refactored** `store_memory` to return consistent JSON
  - Before: Plain text `✅ Message\nHash: abc123`
  - After: JSON `{"success": true, "hash": "abc123"}`
  - Updated tool description: "Returns hash of the created memory"

- **Cleaned** error response format
  - Removed redundant `"data": null` field
  - Error responses now: `{"success": false, "error": "message"}`
  - Consistent across all tools

#### Files Modified
- `src/admin/ui.py` - Event loop handling, session management
- `src/admin/mcp_client.py` - Field mapping, session lifecycle, SSE parsing
- `run_admin.sh` - CLI flags for server URL and auth token
- `src/mcp_memory_service/models/memory.py` - ISO timestamp parsing, metadata extraction
- `src/mcp_memory_service/server.py` - store_memory JSON response
- `src/mcp_memory_service/utils/json_response.py` - Error response cleanup

---

### Added - Phase 5: Installation Scripts Separation (COMPLETED 2025-10-06)

#### New Installation Script
- **Created** `install_admin.py` - Dedicated Admin UI installer
  - Python version check (3.9+)
  - Creates `venv-admin` virtual environment
  - Installs dependencies from `requirements-admin.txt`
  - Validates Streamlit installation
  - Prints usage instructions for `run_admin.sh`

#### Renamed Installation Script
- **Renamed** `install.py` → `install_mcp.py` - MCP server installer
  - Updated all self-references throughout the file
  - Clear separation between MCP server and Admin UI installation

#### Benefits
- **Cleaner separation**: MCP server vs Admin UI dependencies
- **Faster setup**: Install only what you need
- **Easier maintenance**: Isolated dependency management

#### Files Modified (Phase 5)
- `install_admin.py` (NEW - ~120 lines)
- `install.py` → `install_mcp.py` (RENAMED - references updated)

---

### Added - Phase 4: UI Layer Server-Side Pagination (COMPLETED 2025-10-06)

#### ui.py - Server-Side Pagination Implementation
- **Updated** all search operations to use server-side pagination
  - `List All`: Uses `recall_memory()` with `limit` and `offset` parameters
  - `Semantic Search`: Passes `limit` and `offset` for paginated results
  - `Search by Tags`: Server-side pagination with `limit`/`offset`
  - `Search by Content`: Server-side pagination support

- **Removed** client-side pagination logic
  - No more downloading all results and slicing arrays
  - Uses server's `total` count from pagination metadata
  - Calculates offset from current page: `offset = (page - 1) * page_size`

- **Updated** error handling to check `success` field
  - Health check now validates `success` and displays `health` data
  - Backup operation checks `success` and displays `backup` data
  - Consistent error message extraction from `error` field

#### Benefits
- **Better performance**: No more loading 1000+ memories into client
- **Accurate counts**: Total count from server, not client-side array length
- **Memory efficient**: Only loads current page of results
- **Scalable**: Handles large datasets without client-side memory issues

#### Files Modified (Phase 4)
- `src/admin/ui.py` (~150 lines updated)

---

### Added - Phase 3: Client Layer JSON Parsing & Pagination (COMPLETED 2025-10-06)

#### mcp_client.py - JSON Response Handling
- **Updated** `call_tool()` method to parse and validate JSON responses
  - Parses JSON from TextContent wrapper
  - Checks `success` field in all responses
  - Raises exceptions for `success: false` responses with error messages
  - Improved error handling with descriptive messages

- **Updated** `_parse_memories()` for new JSON response format
  - Handles `{"success": true, "memories": [...]}` format
  - Handles `{"success": true, "memory": {...}}` for single memory
  - Returns empty list for `success: false` responses
  - Simplified tag parsing (handles both string and list formats)

- **Updated** wrapper methods to support pagination and return tuples
  - `recall_memory()` - Returns `(memories, pagination)` tuple
  - `search_by_tag()` - Returns `(memories, pagination)` tuple
  - `search_by_content()` - Returns `(memories, pagination)` tuple
  - All methods accept optional `limit` and `offset` parameters

- **Maintained** `get_by_hash()` - Returns single `Memory` or `None`

#### Benefits
- **Consistent parsing**: All responses follow same JSON structure
- **Better error handling**: Server errors properly propagated to UI
- **Pagination support**: Returns pagination metadata for UI controls
- **Type safety**: Clear return types with tuples for paginated results

#### Files Modified (Phase 3)
- `src/admin/mcp_client.py` (~100 lines modified)

---

### Added - Phase 2: Server Layer JSON Responses & Pagination (COMPLETED 2025-10-06)

#### Tool Definition Updates
- **Updated** `recall_memory` tool schema
  - Removed legacy `n_results` parameter
  - Added `limit` parameter (optional - returns all results if not specified)
  - Added `offset` parameter (optional, default 0)
  - Updated description to mention pagination support

- **Updated** `search_by_tag` tool schema
  - Added `limit` parameter (optional - returns all results if not specified)
  - Added `offset` parameter (optional, default 0)
  - Updated description to mention pagination support

- **Updated** `search_by_content` tool schema
  - Made `limit` parameter optional (was required with default 10)
  - Added `offset` parameter (optional, default 0)
  - Updated description to mention pagination support

#### Handler Updates - All Now Return Structured JSON

- **Updated** `handle_recall_memory()`
  - Removed `n_results` parameter handling
  - Returns all results when `limit` is None
  - Uses `create_paginated_response()` helper
  - Returns: `{success, memories, pagination}`

- **Updated** `handle_search_by_tag()`
  - Returns structured JSON with pagination
  - Returns: `{success, memories, pagination}`

- **Updated** `handle_search_by_content()`
  - Removed default limit of 10
  - Returns all results when `limit` is None
  - Returns: `{success, memories, pagination}`

- **Updated** `handle_get_by_hash()`
  - Returns: `{success, memory}`

- **Updated** `handle_update_memory()`
  - Returns: `{success, affected_count, message}`

- **Updated** `handle_delete_memory()`
  - Handles single hash or array
  - Returns: `{success, affected_count, message}`

- **Updated** `handle_delete_by_tag()`
  - Returns: `{success, affected_count, message}`

- **Updated** `handle_check_memory_health()`
  - Returns: `{success, health}` with nested validation, statistics, and performance data

- **Updated** `handle_backup_memory()`
  - Returns: `{success, backup}` with backup_path, size, timestamp, WAL checkpoint status

#### Response Structure Design Decision

**Chose domain-specific top-level fields** over generic `data` wrapper:
```json
{
  "success": true,
  "memories": [...],
  "pagination": {...}
}
```

**Rationale:**
- Cleaner client code (less nesting)
- Self-documenting field names
- Consistent with existing Phase 1-2 implementations
- Industry precedent (GitHub API, Elasticsearch)
- Follows the detailed plan schemas exactly

**Consistency maintained:**
- All responses have `success: true/false` field
- Error responses have `error` field
- Success responses have domain-specific data fields

#### Files Modified (Phase 2)
- `src/mcp_memory_service/server.py` (~400 lines updated)
  - 8 handler methods converted to JSON responses
  - Tool definitions updated with pagination parameters
  - Removed `n_results` legacy parameter

---

### Added - Phase 2: JSON Response Helpers & Server Refactoring (2025-10-06)

#### New JSON Response Utility Module
- **Created** `src/mcp_memory_service/utils/json_response.py` - Dedicated module for JSON formatting
  - `create_json_response()` - Base JSON wrapper for TextContent
  - `create_success_response()` - Standard success response with `success: true`
  - `create_error_response()` - Standard error response with `success: false`
  - `create_paginated_response()` - Paginated responses with metadata
  - `create_single_memory_response()` - Single memory retrieval
  - `create_operation_response()` - Update/delete operation results
  - `create_health_response()` - Health check data
  - `create_backup_response()` - Backup operation results
  - `memory_to_dict()` - Memory object serialization
  - `query_result_to_dict()` - MemoryQueryResult serialization

#### server.py - Cleaner Architecture
- **Imported** JSON response helpers from utils module
- **Removed** duplicate helper functions from server.py (moved to utils)
- **Updated** `handle_recall_memory()` to use `create_paginated_response()`
  - Now returns structured JSON instead of formatted text
  - Supports `limit` and `offset` parameters for pagination
  - Includes pagination metadata (total, has_more, next_offset)

#### Benefits
- **Cleaner code**: Server handlers focus on logic, not formatting
- **Consistency**: All responses follow same JSON structure
- **Reusability**: Helpers can be used across all tool handlers
- **Maintainability**: Single source of truth for response formatting

#### Files Modified
- `src/mcp_memory_service/utils/json_response.py` (NEW - ~200 lines)
- `src/mcp_memory_service/server.py` (imports updated, ~50 lines cleaner)

---

### Added - Phase 1: Storage Layer Pagination (2025-10-06)

#### sqlite_vec.py - Pagination Support for Search Methods
- **retrieve()** method now supports pagination with `limit` and `offset` parameters
  - Returns tuple `(List[MemoryQueryResult], int)` with results and total count
  - `limit` parameter overrides `n_results` for pagination control
  - `offset` parameter enables skipping results for page navigation
  - Backward compatible: uses `n_results` when `limit` is None
  - Updated vector search to handle pagination with enlarged k-value for candidates

- **search_by_tags()** method now supports pagination with `limit` and `offset` parameters
  - Returns tuple `(List[Memory], int)` with results and total count
  - Supports both AND/OR operations with pagination
  - Executes COUNT query first to get total results
  - Appends LIMIT/OFFSET to SQL only when provided

- **search_by_content()** method now supports pagination with `limit` and `offset` parameters
  - Returns tuple `(List[Memory], int)` with results and total count
  - Default `limit` of 10 when not specified (preserves original behavior)
  - Executes COUNT query to get total matching results
  - Enhanced logging with pagination metadata

#### Benefits
- Enables server-side pagination for admin UI
- Reduces memory usage for large result sets
- Provides total count for UI pagination controls
- Maintains backward compatibility with existing code

#### Files Modified
- `src/mcp_memory_service/storage/sqlite_vec.py` (3 methods updated, ~150 lines modified)

---

## Version History

This changelog tracks the JSON responses and pagination refactor implementation (see `ADMIN_UI_JSON_RESPONSES_PLAN.md` for full details).
