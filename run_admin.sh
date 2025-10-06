#!/bin/bash
# MCP Memory Admin UI Launcher

# Default database path
DB_PATH="${1:-./data/sqlite_vec.db}"

echo "🧠 MCP Memory Service - Admin UI"
echo "================================="
echo ""
echo "Database: $DB_PATH"
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
