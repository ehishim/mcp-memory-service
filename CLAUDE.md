# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory Context Reference (Optional)

This project supports enhanced context through MCP Memory Service integration. If you have a local MCP Memory Service instance running, you can store and retrieve project context to reduce token usage in Claude Code sessions.

### Setup Memory Context (Optional)
1. **Deploy MCP Memory Service**: Follow deployment instructions for your environment
2. **Store Reference Memories**: Use the `distributable-reference` tag for shareable context
3. **Create Local CLAUDE_MEMORY.md**: Add your memory hashes (git-ignored)

### Memory Categories for Storage
- **Project Structure**: Server architecture, file locations, component relationships
- **Key Commands**: Installation, testing, debugging, deployment commands
- **Environment Variables**: Configuration options and platform-specific settings
- **Recent Changes**: Version history, resolved issues, breaking changes
- **Testing Practices**: Framework preferences, test patterns, validation steps

### Local Memory Configuration
Create `CLAUDE_MEMORY.md` (git-ignored) with your memory service details:
```markdown
# Local Memory Hashes
- Memory Service: https://your-instance:port
- Auth: Bearer your-api-key
- Retrieve Command: curl -k -s -X POST https://your-instance:port/mcp -H "Authorization: Bearer your-key" -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_by_tag", "arguments": {"tags": ["claude-code-reference"]}}}'
```

### Memory Management Guidelines
- **Review Schedule**: Quarterly review of reference memories for accuracy
- **Distribution**: Use `distributable-reference` tag for team sharing
- **Export Tool**: `./scripts/export_distributable_memories.sh` for network distribution
- **Documentation**: Keep memory hashes in local files, not in version control

## Overview

MCP Memory Service is a Model Context Protocol server that provides semantic memory and persistent storage capabilities for Claude Desktop using ChromaDB and sentence transformers. The project enables long-term memory storage with semantic search across conversations.

## Key Commands

### Development
- **Install dependencies**: `python install.py` (platform-aware installation)
- **Run server**: `python scripts/run_memory_server.py` or `uv run memory`
- **Run tests**: `pytest tests/`
- **Run specific test**: `pytest tests/unit/test_memory_models.py::TestMemoryModel::test_memory_creation`
- **Check environment**: `python scripts/verify_environment.py`
- **Debug with MCP Inspector**: `npx @modelcontextprotocol/inspector uv --directory /path/to/repo run memory`
- **Check documentation links**: `python scripts/check_documentation_links.py` (validates all internal markdown links)
- **Test Docker functionality**: `python scripts/test_docker_functionality.py` (comprehensive Docker container verification)
- **Find and remove duplicates**: `python scripts/find_duplicates.py --execute` (removes duplicate memories from database)
- **Clean corrupted encoding**: `python scripts/cleanup_corrupted_encoding.py --execute` (removes memories with corrupted emoji encoding)
- **Setup git merge drivers**: `./scripts/setup-git-merge-drivers.sh` (one-time setup for new contributors)
- **Store memory**: `/memory-store "content"` - Store information directly to MCP Memory Service at narrowbox.local:8443

### Build & Package
- **Build package**: `python -m build`
- **Install locally**: `pip install -e .`

## Architecture

### Core Components

1. **Server Layer** (`src/mcp_memory_service/server.py`)
   - Implements MCP protocol with async request handlers
   - Global model and embedding caches for performance
   - Handles all memory operations (store, retrieve, search, delete)

2. **Storage Abstraction** (`src/mcp_memory_service/storage/`)
   - `base.py`: Abstract interface for storage backends
   - `chroma.py`: ChromaDB implementation
   - `chroma_enhanced.py`: Extended features (time parsing, advanced search)

3. **Models** (`src/mcp_memory_service/models/memory.py`)
   - `Memory`: Core dataclass for memory entries
   - `MemoryMetadata`: Metadata structure
   - All models use Python dataclasses with type hints

