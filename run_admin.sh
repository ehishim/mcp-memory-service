#!/bin/bash
# MCP Memory Admin UI Launcher
# Reuses MCP environment variables for consistency

# Set database path from arg, or use existing MCP_MEMORY_SQLITE_PATH, or default
if [ -n "$1" ]; then
    # Argument provided - use it
    export MCP_MEMORY_SQLITE_PATH="$1"
elif [ -z "$MCP_MEMORY_SQLITE_PATH" ]; then
    # No argument and no env var - use default
    export MCP_MEMORY_SQLITE_PATH="./data/sqlite_vec.db"
fi

# Set default backups path if not already set
if [ -z "$MCP_MEMORY_BACKUPS_PATH" ]; then
    export MCP_MEMORY_BACKUPS_PATH="./data/backups"
fi

# Set storage backend to sqlite_vec (admin UI only supports this)
export MCP_MEMORY_STORAGE_BACKEND="sqlite_vec"

echo "🧠 MCP Memory Service - Admin UI"
echo "================================="
echo ""
echo "Database: $MCP_MEMORY_SQLITE_PATH"
echo "Backups:  $MCP_MEMORY_BACKUPS_PATH"
echo "Backend:  $MCP_MEMORY_STORAGE_BACKEND"
echo ""
echo "Starting Streamlit admin UI..."
echo "Browser will open to: http://localhost:8501"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Activate venv and run streamlit in headless mode
source venv-admin/bin/activate
streamlit run src/admin/ui.py \
  --browser.serverAddress=localhost \
  --browser.gatherUsageStats=false \
  --server.headless=true
