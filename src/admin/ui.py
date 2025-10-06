#!/usr/bin/env python3
"""
MCP Memory Service - Admin UI
Directly accesses SQLite database for memory management.
"""

import streamlit as st
import sys
import os
from pathlib import Path
from typing import List, Dict, Any
import json
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_memory_service.storage.sqlite_vec import SqliteVecMemoryStorage
from mcp_memory_service.models.memory import Memory


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


def init_storage(db_path: str):
    """Initialize storage backend."""
    if 'storage' not in st.session_state or st.session_state.get('db_path') != db_path:
        st.session_state.storage = SqliteVecMemoryStorage(db_path=db_path)
        st.session_state.db_path = db_path


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
            st.markdown(f'<p class="hash-text">Hash: {memory.content_hash}</p>', unsafe_allow_html=True)
            st.markdown(f"**Created:** {format_timestamp(memory.created_at)} | **Updated:** {format_timestamp(memory.updated_at)}")

        with col2:
            if st.button("✏️ Edit", key=f"edit_{idx}"):
                st.session_state.editing_hash = memory.content_hash
                st.session_state.edit_content = memory.content

                # Store the memory itself to keep it visible during edit
                st.session_state.editing_memory = memory

                # Handle tags - check both tags field and metadata
                tags_to_edit = []

                # First try tags field
                if memory.tags:
                    if isinstance(memory.tags, str):
                        # Handle both comma-separated and empty strings
                        if memory.tags.strip():
                            tags_to_edit = [t.strip() for t in memory.tags.split(",") if t.strip()]
                    elif isinstance(memory.tags, list):
                        tags_to_edit = memory.tags

                # If still no tags, check metadata as fallback
                if not tags_to_edit and memory.metadata and 'tags' in memory.metadata:
                    metadata_tags = memory.metadata['tags']
                    if isinstance(metadata_tags, str):
                        # Try to parse JSON array string
                        try:
                            import json
                            tags_to_edit = json.loads(metadata_tags)
                        except:
                            # Fall back to simple parsing
                            metadata_tags = metadata_tags.strip('[]"').replace('\\"', '')
                            tags_to_edit = [t.strip() for t in metadata_tags.split(",") if t.strip()]
                    elif isinstance(metadata_tags, list):
                        tags_to_edit = metadata_tags

                st.session_state.edit_tags = tags_to_edit
                st.session_state.edit_tags_original = tags_to_edit.copy()  # Track original for comparison
                st.session_state.edit_metadata = memory.metadata or {}
                st.rerun()

            if st.button("🗑️ Delete", key=f"del_{idx}"):
                if st.session_state.storage:
                    import asyncio
                    success, msg = asyncio.run(st.session_state.storage.delete(memory.content_hash))
                    if success:
                        st.success(f"✅ Deleted: {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ Error: {msg}")

        # Display content
        st.text_area("Content", memory.content, height=100, disabled=True, key=f"content_display_{idx}")

        # Display tags
        if memory.tags:
            # Handle both string and list tags
            tags_list = memory.tags if isinstance(memory.tags, list) else [t.strip() for t in memory.tags.split(",") if t.strip()]
            if tags_list:
                st.markdown("**Tags:** " + " ".join([f"`{tag}`" for tag in tags_list]))

        # Display metadata
        if memory.metadata:
            with st.expander("Metadata"):
                st.json(memory.metadata)


