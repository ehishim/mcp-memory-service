#!/usr/bin/env python3
"""
MCP Memory Service - Admin UI (Refactored)
Connects to MCP server via HTTP/JSON-RPC instead of direct SQLite access.
"""

import streamlit as st
import sys
import os
from pathlib import Path
from typing import List, Dict, Any
import json
from datetime import datetime
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from admin.mcp_client import MCPHttpClient
from mcp_memory_service.models.memory import Memory


def run_async(coro):
    """
    Run async coroutine in Streamlit-compatible way.
    Handles event loop management to avoid 'Event loop is closed' errors.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


# Page config
st.set_page_config(
    page_title="MCP Memory Admin",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .stTextArea textarea { font-family: 'Monaco', 'Courier New', monospace; }
    .memory-card {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #f0f2f6;
        margin: 0.5rem 0;
    }
    .hash-text {
        font-family: 'Monaco', monospace;
        font-size: 0.8em;
        color: #666;
    }
    .tag-chip {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        margin: 0.25rem;
        border-radius: 1rem;
        background-color: #e3f2fd;
        color: #1976d2;
        font-size: 0.875rem;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)


async def init_client(mcp_url: str, auth_token: str = None):
    """Initialize MCP HTTP client with optional auth token."""
    client = MCPHttpClient(mcp_url, auth_token=auth_token)
    success, message = await client.test_connection()
    if success:
        st.session_state.client = client
        st.session_state.mcp_url = mcp_url
        st.session_state.auth_token = auth_token
        return True, message
    else:
        return False, message


def format_timestamp(timestamp: float) -> str:
    """Format Unix timestamp to readable string."""
    if not timestamp:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def tag_editor(session_key: str = "edit_tags") -> List[str]:
    """
    Interactive tag editor with clean chip-style UI.
    Returns the updated list of tags.
    """
    if session_key not in st.session_state:
        st.session_state[session_key] = []

    current_tags = st.session_state[session_key]

    st.markdown("**Tags:**")

    # Display existing tags as clickable chips
    if current_tags:
        # Create inline buttons - use fewer columns to prevent wrapping
        cols = st.columns(min(len(current_tags), 4))

        for idx, tag in enumerate(current_tags):
            col_idx = idx % 4
            with cols[col_idx]:
                if st.button(f"{tag} ×", key=f"remove_tag_{session_key}_{idx}", help=f"Click to remove", use_container_width=True):
                    st.session_state[session_key].remove(tag)
                    st.rerun()
    else:
        st.info("No tags yet")

    # Add new tag - simplified
    col1, col2 = st.columns([5, 1])
    with col1:
        new_tag = st.text_input(
            "Add tag",
            key=f"new_tag_input_{session_key}",
            placeholder="Type tag and press Enter or click Add...",
            label_visibility="collapsed"
        )
    with col2:
        if st.button("Add", key=f"add_tag_btn_{session_key}", use_container_width=True):
            if new_tag and new_tag.strip():
                tag = new_tag.strip()
                if tag not in st.session_state[session_key]:
                    st.session_state[session_key].append(tag)
                    st.rerun()
                else:
                    st.warning(f"Tag '{tag}' already exists!")

    return st.session_state[session_key]


def display_memory_card(memory: Memory, idx: int):
    """Display a memory card with edit capabilities."""
    # Build expander title with content preview and tags
    title = f"📝 {memory.content[:80]}..." if len(memory.content) > 80 else f"📝 {memory.content}"

    # Add relevance score to title if present (from semantic search)
    if hasattr(memory, 'relevance_score') and memory.relevance_score is not None:
        score_pct = memory.relevance_score * 100
        title = f"🎯 {score_pct:.1f}% | {title}"

    # Add tags to title if present
    if memory.tags:
        tags_list = memory.tags if isinstance(memory.tags, list) else [t.strip() for t in memory.tags.split(",") if t.strip()]
        if tags_list:
            tags_preview = " ".join([f"`{tag}`" for tag in tags_list[:3]])  # Show first 3 tags
            if len(tags_list) > 3:
                tags_preview += f" +{len(tags_list) - 3} more"
            title += f"  |  {tags_preview}"

    with st.expander(title, expanded=False):
        col1, col2 = st.columns([3, 1])

        with col1:
            st.markdown(f'<p class="hash-text">ID: {memory.id}</p>', unsafe_allow_html=True)

            # Show relevance score prominently if from semantic search
            if hasattr(memory, 'relevance_score') and memory.relevance_score is not None:
                st.markdown(f"**Relevance:** {memory.relevance_score:.4f} ({memory.relevance_score * 100:.1f}%)")

            st.markdown(f"**Created:** {format_timestamp(memory.created_at)} | **Updated:** {format_timestamp(memory.updated_at)}")

        with col2:
            if st.button("✏️ Edit", key=f"edit_{memory.id}"):
                st.session_state.editing_id = memory.id
                st.session_state.edit_content = memory.content

                # Store the memory itself to keep it visible during edit
                st.session_state.editing_memory = memory

                # Handle tags from tags field only
                tags_to_edit = []
                if memory.tags:
                    if isinstance(memory.tags, str):
                        # Handle comma-separated string
                        if memory.tags.strip():
                            tags_to_edit = [t.strip() for t in memory.tags.split(",") if t.strip()]
                    elif isinstance(memory.tags, list):
                        tags_to_edit = memory.tags

                st.session_state.edit_tags = tags_to_edit
                st.session_state.edit_tags_original = tags_to_edit.copy()  # Track original for comparison
                st.session_state.edit_metadata = memory.metadata or {}
                st.rerun()

            if st.button("🗑️ Delete", key=f"del_{memory.id}"):
                if st.session_state.client:
                    result = run_async(st.session_state.client.delete_memory(memory.id))
                    if result.get("success"):
                        st.success(f"✅ Deleted memory")
                        st.rerun()
                    else:
                        st.error(f"❌ Error: {result.get('message', 'Unknown error')}")

        # Display content
        st.text_area("Content", memory.content, height=100, disabled=True, key=f"content_display_{memory.id}")

        # Display tags from tags field only
        tags_list = []
        if memory.tags:
            if isinstance(memory.tags, str):
                # Handle comma-separated string
                if memory.tags.strip():
                    tags_list = [t.strip() for t in memory.tags.split(",") if t.strip()]
            elif isinstance(memory.tags, list):
                tags_list = memory.tags

        if tags_list:
            # Create colored tag chips
            tags_html = " ".join([
                f'<span style="background-color: #e0e0e0; color: #333; padding: 2px 8px; border-radius: 12px; margin-right: 4px; font-size: 0.85em;">{tag}</span>'
                for tag in tags_list
            ])
            st.markdown(f"**Tags:** {tags_html}", unsafe_allow_html=True)
        else:
            st.caption("_No tags_")

        # Display metadata
        if memory.metadata:
            with st.expander("Metadata"):
                st.json(memory.metadata)


def edit_memory_form():
    """Display edit form for selected memory."""
    if 'editing_id' not in st.session_state:
        return

    st.divider()
    st.subheader("✏️ Edit Memory")

    # Display ID for reference
    st.markdown(f'<p class="hash-text">ID: {st.session_state.editing_id}</p>', unsafe_allow_html=True)
    st.markdown("---")

    # Content editor
    new_content = st.text_area(
        "Content",
        value=st.session_state.get('edit_content', ''),
        height=300,
        key="edit_content_input"
    )

    st.markdown("---")

    # Interactive tag editor
    new_tags = tag_editor(session_key="edit_tags")

    st.markdown("---")

    # Metadata editor (JSON) with validation
    st.markdown("**Metadata (JSON):**")
    st.caption("Leave empty for no metadata, or add custom fields as JSON")

    metadata_str = st.text_area(
        "metadata_json",
        value=json.dumps(st.session_state.get('edit_metadata', {}), indent=2) if st.session_state.get('edit_metadata') else "",
        height=150,
        key="edit_metadata_input",
        label_visibility="collapsed",
        help="Custom metadata fields only. Tags are managed separately above. Leave empty or use {} for no metadata.",
        on_change=None  # Triggers re-render on every keystroke
    )

    # Real-time JSON validation (validates as you type)
    is_valid_json = False
    parsed_metadata = {}

    # Trim whitespace for validation
    metadata_str_trimmed = metadata_str.strip()

    # Empty string, whitespace-only, or {} is valid (no metadata)
    if not metadata_str_trimmed or metadata_str_trimmed == "{}":
        is_valid_json = True
        parsed_metadata = {}
        if not metadata_str_trimmed:
            st.success("✅ Valid (no metadata)")
        else:
            st.success("✅ Valid (empty metadata)")
    else:
        try:
            parsed_metadata = json.loads(metadata_str_trimmed)
            is_valid_json = True
            # Show field count
            field_count = len(parsed_metadata.keys()) if isinstance(parsed_metadata, dict) else 0
            st.success(f"✅ Valid JSON ({field_count} field{'s' if field_count != 1 else ''})")
        except json.JSONDecodeError as e:
            st.error(f"❌ Invalid JSON: {e.msg} at line {e.lineno}, column {e.colno}")
            is_valid_json = False
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")
            is_valid_json = False

    preserve_ts = st.checkbox("Preserve updated_at timestamp", value=False, key="preserve_updated_at",
                              help="Keep the existing updated_at instead of setting it to now")

    col1, col2, col3 = st.columns([1, 1, 3])

    with col1:
        if st.button("💾 Save Changes", type="primary", disabled=not is_valid_json):
            # Validate JSON before saving
            if not is_valid_json:
                st.error("❌ Cannot save: Invalid JSON in metadata")
                return

            # Check what changed
            content_changed = new_content != st.session_state.edit_content
            original_tags = st.session_state.get('edit_tags_original', [])
            tags_changed = set(new_tags) != set(original_tags)
            metadata_changed = parsed_metadata != st.session_state.edit_metadata

            # Handle content changes (requires delete + store)
            if content_changed:
                try:
                    # Delete old memory
                    delete_result = run_async(
                        st.session_state.client.delete_memory(st.session_state.editing_id)
                    )
                    if not delete_result.get("success"):
                        st.error(f"❌ Delete failed: {delete_result.get('error', 'Unknown error')}")
                        return

                    # Store new memory with updated content
                    store_result = run_async(
                        st.session_state.client.store_memory(
                            content=new_content,
                            tags=new_tags,
                            metadata=parsed_metadata
                        )
                    )
                    if store_result.get("success"):
                        st.success(f"✅ Memory updated (content changed, new hash: {store_result.get('hash', 'unknown')})")
                    else:
                        st.error(f"❌ Store failed: {store_result.get('error', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ Error: {str(e)}")

            # Handle tags/metadata changes only (no content change)
            elif tags_changed or metadata_changed:
                # Build keyword arguments for update_memory
                update_kwargs = {'id': st.session_state.editing_id}
                if tags_changed:
                    update_kwargs['tags'] = new_tags
                if metadata_changed:
                    update_kwargs['metadata'] = parsed_metadata
                if preserve_ts:
                    update_kwargs['preserve_updated_at'] = True

                result = run_async(
                    st.session_state.client.update_memory(**update_kwargs)
                )
                if result.get("success"):
                    st.success(f"✅ Memory updated")
                else:
                    st.error(f"❌ Update failed: {result.get('message', 'Unknown error')}")
            else:
                st.info("ℹ️ No changes detected")

            # Clear edit state
            del st.session_state.editing_id
            if 'editing_memory' in st.session_state:
                del st.session_state.editing_memory
            st.rerun()

    with col2:
        if st.button("❌ Cancel"):
            del st.session_state.editing_id
            if 'editing_memory' in st.session_state:
                del st.session_state.editing_memory
            st.rerun()


def main():
    st.title("🧠 MCP Memory Service - Admin UI")

    # Sidebar - MCP Server Connection
    with st.sidebar:
        st.header("⚙️ Configuration")

        # MCP server URL from env var or user input
        default_url = os.environ.get('MCP_SERVER_URL', 'http://localhost:8030/mcp')
        mcp_url = st.text_input(
            "MCP Server URL",
            value=st.session_state.get('mcp_url', default_url),
            help="URL of the running MCP server (e.g., http://mevault:8030/mcp)"
        )

        # Optional auth token from env var or user input
        default_token = os.environ.get('MCP_AUTH_TOKEN', '')
        auth_token = st.text_input(
            "Authorization Token (Optional)",
            value=st.session_state.get('auth_token', default_token),
            type="password",
            help="Bearer token for stateless HTTP MCP authentication"
        )

        if st.button("🔌 Connect"):
            try:
                # Only pass non-empty auth token
                token = auth_token.strip() if auth_token else None
                success, message = run_async(init_client(mcp_url, auth_token=token))
                if success:
                    st.success(f"✅ {message}")
                else:
                    st.error(f"❌ {message}")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")

        st.divider()

        # System Operations
        st.header("🛠️ System Operations")

        if st.button("💚 Check Health", use_container_width=True):
            if 'client' in st.session_state:
                try:
                    result = run_async(st.session_state.client.check_memory_health())
                    if result.get("success"):
                        st.success("✅ System Healthy")
                        if "health" in result:
                            st.json(result["health"])
                    else:
                        st.error(f"❌ Health check failed: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ Health check failed: {e}")
            else:
                st.warning("Connect to MCP server first")

        if st.button("💾 Create Backup", use_container_width=True):
            if 'client' in st.session_state:
                try:
                    result = run_async(st.session_state.client.backup_memory())
                    if result.get("success"):
                        st.success("✅ Backup created successfully")
                        if "backup" in result:
                            st.json(result["backup"])
                    else:
                        st.error(f"❌ Backup failed: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    st.error(f"❌ Backup failed: {e}")
            else:
                st.warning("Connect to MCP server first")

        st.divider()

        # Search options
        st.header("🔍 Search & Filter")

        search_mode = st.radio(
            "Search Mode",
            ["List All", "Semantic Search", "Search by Tags", "Search by Content", "Get by ID"]
        )

        # Reset page to 1 when search mode changes
        if 'previous_search_mode' not in st.session_state:
            st.session_state.previous_search_mode = search_mode
        elif st.session_state.previous_search_mode != search_mode:
            st.session_state.current_page = 1
            st.session_state.previous_search_mode = search_mode

        if search_mode == "Semantic Search":
            query_input = st.text_input("Search Query", placeholder="e.g., 'docker configurations' or 'last week'")
        elif search_mode == "Search by Tags":
            tags_input = st.text_input("Tags (comma-separated)")
            match_all = st.checkbox("Match ALL tags (AND logic)", value=False)
        elif search_mode == "Search by Content":
            content_input = st.text_input("Search Text", placeholder="substring search")
        elif search_mode == "Get by ID":
            id_input = st.text_input("Memory ID", placeholder="e.g., 550e8400-e29b-41d4-a716-446655440000")

        # Pagination controls
        st.divider()
        st.markdown("**Pagination:**")
        if 'page_size' not in st.session_state:
            st.session_state.page_size = 25
        if 'current_page' not in st.session_state:
            st.session_state.current_page = 1

        page_size = st.select_slider("Page Size", options=[10, 25, 50, 100], value=st.session_state.page_size)
        st.session_state.page_size = page_size

    # Main content area
    if 'client' not in st.session_state:
        st.info("👈 Connect to MCP server using the sidebar")
        return

    # Fetch memories based on search mode
    memories = []
    total_count = 0
    pagination = {}

    try:
        # Calculate offset from current page
        offset = (st.session_state.current_page - 1) * page_size

        if search_mode == "List All":
            # Use recall_memory with wildcard and server-side pagination
            memories, pagination = run_async(
                st.session_state.client.recall_memory(
                    "*",
                    limit=page_size,
                    offset=offset
                )
            )
            total_count = pagination.get('total', 0)

        elif search_mode == "Semantic Search":
            if query_input:
                memories, pagination = run_async(
                    st.session_state.client.recall_memory(
                        query_input,
                        limit=page_size,
                        offset=offset
                    )
                )
                total_count = pagination.get('total', 0)
            else:
                st.warning("Enter a search query")

        elif search_mode == "Search by Tags":
            if tags_input:
                tags = [t.strip() for t in tags_input.split(",") if t.strip()]
                memories, pagination = run_async(
                    st.session_state.client.search_by_tag(
                        tags,
                        match_all,
                        limit=page_size,
                        offset=offset
                    )
                )
                total_count = pagination.get('total', 0)
            else:
                st.warning("Enter tags to search")

        elif search_mode == "Search by Content":
            if content_input:
                memories, pagination = run_async(
                    st.session_state.client.search_by_content(
                        content_input,
                        limit=page_size,
                        offset=offset
                    )
                )
                total_count = pagination.get('total', 0)
            else:
                st.warning("Enter search text")

        elif search_mode == "Get by ID":
            if id_input:
                memory = run_async(st.session_state.client.get_by_id(id_input))
                if memory:
                    memories = [memory]
                    total_count = 1
            else:
                st.warning("Enter memory ID")

    except Exception as e:
        st.error(f"❌ Error fetching memories: {e}")
        return

    # Display stats and pagination controls
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0

    col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
    with col1:
        st.metric("Total Results", total_count)
    with col2:
        if st.button("← Previous", disabled=st.session_state.current_page <= 1):
            st.session_state.current_page -= 1
            st.rerun()
    with col3:
        if st.button("Next →", disabled=st.session_state.current_page >= total_pages):
            st.session_state.current_page += 1
            st.rerun()
    with col4:
        # Show "0 / 0" when no results, otherwise show current page
        current_page_display = st.session_state.current_page if total_count > 0 else 0
        st.metric("Page", f"{current_page_display} / {total_pages}")

    st.divider()

    # Edit form (if editing)
    if 'editing_id' in st.session_state:
        edit_memory_form()

        # Show the memory being edited
        if 'editing_memory' in st.session_state:
            st.divider()
            st.subheader("📝 Memory Being Edited")
            display_memory_card(st.session_state.editing_memory, 0)
    else:
        # Display memories only when not editing
        if not memories:
            st.info("No memories found")
        else:
            st.subheader(f"📚 Memories ({len(memories)} showing)")
            for idx, memory in enumerate(memories):
                display_memory_card(memory, idx)


if __name__ == "__main__":
    main()
