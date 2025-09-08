#!/bin/bash
# Build MCP Memory Service Docker container

echo "🐳 Building MCP Memory Service Docker container..."

# Build the Docker image
docker build -f Dockerfile.local -t mcp-memory-service:local .

echo "✅ Docker image built successfully!"
echo ""
echo "📋 To run with persistent storage:"
echo "docker run --name mcp-memory -d -p 4000:4000 -v mcp-memory-data:/app/data mcp-memory-service:local"
echo ""
echo "📋 To run with local directory mount:"
echo "docker run --name mcp-memory -d -p 4000:4000 -v ./data:/app/data mcp-memory-service:local"