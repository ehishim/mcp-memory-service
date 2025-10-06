#!/bin/bash
# MCP Memory Admin UI Launcher
# Connects to MCP server via HTTP with optional Bearer token authentication

# Parse command line arguments
while getopts "s:a:" opt; do
  case $opt in
    s) MCP_SERVER_URL="$OPTARG" ;;
    a) MCP_AUTH_TOKEN="$OPTARG" ;;
    \?) echo "Usage: $0 [-s server_url] [-a auth_token]" >&2; exit 1 ;;
  esac
done

# Set defaults if not provided via flags or env vars
if [ -z "$MCP_SERVER_URL" ]; then
    MCP_SERVER_URL="http://localhost:8030/mcp"
fi

# Export environment variables for Streamlit
export MCP_SERVER_URL
if [ -n "$MCP_AUTH_TOKEN" ]; then
    export MCP_AUTH_TOKEN
fi

echo "🧠 MCP Memory Service - Admin UI"
echo "================================="
echo ""
echo "MCP Server: $MCP_SERVER_URL"
if [ -n "$MCP_AUTH_TOKEN" ]; then
    echo "Auth Token: ******* (provided)"
else
    echo "Auth Token: (none)"
fi
echo ""
echo "Starting Streamlit admin UI..."
echo "Browser will open to: http://localhost:8501"
echo ""
echo "Usage:"
echo "  ./run_admin.sh                                    # Connect to localhost:8030"
echo "  ./run_admin.sh -s http://mevault:8030/mcp         # Specify server URL"
echo "  ./run_admin.sh -s <url> -a <token>                # With authentication"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Activate venv and run streamlit
source venv-admin/bin/activate
streamlit run src/admin/ui.py \
  --browser.serverAddress=localhost \
  --browser.gatherUsageStats=false \
  --server.headless=true
