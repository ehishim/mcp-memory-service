# Admin UI Refactor Plan: MCP HTTP Client Architecture

**Date:** 2025-10-06
**Goal:** Refactor admin UI to connect to MCP server via HTTP/SSE instead of direct SQLite access

---

## Architecture Understanding

### Current Setup (Has Concurrency Issues)
```
┌─────────────┐
│  Admin UI   │ ──────> SQLite DB (Direct access)
└─────────────┘           ▲
                          │
┌─────────────┐          │
│ MCP Server  │ ─────────┘
└─────────────┘
```

**Problems:**
- Multiple writers to SQLite (MCP + Admin UI)
- Potential `SQLITE_BUSY` errors
- WAL checkpoint concerns
- Stale data without manual refresh

### Target Setup (Clean Architecture)
```
┌─────────────┐
│  Admin UI   │ ──HTTP/SSE──> ┌─────────────┐
└─────────────┘                │ MCP Server  │ ──> SQLite DB
                               │ (Single     │     (Single writer)
┌─────────────┐                │  Writer)    │
│ Claude Code │ ──MCP──────>   └─────────────┘
└─────────────┘
```

**Current MCP Server:**
- Running at: `http://mevault:8030/mcp`
- Uses mcp-proxy wrapper (stdio → HTTP)
- Base server: `src/mcp_memory_service/server.py`

---

## Available MCP Tools (from server.py:434-584)

1. **store_memory** - Store memory with tags/metadata
2. **recall_memory** - Semantic search with time filtering
3. **search_by_tag** - Filter by tags (AND/OR logic)
4. **delete_memory** - Delete by hash (single or array)
5. **delete_by_tag** - Delete by tags
6. **get_by_hash** - Retrieve specific memory
7. **search_by_content** - Substring text search
8. **update_memory** - Update content/tags/metadata
9. **check_memory_health** - System health & stats
10. **backup_memory** - Create backup with WAL checkpoint

---

## Implementation Plan

### Phase 1: Create MCP HTTP Client

**New File:** `src/admin/mcp_client.py`

```python
"""
MCP HTTP/SSE Client for Admin UI
Communicates with MCP server via HTTP/JSON-RPC
"""

import requests
import json
from typing import List, Dict, Any, Optional
from mcp_memory_service.models.memory import Memory


class MCPHttpClient:
    """HTTP client for MCP server communication"""

    def __init__(self, base_url: str, timeout: int = 30):
        """
        Initialize MCP client

        Args:
            base_url: MCP server URL (e.g., http://mevault:8030/mcp)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to MCP server"""
        try:
            tools = await self.list_tools()
            return True, f"Connected - {len(tools)} tools available"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Get available MCP tools from server"""
        # Implement JSON-RPC call to list_tools
        pass

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute MCP tool and return result

        Args:
            name: Tool name (e.g., 'recall_memory')
            arguments: Tool arguments as dict

        Returns:
            Tool execution result
        """
        # Implement JSON-RPC call_tool
        pass

    # High-level tool wrappers for admin UI

    async def recall_memory(self, query: str, n_results: int = 5) -> List[Memory]:
        """Semantic search with natural language time filtering"""
        result = await self.call_tool('recall_memory', {
            'query': query,
            'n_results': n_results
        })
        return self._parse_memories(result)

    async def search_by_tag(self, tags: List[str], match_all: bool = False) -> List[Memory]:
        """Filter memories by tags with AND/OR logic"""
        result = await self.call_tool('search_by_tag', {
            'tags': tags,
            'match_all': match_all
        })
        return self._parse_memories(result)

    async def get_by_hash(self, hash: str) -> Optional[Memory]:
        """Retrieve specific memory by hash"""
        result = await self.call_tool('get_by_hash', {'hash': hash})
        memories = self._parse_memories(result)
        return memories[0] if memories else None

    async def search_by_content(self, search_text: str, limit: int = 10) -> List[Memory]:
        """Substring text search in memory content"""
        result = await self.call_tool('search_by_content', {
            'search_text': search_text,
            'limit': limit
        })
        return self._parse_memories(result)

    async def update_memory(
        self,
        hash: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Update memory content/tags/metadata"""
        args = {'hash': hash}
        if content is not None:
            args['content'] = content
        if tags is not None:
            args['tags'] = tags
        if metadata is not None:
            args['metadata'] = metadata

        return await self.call_tool('update_memory', args)

    async def delete_memory(self, hash: str | List[str]) -> Dict[str, Any]:
        """Delete memory by hash (single or array)"""
        return await self.call_tool('delete_memory', {'hash': hash})

    async def delete_by_tag(self, tags: List[str], match_all: bool = False) -> Dict[str, Any]:
        """Delete memories by tags"""
        return await self.call_tool('delete_by_tag', {
            'tags': tags,
            'match_all': match_all
        })

    async def check_memory_health(self) -> Dict[str, Any]:
        """Get system health and statistics"""
        return await self.call_tool('check_memory_health', {})

    async def backup_memory(self) -> Dict[str, Any]:
        """Create memory backup with WAL checkpoint"""
        return await self.call_tool('backup_memory', {})

    def _parse_memories(self, result: Dict[str, Any]) -> List[Memory]:
        """Parse MCP tool result into Memory objects"""
        # Parse JSON response and convert to Memory objects
        pass
```

