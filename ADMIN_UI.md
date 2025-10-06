# MCP Memory Service - Admin UI

Standalone Streamlit app for managing MCP Memory Service database directly.

## Features

✅ **Full CRUD Operations**
- List all memories with pagination
- **Semantic search** with natural language queries (including time filtering)
- Search by tags (AND/OR logic)
- Get memory by hash
- Edit content/tags/metadata (unified update method)
- Delete memories

✅ **System Operations**
- **Health check** - Database statistics and system status
- **Create backups** - Snapshot database to backup directory
- **Cleanup duplicates** - Remove duplicate memories

✅ **Document Ingestion**
- **Single file ingestion** - PDF, text, markdown, JSON
- **Directory batch ingestion** - Process multiple documents
- Configurable chunking (size and overlap)
- Tag assignment during ingestion

✅ **Direct Database Access**
- No MCP server required
- Works offline with SQLite database
- Reuses existing storage backend code (`SqliteVecMemoryStorage`)

## Installation

```bash
# Install admin UI dependency
pip install -r requirements-admin.txt

# Or install streamlit directly
pip install streamlit
```

## Usage

### Basic Usage

```bash
# Run with default database path (./data/sqlite_vec.db)
streamlit run admin_ui.py

# Specify custom database path
streamlit run admin_ui.py -- --db-path /path/to/your/sqlite_vec.db
```

### Alternative: Direct Python

```bash
# Set database path in UI sidebar after launching
streamlit run admin_ui.py
```

Then open your browser to `http://localhost:8501`

## UI Guide

### Sidebar - Configuration

1. **Database Path**: Enter path to your SQLite database
2. **Connect**: Click to initialize connection

### System Operations

3. **Health Check**: View database statistics and system status
4. **Create Backup**: Snapshot database to `./data/backups/`
5. **Cleanup Duplicates**: Remove duplicate memories from database

### Search & Filter

6. **Search Mode**:
   - **List All**: Browse all memories with pagination
   - **Semantic Search**: Natural language queries (supports time expressions)
   - **Search by Tags**: Filter by tags (AND/OR logic)
   - **Get by Hash**: Fetch specific memory by hash
7. **Pagination**: Set page size and navigate pages (List All mode)

### Document Ingestion

8. **Single File**: Ingest individual documents (PDF, text, markdown, JSON)
9. **Directory**: Batch process entire directories
10. **Configuration**: Set chunk size, overlap, tags, file filters

### Main Area - Memory Management

#### Viewing Memories
- Memories displayed as expandable cards
- Shows: content preview, hash, timestamps, tags, metadata

#### Editing Memories
1. Click **✏️ Edit** button on any memory
2. Edit form appears with:
   - Content editor (regenerates hash/embeddings on save)
   - Tags (interactive chip-style editor)
   - Metadata (JSON format with validation)
3. Click **💾 Save Changes** or **❌ Cancel**

#### Deleting Memories
- Click **🗑️ Delete** button on memory card
- Immediate deletion (no undo)

## Technical Details

### Unified Update System
- **Single method** (`update_memory`) handles all updates:
  - Content changes: Regenerates hash and embeddings
  - Tag changes: Replaces existing tags
  - Metadata changes: Merges with existing metadata
- Atomic updates: All changes applied in single transaction
- Preserves: created_at timestamp

### Semantic Search
- Uses SQLite storage backend's `recall_memory` method
- Supports natural language time filtering:
  - "docker configurations from last week"
  - "python examples from January 2024"
  - "architecture decisions from yesterday"
- Returns most relevant results by similarity score

### Database Locking
⚠️ **Important**: Stop MCP server before using admin UI to avoid database locks

## Architecture

- **Direct SQLite Access**: Uses `SqliteVecMemoryStorage` backend
- **Unified Updates**: Single `update_memory()` method for all modifications
- **Semantic Search**: `recall_memory()` for natural language queries
- **Document Processing**: `DocumentProcessor` for ingestion workflows
- **No Network Required**: Pure local database operations

## Troubleshooting

### Database Locked Error
```
Stop the MCP server:
docker stop mcp-memory  # If using Docker
# OR kill the Python process running the server
```

### Cannot Find Database
```bash
# Check database path
ls -la ./data/sqlite_vec.db

# Use absolute path
streamlit run admin_ui.py
# Then enter full path in sidebar: /Users/you/project/data/sqlite_vec.db
```

### Import Errors
```bash
# Ensure you're in project root
cd /path/to/mcp-memory-service

# Install dependencies
pip install -r requirements.txt
pip install streamlit
```

## Example Workflows

### Workflow 1: System Health & Maintenance

1. **Launch UI**: `streamlit run src/admin/ui.py`
2. **Connect**: Enter `./data/sqlite_vec.db`, click "Connect"
3. **Check Health**: Click "💚 Check Health" to view stats
4. **Create Backup**: Click "💾 Create Backup" before bulk operations
5. **Cleanup**: Click "🧹 Cleanup Duplicates" if needed

### Workflow 2: Semantic Search

1. **Select Mode**: Choose "Semantic Search"
2. **Enter Query**: "docker configurations from last week"
3. **Adjust Results**: Use slider to set max results (1-50)
4. **View Results**: Memories ranked by relevance with scores

### Workflow 3: Document Ingestion

1. **Expand Ingestion**: Click "Ingest Documents" expander
2. **Select Mode**: Choose "Single File" or "Directory"
3. **Configure**:
   - Path: `/path/to/documents/`
   - Tags: `PROJECT:myproject, TYPE:documentation`
   - Chunk size: 1000, Overlap: 100
4. **Ingest**: Click "📄 Ingest File" or "📁 Ingest Directory"
5. **Verify**: Use "List All" or "Search by Tags" to see ingested memories

### Workflow 4: Edit Memory

1. **Find Memory**: Use any search mode
2. **Edit**: Click ✏️ on desired memory
3. **Modify**: Update content, tags, or metadata
4. **Save**: Click "💾 Save Changes"
5. **Verify**: Memory updated (new hash if content changed)

## Security Note

This UI provides **full write access** to your memory database. Use with caution:
- No undo for deletions
- Content changes create new hashes
- Always backup before bulk operations

## Backup Command

```bash
# Create backup before using admin UI
cp ./data/sqlite_vec.db ./data/sqlite_vec.db.backup
```