def edit_memory_form():
    """Display edit form for selected memory."""
    if 'editing_hash' not in st.session_state:
        return

    st.divider()
    st.subheader("✏️ Edit Memory")

    # Display hash for reference
    st.markdown(f'<p class="hash-text">Hash: {st.session_state.editing_hash}</p>', unsafe_allow_html=True)
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

    col1, col2, col3 = st.columns([1, 1, 3])

    with col1:
        if st.button("💾 Save Changes", type="primary", disabled=not is_valid_json):
            import asyncio

            # Validate JSON before saving
            if not is_valid_json:
                st.error("❌ Cannot save: Invalid JSON in metadata")
                return

            # Build update payload
            update_params = {}

            # Check if content changed
            if new_content != st.session_state.edit_content:
                update_params['content'] = new_content

            # Check if tags changed
            original_tags = st.session_state.get('edit_tags_original', [])
            if set(new_tags) != set(original_tags):
                update_params['tags'] = new_tags

            # Check if metadata changed
            if parsed_metadata != st.session_state.edit_metadata:
                update_params['metadata'] = parsed_metadata

            # Only update if there are changes
            if update_params:
                # Use unified update_memory method
                success, msg = asyncio.run(
                    st.session_state.storage.update_memory(
                        st.session_state.editing_hash,
                        **update_params
                    )
                )
                if success:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ Update failed: {msg}")
            else:
                st.info("ℹ️ No changes detected")

            # Clear edit state
            del st.session_state.editing_hash
            if 'editing_memory' in st.session_state:
                del st.session_state.editing_memory
            st.rerun()

    with col2:
        if st.button("❌ Cancel"):
            del st.session_state.editing_hash
            if 'editing_memory' in st.session_state:
                del st.session_state.editing_memory
            st.rerun()