---

### Phase 2: Pagination Strategy

**Decision: Client-Side Pagination (Initial Implementation)**

**Rationale:**
- Simpler to implement (no server changes)
- Sufficient for small-medium datasets (<1000 memories)
- Can migrate to server-side later if needed

**Implementation:**
```python
# In ui.py session state
if 'page_size' not in st.session_state:
    st.session_state.page_size = 25
if 'current_page' not in st.session_state:
    st.session_state.current_page = 1

# Fetch all results, paginate in UI
all_memories = await client.search_by_tag(tags)
total_count = len(all_memories)
total_pages = (total_count + page_size - 1) // page_size

start_idx = (current_page - 1) * page_size
end_idx = start_idx + page_size
page_memories = all_memories[start_idx:end_idx]
```

**Future: Server-Side Pagination**
- Add `offset` and `limit` to MCP tools
- Modify tool handlers in server.py
- Return `{"memories": [...], "total": 100, "offset": 0, "limit": 25}`

---

### Phase 3: Refactor Admin UI

**File:** `src/admin/ui.py`

**Major Changes:**

1. **Replace Imports**
   ```python
   # OLD
   from mcp_memory_service.storage.sqlite_vec import SqliteVecMemoryStorage
   from mcp_memory_service.config import SQLITE_VEC_PATH, BACKUPS_PATH

   # NEW
   from admin.mcp_client import MCPHttpClient
   ```

2. **Update Connection Section**
   ```python
   # Sidebar - MCP Server Connection
   with st.sidebar:
       st.header("⚙️ Configuration")

       # MCP server URL from env var or user input
       default_url = os.environ.get('MCP_SERVER_URL', 'http://localhost:8030/mcp')
       mcp_url = st.text_input(
           "MCP Server URL",
           value=st.session_state.get('mcp_url', default_url),
           help="URL of the running MCP server"
       )

       if st.button("🔌 Connect"):
           try:
               client = MCPHttpClient(mcp_url)
               success, message = asyncio.run(client.test_connection())
               if success:
                   st.session_state.client = client
                   st.session_state.mcp_url = mcp_url
                   st.success(f"✅ {message}")
               else:
                   st.error(f"❌ {message}")
           except Exception as e:
               st.error(f"❌ Connection failed: {e}")
   ```

3. **Replace All Storage Operations**
   ```python
   # OLD
   memories = asyncio.run(st.session_state.storage.search_by_tags(tags))

   # NEW
   memories = asyncio.run(st.session_state.client.search_by_tag(tags))
   ```

4. **Add Pagination Controls**
   ```python
   # After displaying results
   col1, col2, col3, col4 = st.columns([2, 1, 1, 2])

   with col1:
       st.metric("Total Results", total_count)

   with col2:
       if st.button("← Previous", disabled=current_page <= 1):
           st.session_state.current_page -= 1
           st.rerun()

   with col3:
       if st.button("Next →", disabled=current_page >= total_pages):
           st.session_state.current_page += 1
           st.rerun()

   with col4:
       st.metric("Page", f"{current_page} / {total_pages}")
   ```

5. **Update All CRUD Operations**
   - List All → `client.recall_memory("*", n_results=1000)` or `client.search_by_tag([])`
   - Semantic Search → `client.recall_memory(query, n_results)`
   - Search by Tags → `client.search_by_tag(tags, match_all)`
   - Get by Hash → `client.get_by_hash(hash)`
   - Edit → `client.update_memory(hash, content, tags, metadata)`
   - Delete → `client.delete_memory(hash)`
   - Backup → `client.backup_memory()`
   - Health Check → `client.check_memory_health()`

---

### Phase 4: Update run_admin.sh

```bash
#!/bin/bash
# MCP Memory Admin UI Launcher
# Connects to MCP server via HTTP instead of direct database access

# Set MCP server URL from arg or use existing env var or default
if [ -n "$1" ]; then
    # Argument provided - use it as MCP server URL
    export MCP_SERVER_URL="$1"
elif [ -z "$MCP_SERVER_URL" ]; then
    # No argument and no env var - use default
    export MCP_SERVER_URL="http://localhost:8030/mcp"
fi

echo "🧠 MCP Memory Service - Admin UI"
echo "================================="
echo ""
echo "MCP Server: $MCP_SERVER_URL"
echo ""
echo "Starting Streamlit admin UI..."
echo "Browser will open to: http://localhost:8501"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Activate venv and run streamlit
source venv-admin/bin/activate
streamlit run src/admin/ui.py \
  --browser.serverAddress=localhost \
  --browser.gatherUsageStats=false \
  --server.headless=true
```