4. **Configuration** (`src/mcp_memory_service/config.py`)
   - Environment-based configuration
   - Platform-specific optimizations
   - Hardware acceleration detection

### Key Design Patterns

- **Async/Await**: All I/O operations are async
- **Type Safety**: Comprehensive type hints (Python 3.10+)
- **Error Handling**: Specific exception types with clear messages
- **Caching**: Global caches for models and embeddings to improve performance
- **Platform Detection**: Automatic hardware optimization (CUDA, MPS, DirectML, ROCm)

### MCP Protocol Operations

Memory operations implemented:
- `store_memory`: Store new memories with tags and metadata
- `retrieve_memory`: Basic retrieval by query
- `recall_memory`: Natural language time-based retrieval
- `search_by_tag`: Tag-based search
- `delete_memory`: Delete specific memories
- `delete_by_tag/tags`: Bulk deletion by tags
- `optimize_db`: Database optimization
- `check_database_health`: Health monitoring
- `debug_retrieve`: Similarity analysis for debugging

### Testing

Tests are organized by type:
- `tests/unit/`: Unit tests for individual components
- `tests/integration/`: Integration tests for full workflows
- `tests/performance/`: Performance benchmarks

Run tests with coverage: `pytest --cov=src/mcp_memory_service tests/`

### Environment Variables

Key configuration:
- `MCP_MEMORY_CHROMA_PATH`: ChromaDB storage location (default: `~/.mcp_memory_chroma`)
- `MCP_MEMORY_BACKUPS_PATH`: Backup location (default: `~/.mcp_memory_backups`)
- `MCP_MEMORY_INCLUDE_HOSTNAME`: Enable automatic machine identification (default: `false`)
  - When enabled, adds client hostname as `source:hostname` tag to stored memories
  - Clients can specify hostname via `client_hostname` parameter or `X-Client-Hostname` header
  - Fallback to server hostname if client doesn't provide one
- `MCP_API_KEY`: API key for HTTP authentication (optional, no default)
- `LOG_LEVEL`: Logging verbosity (DEBUG, INFO, WARNING, ERROR)
- Platform-specific: `PYTORCH_ENABLE_MPS_FALLBACK`, `MCP_MEMORY_USE_ONNX`

#### API Key Configuration

The `MCP_API_KEY` environment variable enables HTTP API authentication:

```bash
# Generate a secure API key
export MCP_API_KEY="$(openssl rand -base64 32)"

# Or set manually
export MCP_API_KEY="your-secure-api-key-here"
```

When set, all HTTP API requests require the `Authorization: Bearer <api_key>` header. This is essential for production deployments and multi-client setups.

### Platform Support

The codebase includes platform-specific optimizations:
- **macOS**: MPS acceleration for Apple Silicon, CPU fallback for Intel
- **Windows**: CUDA, DirectML, or CPU
- **Linux**: CUDA, ROCm, or CPU

Hardware detection is automatic via `utils/system_detection.py`.

### Memory Storage Command

The `/memory-store` command allows direct storage of information to the MCP Memory Service:

**Basic Usage:**
```bash
/memory-store "content to store"
```

**Advanced Usage:**
- Automatically detects project context and adds relevant tags
- Captures git repository information and recent commits
- Adds client hostname via the hostname capture feature
- Uses direct curl to `https://narrowbox.local:8443/api/memories`
- No temporary files or confirmation prompts required

**Example Patterns:**
```bash
/memory-store "Fixed critical bug in hostname capture logic"
/memory-store "Decision: Use SQLite-vec for better performance than ChromaDB"
/memory-store "TODO: Update Docker configuration after database backend change"
```

The command will:
1. Analyze current working directory and git context
2. Generate appropriate tags (project name, file types, git commits)
3. Store directly via curl with proper JSON formatting
4. Return content hash and applied tags for confirmation

### Development Tips

