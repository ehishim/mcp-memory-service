# MCP Memory Service - Minimal Build

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED?style=flat&logo=docker)](https://github.com/doobidoo/mcp-memory-service#docker-deployment)
[![Minimal Build](https://img.shields.io/badge/Build-Minimal-orange?style=flat)](https://github.com/doobidoo/mcp-memory-service/tree/lite)

[![Works with Claude](https://img.shields.io/badge/Works%20with-Claude-blue)](https://claude.ai)
[![MCP Protocol](https://img.shields.io/badge/MCP-Compatible-4CAF50?style=flat)](https://modelcontextprotocol.io/)
[![SQLite-Vec](https://img.shields.io/badge/SQLite--Vec-Primary-lightgrey?style=flat)](https://github.com/asg017/sqlite-vec)

**Minimal MCP memory service** optimized for **Docker deployment** with core semantic memory functionality. Streamlined build with **SQLite-vec** primary storage, **basic ChromaDB** support, and **document ingestion** for AI assistants.

> 🎯 **This is the `lite` branch** - a minimized version optimized for containerized deployment. For full features, see the [main branch](https://github.com/doobidoo/mcp-memory-service).

## 🐳 Docker Deployment (Recommended)

### Quick Start
```bash
# Clone the lite branch
git clone -b lite https://github.com/doobidoo/mcp-memory-service.git
cd mcp-memory-service

# Build and run minimal container
docker build -f Dockerfile.local -t mcp-memory-service .
docker run -d \
  --name mcp-memory \
  -p 4000:4000 \
  -v $(pwd)/data:/app/data \
  -e MCP_MEMORY_STORAGE_BACKEND=sqlite_vec \
  mcp-memory-service

# Check logs
docker logs mcp-memory -f
```

### Alternative: Direct Python
```bash
# Setup & Development (if install.py exists)
python install.py                    # Platform-aware installation
# OR manually install minimal dependencies
pip install -e .

# Run directly
python scripts/run_memory_server.py
```

## ✨ What's Included - Minimal Build

**Core Features:**
- 🧠 **Semantic Memory**: Store and retrieve information with natural language
- 🏷️ **Tag-based Search**: Organize memories with flexible tagging
- 📄 **Document Ingestion**: Process PDF, text, markdown, and JSON files
- 🗃️ **SQLite-Vec Storage**: Fast local vector database (primary)
- 🌐 **ChromaDB Support**: Alternative storage backend (basic version)
- 🐳 **Docker Optimized**: Single container deployment

**Removed from Full Version:**
- ❌ Web Dashboard (FastAPI interface)
- ❌ HTTP API server
- ❌ Multi-client coordination
- ❌ Debug utilities and complex tooling
- ❌ LM Studio compatibility layers
- ❌ Network discovery features

## 📊 Minimal Tool Set

The lite build includes **7 essential tools**:

**Memory Operations (5):**
```bash
store_memory         # Store content with optional tags
retrieve_memory      # Semantic search and retrieval
search_by_tag       # Tag-based filtering (AND/OR logic)  
delete_memory       # Remove by content hash
check_database_health # System health status
```

**Document Processing (2):**
```bash
ingest_document     # Process single files (PDF/text/markdown/JSON)
ingest_directory    # Batch process document directories
```

## ⚠️ First-Time Setup

**Docker users:** The container automatically downloads the embedding model (~25MB) on first run. This takes 1-2 minutes.

**Common first-run messages (normal):**
- "WARNING: sqlite-vec not available" (until dependencies install)
- "Model download in progress" (embedding model initialization)
- "Storage backend initialization" (first-time setup)


## 🚀 Usage Examples

### Basic Memory Operations
```python
# Store information (via MCP client like Claude Desktop)
"Store this important fact: Docker containers are isolated environments"

# Retrieve with semantic search  
"What did I store about containers?"

# Tag-based organization
"Store: Meeting notes for Q4 planning" (tags: ["meetings", "q4", "planning"])
"Find all memories tagged with 'meetings'"
```

### Document Ingestion
```bash
# Process single document
ingest_document --file_path /docs/manual.pdf --tags "documentation,manual"

# Batch process directory
ingest_directory --directory_path /docs --recursive true --tags "knowledge-base"
```

## 📝 Dependencies - Minimal

**Core Dependencies (8 packages):**
```
mcp>=1.0.0,<2.0.0          # MCP protocol
sqlite-vec>=0.1.0          # Vector database  
chromadb==0.5.23           # Alternative storage
tokenizers==0.20.3         # Text tokenization
sentence-transformers>=2.2.2 # Embeddings
torch>=2.0.0               # ML backend
PyPDF2>=3.0.0             # PDF processing
chardet>=5.0.0            # Text encoding
```

**Removed Dependencies (13 packages):**
- ❌ FastAPI, uvicorn (web server)
- ❌ aiohttp, httpx (HTTP clients)  
- ❌ psutil (system monitoring)
- ❌ zeroconf (network discovery)
- ❌ Various development and debugging tools

## 🔧 Configuration

**Environment Variables:**
```bash
# Required
MCP_MEMORY_STORAGE_BACKEND=sqlite_vec

# Optional  
MCP_MEMORY_SQLITE_PATH=/app/data/sqlite_vec.db
MCP_MEMORY_CHROMA_PATH=/app/data/chroma_db
DOCKER_CONTAINER=true  # Auto-detected in container
```

## 🐛 Troubleshooting

**Container Issues:**
```bash
# Check container status
docker ps -a

# View logs
docker logs mcp-memory -f

# Access container shell
docker exec -it mcp-memory /bin/bash

# Test Python syntax
docker exec -it mcp-memory python3 -m py_compile /app/src/mcp_memory_service/server.py
```

**Common Problems:**
- **Port 4000 in use**: Change port mapping `-p 4001:4000`
- **Storage permission errors**: Ensure data volume is writable
- **Memory allocation**: Allocate 512MB+ for container
- **Model download timeout**: Increase container startup timeout

## 📁 File Structure

```
mcp-memory-service/
├── Dockerfile.local           # Minimal container build
├── pyproject.toml            # 8 core dependencies  
├── scripts/run_memory_server.py # Direct entry point
├── src/mcp_memory_service/
│   ├── server.py             # Main server (2,287 lines)
│   ├── storage/
│   │   ├── sqlite_vec.py     # Primary storage
│   │   ├── chroma.py         # Basic ChromaDB  
│   │   └── cloudflare.py     # Production scaling
│   ├── ingestion/            # Document processing
│   └── [essential utilities]
└── data/                     # Persistent storage (Docker volume)
```

## 🔗 Integration

**Claude Desktop:** Add to your MCP configuration:
```json
{
  "mcpServers": {
    "memory": {
      "command": "docker",
      "args": ["exec", "mcp-memory", "python", "/app/scripts/run_memory_server.py"]
    }
  }
}
```

**Other MCP Clients:** Connect to `localhost:4000` for MCP stream protocol.

## 📜 License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

## 🔄 Migration

**From Full Version:**
- Export your data using the full version's backup tools
- Switch to lite branch: `git checkout lite`  
- Import data into SQLite-Vec storage
- Adapt any custom integrations to the minimal tool set

**To Full Version:**
- Your SQLite-Vec data is compatible
- Switch to main branch for web dashboard and advanced features
- Additional dependencies will be installed automatically

---

> 🎯 **Minimal Build Goals**: Fastest deployment, essential features only, Docker-optimized, production-ready core functionality.