**Usage Examples:**
```bash
# Connect to remote MCP server
./run_admin.sh http://mevault:8030/mcp

# Use MCP_SERVER_URL environment variable
export MCP_SERVER_URL=http://mevault:8030/mcp
./run_admin.sh

# Use default (localhost)
./run_admin.sh
```

---

### Phase 5: Update Dependencies

**File:** `requirements-admin.txt`

```txt
# Current dependencies
streamlit>=1.28.0
sqlite-vec>=0.1.0
sentence-transformers>=2.2.2
torch>=2.0.0
scikit-learn>=1.3.0

# NEW: Add HTTP client dependencies
requests>=2.31.0
aiohttp>=3.9.0  # For async HTTP requests
sseclient-py>=1.8.0  # For SSE streaming (if needed)
```

---

## Implementation Checklist

### Phase 1: MCP Client (2-3 hours)
- [ ] Create `src/admin/mcp_client.py`
- [ ] Implement JSON-RPC communication layer
- [ ] Implement `list_tools()` and `call_tool()`
- [ ] Add tool wrapper methods (recall, search, update, delete, etc.)
- [ ] Implement `_parse_memories()` helper
- [ ] Add connection testing
- [ ] Test against running MCP server at `http://mevault:8030/mcp`

### Phase 2: Pagination (1 hour)
- [ ] Add pagination state to Streamlit session
- [ ] Implement client-side pagination logic
- [ ] Add UI controls (page size selector, prev/next buttons)
- [ ] Test with various page sizes

### Phase 3: UI Refactoring (3-4 hours)
- [ ] Update imports in `ui.py`
- [ ] Replace database path input with MCP URL input
- [ ] Replace `SqliteVecMemoryStorage` with `MCPHttpClient`
- [ ] Update connection logic
- [ ] Refactor all memory operations to use client
- [ ] Update health check to use `client.check_memory_health()`
- [ ] Update backup to use `client.backup_memory()`
- [ ] Remove direct SQLite access code

### Phase 4: Testing (1-2 hours)
- [ ] Test connection to MCP server
- [ ] Test all CRUD operations (create, read, update, delete)
- [ ] Test search operations (semantic, tags, content, hash)
- [ ] Test pagination with various datasets
- [ ] Test error scenarios (server down, timeout, invalid data)
- [ ] Verify no SQLite lock conflicts

### Phase 5: Documentation (30 mins)
- [ ] Update `ADMIN_UI.md` with new architecture
- [ ] Document MCP server URL configuration
- [ ] Add troubleshooting section for connection issues
- [ ] Update `run_admin.sh` usage examples

---

## Key Benefits

✅ **No Concurrency Issues** - Single writer (MCP server only)
✅ **Always Fresh Data** - No WAL checkpoint concerns, always reads from server
✅ **Consistent Logic** - All operations through same MCP tools
✅ **Simpler Architecture** - Admin UI is pure view layer
✅ **Remote Access** - Can connect to MCP server on different machine
✅ **Better Error Handling** - MCP server handles all validation
✅ **Future-Proof** - Easy to add new MCP tools to UI

---

## Files Summary

### New Files
- `src/admin/mcp_client.py` - HTTP/SSE client for MCP server

### Modified Files
- `src/admin/ui.py` - Refactor to use MCP client instead of direct SQLite
- `run_admin.sh` - Update for MCP URL configuration
- `requirements-admin.txt` - Add HTTP client libraries

### Removed/Deprecated
- Direct SQLite access from admin UI
- `SqliteVecMemoryStorage` import in UI
- Database path configuration in UI
- WAL checkpoint concerns in admin UI

---

## Rollback Plan

If issues arise, rollback is simple:
1. Keep backup of current `src/admin/ui.py`
2. Revert changes via git: `git checkout HEAD~1 src/admin/ui.py run_admin.sh`
3. Current direct SQLite approach still works (with concurrency caveats)

---

## Future Enhancements

1. **Server-Side Pagination**
   - Add `offset`/`limit` to MCP tools
   - Support large datasets (10K+ memories)

2. **Real-Time Updates**
   - Use SSE for live memory updates
   - Auto-refresh when MCP server writes new data

3. **Batch Operations**
   - Bulk edit/delete via UI
   - Progress indicators for long operations

4. **Advanced Search**
   - Combined filters (tags + content + time)
   - Saved search queries

5. **Visualization**
   - Memory timeline charts
   - Tag cloud visualization
   - Storage usage graphs

---

**Total Estimated Time:** 8-10 hours
**Priority:** High (fixes concurrency issues)
**Risk:** Low (clean rollback path available)
