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