1. When modifying storage backends, ensure compatibility with the abstract base class
2. Memory operations should handle duplicates gracefully (content hashing)
3. Time parsing supports natural language ("yesterday", "last week")
4. Use the debug_retrieve operation to analyze similarity scores
5. The server maintains global state for models - be careful with concurrent modifications
6. All new features should include corresponding tests
7. Use semantic commit messages for version management
8. Use `/memory-store` to capture important decisions and context during development

### Deployment & Debugging

#### Remote Server Deployment Process
When deploying changes to production servers (e.g., 10.0.1.30:8443):

1. **Pre-deployment**: Test changes locally first
2. **Deploy**: SSH to server, `git pull`, restart service
3. **Verify**: Check `/api/health` for new version number
4. **Test**: Verify web frontend loads at https://server:8443/

#### Web Frontend Debugging
Common issues and solutions for internal server errors:

**Python Syntax Errors in HTML Templates**:
- **Problem**: F-strings or `.format()` with CSS cause syntax errors
- **Symptom**: `SyntaxError: invalid decimal literal` or `KeyError` with CSS fragments
- **Solution**: Use string concatenation instead: `"text" + variable + "more text"`
- **Example**: Replace `f"CSS {color: blue;}"` with `"CSS " + color_var + ": blue;"`

**Cache-Related Issues**:
- **Problem**: Code changes not taking effect despite restart
- **Symptoms**: Old behavior persists, web frontend shows errors
- **Solution**: Clear all Python caches:
  ```bash
  # Clear Python bytecode cache
  find . -name "*.pyc" -delete
  find . -name "__pycache__" -type d -exec rm -rf {} +
  
  # Clear UV cache (can be several GB)
  uv cache clean
  
  # Kill all running server processes
  pkill -f "run_server.py"
  pkill -f "python -m src.mcp_memory_service.server"
  
  # Restart with fresh environment
  ```

**Environment Reset Procedure**:
When persistent issues occur:
1. Stop all services: `sudo systemctl stop mcp-memory-service`
2. Clear caches (see above)
3. Reset UV environment: `rm -rf .venv && uv sync`
4. Restart service: `sudo systemctl start mcp-memory-service`
5. Monitor logs: `journalctl -u mcp-memory-service -f`

#### Version Mismatch Issues
- **Problem**: Health endpoints show old version after deployment
- **Check**: Verify `__version__` import in affected files
- **Files to verify**: `server.py`, `web/app.py`, `web/api/health.py`
- **Solution**: Ensure all files import `from . import __version__` or `from ... import __version__`

#### Backend Method Compatibility
- **Problem**: Web API calls methods not available in storage backend
- **Example**: API calls `search_by_tags` but backend only has `search_by_tag`
- **Solution**: Implement missing methods with backward compatibility
- **Check**: Verify API endpoints match storage backend method names

### Git Configuration

#### Automated uv.lock Conflict Resolution

The repository includes automated resolution for `uv.lock` conflicts:

1. **For new contributors**: Run `./scripts/setup-git-merge-drivers.sh` once after cloning
2. **How it works**: 
   - Git automatically resolves `uv.lock` conflicts using the incoming version
   - Then runs `uv sync` to regenerate the lock file based on your `pyproject.toml`
   - Ensures consistent dependency resolution across all environments

Files involved:
- `.gitattributes`: Defines merge strategy for `uv.lock`
- `scripts/uv-lock-merge.sh`: Custom merge driver script
- `scripts/setup-git-merge-drivers.sh`: One-time setup for contributors

### Common Issues

1. **MPS Fallback**: On macOS, if MPS fails, set `PYTORCH_ENABLE_MPS_FALLBACK=1`
2. **ONNX Runtime**: For compatibility issues, use `MCP_MEMORY_USE_ONNX=true`
3. **ChromaDB Persistence**: Ensure write permissions for storage paths
4. **Memory Usage**: Model loading is deferred until first use to reduce startup time
5. **uv.lock Conflicts**: Should resolve automatically; if not, ensure git merge drivers are set up
- add the fact that we can add discussion content via github graphql to memory