def main():
    st.title("🧠 MCP Memory Service - Admin UI")

    # Sidebar - Database selection
    with st.sidebar:
        st.header("⚙️ Configuration")

        db_path = st.text_input(
            "Database Path",
            value=st.session_state.get('db_path', './data/sqlite_vec.db'),
            help="Path to SQLite database file"
        )

        if st.button("🔌 Connect"):
            try:
                init_storage(db_path)
                st.success("✅ Connected to database")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")

        st.divider()

        # System Operations
        st.header("🛠️ System Operations")

        if st.button("💚 Check Health", use_container_width=True):
            if 'storage' in st.session_state:
                import asyncio
                try:
                    stats = asyncio.run(st.session_state.storage.get_stats())
                    st.success("✅ System Healthy")
                    st.json(stats)
                except Exception as e:
                    st.error(f"❌ Health check failed: {e}")
            else:
                st.warning("Connect to database first")

        if st.button("💾 Create Backup", use_container_width=True):
            if 'storage' in st.session_state:
                import asyncio
                import shutil
                from datetime import datetime
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = f"./data/backups/sqlite_vec_{timestamp}.db"
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(db_path, backup_path)
                    st.success(f"✅ Backup created: {backup_path}")
                except Exception as e:
                    st.error(f"❌ Backup failed: {e}")
            else:
                st.warning("Connect to database first")

        if st.button("🧹 Cleanup Duplicates", use_container_width=True):
            if 'storage' in st.session_state:
                import asyncio
                try:
                    count, msg = asyncio.run(st.session_state.storage.cleanup_duplicates())
                    st.success(f"✅ {msg}")
                except Exception as e:
                    st.error(f"❌ Cleanup failed: {e}")
            else:
                st.warning("Connect to database first")

        st.divider()

        # Search options
        st.header("🔍 Search & Filter")

        search_mode = st.radio(
            "Search Mode",
            ["List All", "Semantic Search", "Search by Tags", "Get by Hash"]
        )

        if search_mode == "Semantic Search":
            query_input = st.text_input("Search Query", placeholder="e.g., 'docker configurations' or 'last week'")
            n_results = st.slider("Max Results", min_value=1, max_value=50, value=10)
        elif search_mode == "Search by Tags":
            tags_input = st.text_input("Tags (comma-separated)")
            match_all = st.checkbox("Match ALL tags (AND logic)", value=False)
        elif search_mode == "Get by Hash":
            hash_input = st.text_input("Content Hash")

        # Pagination (only for List All)
        if search_mode == "List All":
            st.divider()
            page_size = st.select_slider("Page Size", options=[10, 25, 50, 100], value=25)
            page = st.number_input("Page", min_value=1, value=1)
        else:
            page_size = 25
            page = 1

        st.divider()

        # Document Ingestion
        st.header("📄 Document Ingestion")

        with st.expander("Ingest Documents", expanded=False):
            ingest_mode = st.radio("Ingestion Mode", ["Single File", "Directory"], horizontal=True)

            if ingest_mode == "Single File":
                file_path = st.text_input("File Path", placeholder="/path/to/document.pdf")
                file_tags = st.text_input("Tags (comma-separated)", key="file_tags")
                chunk_size = st.number_input("Chunk Size", min_value=100, max_value=5000, value=1000)
                chunk_overlap = st.number_input("Chunk Overlap", min_value=0, max_value=500, value=100)

                if st.button("📄 Ingest File", use_container_width=True):
                    if 'storage' in st.session_state and file_path:
                        import asyncio
                        from mcp_memory_service.ingestion.document_processor import DocumentProcessor
                        try:
                            processor = DocumentProcessor(st.session_state.storage)
                            tags = [t.strip() for t in file_tags.split(",")] if file_tags else []
                            count, msg = asyncio.run(processor.ingest_document(
                                file_path, tags, chunk_size, chunk_overlap
                            ))
                            st.success(f"✅ {msg}")
                        except Exception as e:
                            st.error(f"❌ Ingestion failed: {e}")
                    else:
                        st.warning("Connect to database and provide file path")

            else:  # Directory
                dir_path = st.text_input("Directory Path", placeholder="/path/to/documents/")
                dir_tags = st.text_input("Tags (comma-separated)", key="dir_tags")
                recursive = st.checkbox("Recursive", value=False)
                file_extensions = st.text_input("File Extensions", value=".pdf,.txt,.md,.json")
                max_files = st.number_input("Max Files", min_value=1, max_value=1000, value=100)

                if st.button("📁 Ingest Directory", use_container_width=True):
                    if 'storage' in st.session_state and dir_path:
                        import asyncio
                        from mcp_memory_service.ingestion.document_processor import DocumentProcessor
                        try:
                            processor = DocumentProcessor(st.session_state.storage)
                            tags = [t.strip() for t in dir_tags.split(",")] if dir_tags else []
                            exts = [e.strip() for e in file_extensions.split(",")]
                            count, msg = asyncio.run(processor.ingest_directory(
                                dir_path, tags, recursive, exts, max_files=max_files
                            ))
                            st.success(f"✅ {msg}")
                        except Exception as e:
                            st.error(f"❌ Ingestion failed: {e}")
                    else:
                        st.warning("Connect to database and provide directory path")

    # Main content area
    if 'storage' not in st.session_state:
        st.info("👈 Connect to a database using the sidebar")
        return

    import asyncio

    # Fetch memories based on search mode
    memories = []
    total_count = 0

    try:
        if search_mode == "List All":
            offset = (page - 1) * page_size
            memories = asyncio.run(
                st.session_state.storage.get_all_memories(limit=page_size, offset=offset)
            )
            # Get total count for pagination
            all_memories = asyncio.run(st.session_state.storage.get_all_memories())
            total_count = len(all_memories)

        elif search_mode == "Semantic Search":
            if query_input:
                results = asyncio.run(
                    st.session_state.storage.recall_memory(query_input, n_results)
                )
                memories = results  # recall_memory returns List[Memory]
                total_count = len(memories)
            else:
                st.warning("Enter a search query")

        elif search_mode == "Search by Tags":
            if tags_input:
                tags = [t.strip() for t in tags_input.split(",") if t.strip()]
                operation = "AND" if match_all else "OR"
                memories = asyncio.run(
                    st.session_state.storage.search_by_tags(tags, operation=operation)
                )
                total_count = len(memories)
                # Apply pagination to results
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                memories = memories[start_idx:end_idx]

        elif search_mode == "Get by Hash":
            if hash_input:
                memory = asyncio.run(st.session_state.storage.get_by_hash(hash_input))
                if memory:
                    memories = [memory]
                    total_count = 1

    except Exception as e:
        st.error(f"❌ Error fetching memories: {e}")
        return

    # Display stats
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Results", total_count)
    with col2:
        st.metric("Current Page", page)
    with col3:
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
        st.metric("Total Pages", total_pages)

    st.divider()

    # Edit form (if editing)
    if 'editing_hash' in st.session_state:
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
