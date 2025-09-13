# CLAUDE.md - Minimal Build

This file provides guidance to Claude Code (claude.ai/code) when working with this **minimized** MCP Memory Service repository.

## Overview

MCP Memory Service **Minimal Build** is a streamlined Model Context Protocol server providing core semantic memory functionality optimized for Docker deployment with SQLite-Vec and ChromaDB backends.

## Essential Commands

```bash
# Docker Deployment (Primary Method)
docker build -f Dockerfile.local -t mcp-memory-service .
docker run -p 4000:4000 -v $(pwd)/data:/app/data mcp-memory-service

# Development & Testing
python scripts/run_memory_server.py         # Direct server start
python3 -m py_compile src/mcp_memory_service/server.py  # Syntax check

# Memory Operations via MCP Protocol
# Use through Claude Desktop or other MCP clients
```

## Architecture - Minimized

**Core Components:**
- **Server Layer**: Streamlined MCP protocol implementation (`src/mcp_memory_service/server.py` - 2,287 lines)
- **Storage Backends**: SQLite-Vec (primary), ChromaDB (basic version)
- **Document Ingestion**: PDF, text, markdown, and JSON processing
- **Embedding**: Sentence transformers for semantic search

**Removed Components:**
- ❌ Web Interface (FastAPI dashboard removed)  
- ❌ HTTP Server (multi-client coordination removed)
- ❌ Debug utilities (production-ready only)
- ❌ Complex consolidation system
- ❌ LM Studio compatibility layers
- ❌ Network discovery and port detection

**Key Design Patterns:**
- Async/await for I/O operations
- Simplified client detection (Docker-aware)
- Direct storage initialization
- Essential tool set (5 core + 2 ingestion tools)

## Environment Variables - Minimal

**Docker Configuration:**
```bash
# Storage Backend (Required)
export MCP_MEMORY_STORAGE_BACKEND=sqlite_vec

# Database Paths (Docker volumes)
export MCP_MEMORY_SQLITE_PATH=/app/data/sqlite_vec.db
export MCP_MEMORY_CHROMA_PATH=/app/data/chroma_db  
export MCP_MEMORY_BACKUPS_PATH=/app/data/backups

# Container Detection
export DOCKER_CONTAINER=true
```

**Platform Support:** Containerized deployment (Linux/Docker), cross-platform compatible

## Storage Backends - Simplified

| Backend | Status | Use Case |
|---------|--------|----------|
| SQLite-Vec | ✅ Primary | Docker deployment, single container |
| ChromaDB | ✅ Basic | Alternative storage, same container |
| Cloudflare | ✅ Available | Production scaling (if needed) |

## Development Guidelines - Minimal

- **Docker-first approach**: Use containerized deployment for consistency
- Memory operations handle duplicates via content hashing
- Core tools only: store, retrieve, search, delete, health check, ingestion
- Minimal dependencies: 8 essential packages in pyproject.toml
- No web interface: MCP protocol only (stream mode)

## Minimal Tool Set

**Core Memory Tools (5):**
- `store_memory` - Store content with optional tags
- `retrieve_memory` - Semantic search and retrieval  
- `search_by_tag` - Tag-based filtering (AND/OR logic)
- `delete_memory` - Remove by content hash
- `check_database_health` - System health status

**Document Ingestion (2):**
- `ingest_document` - Process single PDF/text/markdown/JSON files
- `ingest_directory` - Batch process document directories

## Docker Deployment

```bash
# Build minimal container
docker build -f Dockerfile.local -t mcp-memory-service .

# Run with persistent data volume
docker run -d \
  --name mcp-memory \
  -p 4000:4000 \
  -v $(pwd)/data:/app/data \
  -e MCP_MEMORY_STORAGE_BACKEND=sqlite_vec \
  mcp-memory-service

# Check container logs
docker logs mcp-memory -f
```

## File Structure - Minimized

```
src/mcp_memory_service/
├── server.py (2,287 lines - main server)
├── storage/
│   ├── chroma.py (basic ChromaDB)
│   ├── sqlite_vec.py (primary storage)
│   └── cloudflare.py (production scaling)
├── ingestion/ (6 files - document processing)
└── [other utility modules]

Key removed files:
❌ utils/debug.py (debug utilities)
❌ utils/db_utils.py (database utilities) 
❌ utils/http_server_manager.py (web server)
❌ storage/http_client.py (multi-client)
❌ storage/chroma_enhanced.py (complex ChromaDB)
```

## Troubleshooting - Docker

**Common Issues:**
- **Container won't start**: Check port 4000 availability
- **Storage errors**: Ensure `/app/data` volume is writable
- **Memory issues**: Allocate sufficient container memory (512MB+)
- **Model download**: First run downloads ~25MB embedding model

**Debug Commands:**
```bash
# Check container status
docker ps -a

# Inspect container environment  
docker exec -it mcp-memory env

# Validate Python syntax
docker exec -it mcp-memory python3 -m py_compile /app/src/mcp_memory_service/server.py
```

> **This is a minimal, Docker-optimized build. For full features, use the main branch.**