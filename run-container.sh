#!/bin/bash
# Run MCP Memory Service Docker container

echo "🚀 Starting MCP Memory Service container..."

# Create data directory if it doesn't exist
mkdir -p ./data

# Stop and remove existing container if it exists
docker stop mcp-memory 2>/dev/null || true
docker rm mcp-memory 2>/dev/null || true

# Run the container with volume mount
docker run \
  --name mcp-memory \
  -d \
  -p 4000:4000 \
  -v "$(pwd)/data:/app/data" \
  mcp-memory-service:local

echo "✅ Container started successfully!"
echo ""
echo "📊 Container status:"
docker ps --filter name=mcp-memory

echo ""
echo "📋 Useful commands:"
echo "  View logs: docker logs mcp-memory -f"
echo "  Stop container: docker stop mcp-memory"
echo "  Remove container: docker rm mcp-memory"
echo "  Database location: ./data/sqlite_vec.db"