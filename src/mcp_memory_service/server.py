# Copyright 2024 Heinrich Krupp
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
MCP Memory Service
Copyright (c) 2024 Heinrich Krupp
Licensed under the MIT License. See LICENSE file in the project root for full license text.
"""
import sys
import os
import socket
import time
import logging
# Client detection for environment-aware behavior
def detect_mcp_client():
    """Detect which MCP client is running this server."""
    try:
        # Check environment variables for client hints
        if os.environ.get('DOCKER_CONTAINER'):
            return 'docker'
        
        # Fallback: check environment variables
        if os.getenv('CLAUDE_DESKTOP'):
            return 'claude_desktop'
            
        # Default to Claude Desktop for strict JSON compliance
        return 'claude_desktop'
        
    except Exception:
        # If detection fails, default to Claude Desktop (strict mode)
        return 'claude_desktop'

# Detect the current MCP client
MCP_CLIENT = detect_mcp_client()

# Custom logging handler that routes INFO/DEBUG to stdout, WARNING/ERROR to stderr
class DualStreamHandler(logging.Handler):
    """Client-aware handler that adjusts logging behavior based on MCP client."""
    
    def __init__(self, client_type='claude_desktop'):
        super().__init__()
        self.client_type = client_type
        self.stdout_handler = logging.StreamHandler(sys.stdout)
        self.stderr_handler = logging.StreamHandler(sys.stderr)
        
        # Set the same formatter for both handlers
        formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')
        self.stdout_handler.setFormatter(formatter)
        self.stderr_handler.setFormatter(formatter)
    
    def emit(self, record):
        """Route log records based on client type and level."""
        # For Claude Desktop: strict JSON mode - suppress most output, route everything to stderr
        if self.client_type == 'claude_desktop':
            # Only emit WARNING and above to stderr to maintain JSON protocol
            if record.levelno >= logging.WARNING:
                self.stderr_handler.emit(record)
            # Suppress INFO/DEBUG for Claude Desktop to prevent JSON parsing errors
            return
        
        # Enhanced mode with dual-stream (fallback for non-Claude Desktop)
        if record.levelno >= logging.WARNING:  # WARNING, ERROR, CRITICAL
            self.stderr_handler.emit(record)
        else:  # DEBUG, INFO
            self.stdout_handler.emit(record)

# Configure logging with client-aware handler BEFORE any imports that use logging
log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()  # Default to WARNING for performance
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, log_level, logging.WARNING))

# Remove any existing handlers to avoid duplicates
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add our custom client-aware handler
client_aware_handler = DualStreamHandler(client_type=MCP_CLIENT)
root_logger.addHandler(client_aware_handler)

logger = logging.getLogger(__name__)

# Enhanced path detection for Claude Desktop compatibility
def setup_python_paths():
    """Setup Python paths for dependency access."""
    current_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    
    # Check for virtual environment
    potential_venv_paths = [
        os.path.join(current_dir, 'venv', 'Lib', 'site-packages'),  # Windows venv
        os.path.join(current_dir, 'venv', 'lib', 'python3.11', 'site-packages'),  # Linux/Mac venv
        os.path.join(current_dir, '.venv', 'Lib', 'site-packages'),  # Windows .venv
        os.path.join(current_dir, '.venv', 'lib', 'python3.11', 'site-packages'),  # Linux/Mac .venv
    ]
    
    for venv_path in potential_venv_paths:
        if os.path.exists(venv_path):
            sys.path.insert(0, venv_path)
            logger.debug(f"Added venv path: {venv_path}")
            break
    
    # For Claude Desktop: also check if we can access global site-packages
    try:
        import site
        global_paths = site.getsitepackages()
        user_path = site.getusersitepackages()
        
        # Add user site-packages if not blocked by PYTHONNOUSERSITE
        if not os.environ.get('PYTHONNOUSERSITE') and user_path not in sys.path:
            sys.path.append(user_path)
            logger.debug(f"Added user site-packages: {user_path}")
        
        # Add global site-packages if available
        for path in global_paths:
            if path not in sys.path:
                sys.path.append(path)
                logger.debug(f"Added global site-packages: {path}")
                
    except Exception as e:
        logger.warning(f"Could not access site-packages: {e}")

# Setup paths before other imports
setup_python_paths()
import asyncio
import traceback
import json
import platform
from collections import deque
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio
from mcp.types import Resource, Prompt

from . import __version__
from .dependency_check import run_dependency_check, get_recommended_timeout
from .config import (
    CHROMA_PATH,
    BACKUPS_PATH,
    SERVER_NAME,
    SERVER_VERSION,
    STORAGE_BACKEND,
    SQLITE_VEC_PATH,
    INCLUDE_HOSTNAME,
    # Cloudflare configuration
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_VECTORIZE_INDEX,
    CLOUDFLARE_D1_DATABASE_ID,
    CLOUDFLARE_R2_BUCKET,
    CLOUDFLARE_EMBEDDING_MODEL,
    CLOUDFLARE_LARGE_CONTENT_THRESHOLD,
    CLOUDFLARE_MAX_RETRIES,
    CLOUDFLARE_BASE_DELAY
)
# Storage imports will be done conditionally in the server class
from .models.memory import Memory
from .utils.hashing import generate_content_hash
from .utils.system_detection import (
    get_system_info,
    print_system_diagnostics,
    AcceleratorType
)
from .utils.time_parser import extract_time_expression, parse_time_expression
from .utils.json_response import (
    create_json_response,
    create_success_response,
    create_error_response,
    create_paginated_response,
    create_single_memory_response,
    create_operation_response,
    create_health_response,
    create_backup_response,
    memory_to_dict,
    query_result_to_dict
)

# Note: Logging is already configured at the top of the file with dual-stream handler

# Configure performance-critical module logging
if not os.getenv('DEBUG_MODE'):
    # Set higher log levels for performance-critical modules
    for module_name in ['chromadb', 'sentence_transformers', 'transformers', 'torch', 'numpy']:
        logging.getLogger(module_name).setLevel(logging.WARNING)

# Check if UV is being used
def check_uv_environment():
    """Check if UV is being used and provide recommendations if not."""
    running_with_uv = 'UV_ACTIVE' in os.environ or any('uv' in arg.lower() for arg in sys.argv)
    
    if not running_with_uv:
        logger.info("Memory server is running without UV. For better performance and dependency management, consider using UV:")
        logger.info("  pip install uv")
        logger.info("  uv run memory")
    else:
        logger.info("Memory server is running with UV")
    
    return running_with_uv

# Configure environment variables based on detected system
def configure_environment():
    """Configure environment variables based on detected system."""
    system_info = get_system_info()
    
    # Log system information
    logger.info(f"Detected system: {system_info.os_name} {system_info.architecture}")
    logger.info(f"Memory: {system_info.memory_gb:.2f} GB")
    logger.info(f"Accelerator: {system_info.accelerator}")
    
    # Set environment variables for better cross-platform compatibility
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    
    # For Apple Silicon, ensure we use MPS when available
    if system_info.architecture == "arm64" and system_info.os_name == "darwin":
        logger.info("Configuring for Apple Silicon")
        os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
    
    # For Windows with limited GPU memory, use smaller chunks
    if system_info.os_name == "windows" and system_info.accelerator == AcceleratorType.CUDA:
        logger.info("Configuring for Windows with CUDA")
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128"
    
    # For Linux with ROCm, ensure we use the right backend
    if system_info.os_name == "linux" and system_info.accelerator == AcceleratorType.ROCm:
        logger.info("Configuring for Linux with ROCm")
        os.environ["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
    
    # For systems with limited memory, reduce cache sizes
    if system_info.memory_gb < 8:
        logger.info("Configuring for low-memory system")
        os.environ["TRANSFORMERS_CACHE"] = os.path.join(os.path.dirname(CHROMA_PATH), "model_cache")
        os.environ["HF_HOME"] = os.path.join(os.path.dirname(CHROMA_PATH), "hf_cache")
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = os.path.join(os.path.dirname(CHROMA_PATH), "st_cache")

# Configure environment before any imports that might use it
configure_environment()

# Performance optimization environment variables
def configure_performance_environment():
    """Configure environment variables for optimal performance."""
    # PyTorch optimizations
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128,garbage_collection_threshold:0.6"
    
    # CPU optimizations
    os.environ["OMP_NUM_THREADS"] = str(min(8, os.cpu_count() or 1))
    os.environ["MKL_NUM_THREADS"] = str(min(8, os.cpu_count() or 1))
    
    # Disable unnecessary features for performance
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
    
    # Async CUDA operations
    os.environ["CUDA_LAUNCH_BLOCKING"] = "0"

# Apply performance optimizations
configure_performance_environment()

class MemoryServer:
    def __init__(self):
        """Initialize the server with hardware-aware configuration."""
        self.server = Server(SERVER_NAME)
        self.system_info = get_system_info()
        
        # Initialize query time tracking
        self.query_times = deque(maxlen=50)  # Keep last 50 query times for averaging
        
        # Initialize progress tracking
        self.current_progress = {}  # Track ongoing operations
        
        try:
            # Initialize paths
            logger.info(f"Creating directories if they don't exist...")
            os.makedirs(CHROMA_PATH, exist_ok=True)
            os.makedirs(BACKUPS_PATH, exist_ok=True)
            
            # Log system diagnostics
            logger.info(f"Initializing on {platform.system()} {platform.machine()} with Python {platform.python_version()}")
            logger.info(f"Using accelerator: {self.system_info.accelerator}")
            
            # DEFER CHROMADB INITIALIZATION - Initialize storage lazily when needed
            # This prevents hanging during server startup due to embedding model loading
            logger.info(f"Deferring {STORAGE_BACKEND} storage initialization to prevent hanging")
            
            # Initialize storage state
            self.storage = None
            self._storage_initialized = False
        except Exception as e:
            logger.error(f"Initialization error: {str(e)}")
            logger.error(traceback.format_exc())
            
            # Set storage to None to prevent any hanging
            self.storage = None
            self._storage_initialized = False
        
        # Register handlers
        self.register_handlers()
        logger.info("Server initialization complete")
        
        # Test handler registration with proper arguments
        try:
            logger.info("Testing handler registration...")
            capabilities = self.server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={}
            )
            logger.info(f"Server capabilities: {capabilities}")
        except Exception as e:
            logger.error(f"Eager storage initialization failed: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _ensure_storage_initialized(self):
        """Lazily initialize storage backend when needed."""
        if not self._storage_initialized:
            try:
                logger.info(f"Initializing {STORAGE_BACKEND} storage backend...")
                
                if STORAGE_BACKEND == 'sqlite_vec':
                    # Direct SQLite-Vec storage (multi-client coordination removed)
                    import importlib
                    storage_module = importlib.import_module("mcp_memory_service.storage.sqlite_vec")
                    SqliteVecMemoryStorage = storage_module.SqliteVecMemoryStorage
                    self.storage = SqliteVecMemoryStorage(SQLITE_VEC_PATH)
                    logger.info(f"Created SQLite-vec storage at: {SQLITE_VEC_PATH}")
                elif STORAGE_BACKEND == 'cloudflare':
                    # Cloudflare backend using Vectorize, D1, and R2
                    from .storage.cloudflare import CloudflareStorage
                    self.storage = CloudflareStorage(
                        api_token=CLOUDFLARE_API_TOKEN,
                        account_id=CLOUDFLARE_ACCOUNT_ID,
                        vectorize_index=CLOUDFLARE_VECTORIZE_INDEX,
                        d1_database_id=CLOUDFLARE_D1_DATABASE_ID,
                        r2_bucket=CLOUDFLARE_R2_BUCKET,
                        embedding_model=CLOUDFLARE_EMBEDDING_MODEL,
                        large_content_threshold=CLOUDFLARE_LARGE_CONTENT_THRESHOLD,
                        max_retries=CLOUDFLARE_MAX_RETRIES,
                        base_delay=CLOUDFLARE_BASE_DELAY
                    )
                    logger.info(f"Created Cloudflare storage with Vectorize index: {CLOUDFLARE_VECTORIZE_INDEX}")
                else:
                    # ChromaDB backend (deprecated) - Check for migration
                    logger.warning("=" * 70)
                    logger.warning("DEPRECATION WARNING: ChromaDB backend is deprecated!")
                    logger.warning("ChromaDB will be removed in v6.0.0.")
                    logger.warning("Please migrate to SQLite-vec for better performance and reliability.")
                    logger.warning("To migrate your data, run: python scripts/migrate_to_sqlite_vec.py")
                    logger.warning("=" * 70)
                    
                    # Check if ChromaDB has existing data
                    if os.path.exists(CHROMA_PATH) and os.listdir(CHROMA_PATH):
                        logger.warning("")
                        logger.warning("MIGRATION RECOMMENDED: Existing ChromaDB data detected!")
                        logger.warning("Your memories are stored in the deprecated ChromaDB format.")
                        logger.warning("")
                        logger.warning("To migrate now (recommended):")
                        logger.warning("  1. Stop this server (Ctrl+C)")
                        logger.warning("  2. Run: python scripts/migrate_to_sqlite_vec.py")
                        logger.warning("  3. Set environment: export MCP_MEMORY_STORAGE_BACKEND=sqlite_vec")
                        logger.warning("  4. Restart the server")
                        logger.warning("")
                        logger.warning("Continuing with ChromaDB for now...")
                        logger.warning("")
                    
                    from .storage.chroma import ChromaMemoryStorage
                    self.storage = ChromaMemoryStorage(CHROMA_PATH, preload_model=False)
                    logger.info(f"Created ChromaDB storage at: {CHROMA_PATH}")
                
                # Initialize the storage backend
                await self.storage.initialize()
                
                # Verify the storage is properly initialized
                if hasattr(self.storage, 'is_initialized') and not self.storage.is_initialized():
                    # Get detailed status for debugging
                    if hasattr(self.storage, 'get_initialization_status'):
                        status = self.storage.get_initialization_status()
                        logger.error(f"Storage initialization incomplete: {status}")
                    raise RuntimeError("Storage initialization incomplete")
                
                self._storage_initialized = True
                logger.info(f"Storage backend ({STORAGE_BACKEND}) initialization successful")
                
            except Exception as e:
                logger.error(f"Failed to initialize {STORAGE_BACKEND} storage: {str(e)}")
                logger.error(traceback.format_exc())
                # Set storage to None to indicate failure
                self.storage = None
                self._storage_initialized = False
                raise
        return self.storage

    async def initialize(self):
        """Async initialization method with eager storage initialization and timeout."""
        try:
            # Run any async initialization tasks here
            logger.info("Starting async initialization...")

            # Initialize storage eagerly at startup to download models
            logger.info("Initializing storage backend eagerly at startup...")
            await self._ensure_storage_initialized()

            logger.info("Async initialization completed successfully")
            return True
        except Exception as e:
            logger.error(f"Async initialization error: {str(e)}")
            logger.error(traceback.format_exc())
            # Add explicit console error output for Smithery to see
            print(f"Initialization error: {str(e)}", file=sys.stderr, flush=True)
            # Don't raise the exception, just return False
            return False

    def record_query_time(self, query_time_ms: float):
        """Record a query time for averaging."""
        self.query_times.append(query_time_ms)
        logger.debug(f"Recorded query time: {query_time_ms:.2f}ms")

    def get_average_query_time(self) -> float:
        """Get the average query time from recent operations."""
        if not self.query_times:
            return 0.0
        
        avg = sum(self.query_times) / len(self.query_times)
        logger.debug(f"Average query time: {avg:.2f}ms (from {len(self.query_times)} samples)")
        return round(avg, 2)

    def register_handlers(self):
        """Register MCP handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> List[types.Tool]:
            logger.info("=== HANDLING LIST_TOOLS REQUEST ===")
            try:
                tools = [
                    types.Tool(
                        name="store_memory",
                        description="Store memory with optional tags/metadata. Returns hash.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "metadata": {"type": "object"}
                            },
                            "required": ["content"]
                        }
                    ),
                    types.Tool(
                        name="recall_memory",
                        description="Semantic search with natural language time filtering and pagination support.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Supports time expressions (yesterday, last week, Jan 2024)"
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Maximum results to return (optional - returns all if not specified)"
                                },
                                "offset": {
                                    "type": "integer",
                                    "description": "Number of results to skip (optional, default 0)"
                                }
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="search_by_tag",
                        description="Filter memories by tags with AND/OR logic and pagination support.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of tags to search for"
                                },
                                "match_all": {
                                    "type": "boolean",
                                    "description": "If true, match all tags (AND). If false, match any tag (OR)",
                                    "default": False
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Maximum results to return (optional - returns all if not specified)"
                                },
                                "offset": {
                                    "type": "integer",
                                    "description": "Number of results to skip (optional, default 0)"
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="delete_memory",
                        description="Delete memory by hash. Supports single or array.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "hash": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}}
                                    ],
                                    "description": "String or array of strings"
                                }
                            },
                            "required": ["hash"]
                        }
                    ),
                    types.Tool(
                        name="delete_by_tag",
                        description="Delete memories by tags. Permanent operation.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "match_all": {
                                    "type": "boolean",
                                    "description": "true=AND, false=OR",
                                    "default": False
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="get_by_hash",
                        description="Retrieve specific memory by hash.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "hash": {"type": "string"}
                            },
                            "required": ["hash"]
                        }
                    ),
                    types.Tool(
                        name="search_by_content",
                        description="Substring text search in memory content with pagination support.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "search_text": {
                                    "type": "string",
                                    "description": "Text to search for in content"
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Maximum results to return (optional - returns all if not specified)"
                                },
                                "offset": {
                                    "type": "integer",
                                    "description": "Number of results to skip (optional, default 0)"
                                }
                            },
                            "required": ["search_text"]
                        }
                    ),
                    types.Tool(
                        name="update_memory",
                        description="Update memory content/tags/metadata. Content updates regenerate embedding.",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "hash": {"type": "string"},
                                "content": {
                                    "type": "string",
                                    "description": "Optional. Regenerates embedding"
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional. Replaces existing"
                                },
                                "metadata": {
                                    "type": "object",
                                    "description": "Optional. Merges with existing"
                                }
                            },
                            "required": ["hash"]
                        }
                    ),
                    types.Tool(
                        name="check_memory_health",
                        description="Get system health and statistics.",
                        inputSchema={"type": "object", "properties": {}}
                    ),
                    types.Tool(
                        name="backup_memory",
                        description="Create memory backup. Returns location and statistics.",
                        inputSchema={"type": "object", "properties": {}}
                    )
                ]

                # Note: Document ingestion tools removed from MCP
                # Ingestion features are now available in the admin UI for better user experience
                
                logger.info(f"Returning {len(tools)} tools")
                return tools
            except Exception as e:
                logger.error(f"Error in handle_list_tools: {str(e)}")
                logger.error(traceback.format_exc())
                raise
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict | None) -> List[types.TextContent]:
            """Handle tool call requests with routing to appropriate handlers."""
            try:
                if arguments is None:
                    arguments = {}
                
                logger.info(f"Processing tool: {name}")
                if MCP_CLIENT == 'lm_studio':
                    print(f"Processing tool: {name}", file=sys.stdout, flush=True)
                
                if name == "store_memory":
                    return await self.handle_store_memory(arguments)
                elif name == "recall_memory":
                    return await self.handle_recall_memory(arguments)
                elif name == "search_by_tag":
                    return await self.handle_search_by_tag(arguments)
                elif name == "delete_memory":
                    return await self.handle_delete_memory(arguments)
                elif name == "delete_by_tag":
                    return await self.handle_delete_by_tag(arguments)
                elif name == "get_by_hash":
                    return await self.handle_get_by_hash(arguments)
                elif name == "search_by_content":
                    return await self.handle_search_by_content(arguments)
                elif name == "update_memory":
                    return await self.handle_update_memory(arguments)
                elif name == "check_memory_health":
                    logger.info("Calling handle_check_memory_health")
                    return await self.handle_check_memory_health(arguments)
                elif name == "backup_memory":
                    logger.info("Calling handle_backup_memory")
                    return await self.handle_backup_memory(arguments)
                else:
                    logger.warning(f"Unknown tool requested: {name}")
                    raise ValueError(f"Unknown tool: {name}")
            except Exception as e:
                error_msg = f"Error in {name}: {str(e)}\n{traceback.format_exc()}"
                logger.error(error_msg)
                print(f"ERROR in tool execution: {error_msg}", file=sys.stderr, flush=True)
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

    async def handle_dashboard_check_health(self, arguments: dict) -> List[types.TextContent]:
        logger.info("=== EXECUTING DASHBOARD_CHECK_HEALTH ===")
        try:
            # Get real average query time from tracked operations
            avg_query_time = self.get_average_query_time()
            
            # Return actual health status with real query time data
            health_status = {
                "status": "healthy",  # Server is running if we reach this point
                "health": 100,
                "avg_query_time": avg_query_time
            }
            logger.info(f"Health status with real query time: {health_status}")
            result = json.dumps(health_status)
            logger.info(f"Returning JSON: {result}")
            return [types.TextContent(type="text", text=result)]
        except Exception as e:
            logger.error(f"Error in dashboard_check_health: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=json.dumps({"status": "unhealthy", "health": 0, "error": str(e)}))]

    async def handle_store_memory(self, arguments: dict) -> List[types.TextContent]:
        content = arguments.get("content")
        tags = arguments.get("tags", [])
        metadata = arguments.get("metadata", {})

        if not content:
            return [types.TextContent(type="text", text="Error: Content is required")]

        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Normalize tags to a list
            if isinstance(tags, str):
                tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
            elif isinstance(tags, list):
                tags = [str(tag).strip() for tag in tags if str(tag).strip()]
            else:
                tags = []

            # Add optional hostname tracking
            final_metadata = metadata.copy()
            if INCLUDE_HOSTNAME:
                # Prioritize client-provided hostname, then fallback to server
                client_hostname = arguments.get("client_hostname")
                if client_hostname:
                    hostname = client_hostname
                else:
                    hostname = socket.gethostname()

                source_tag = f"source:{hostname}"
                if source_tag not in tags:
                    tags.append(source_tag)
                final_metadata["hostname"] = hostname

            # Create memory object
            content_hash = generate_content_hash(content, final_metadata)
            now = time.time()
            memory = Memory(
                content=content,
                content_hash=content_hash,
                tags=tags,
                metadata=final_metadata,
                created_at=now,
                created_at_iso=datetime.utcfromtimestamp(now).isoformat() + "Z"
            )

            # Store memory
            success, message = await storage.store(memory)

            if success:
                # Return success message with hash for reference
                response = f"✅ {message}\nHash: {content_hash}"
                return [types.TextContent(type="text", text=response)]
            else:
                return [types.TextContent(type="text", text=f"❌ {message}")]
        except Exception as e:
            logger.error(f"Error storing memory: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error storing memory: {str(e)}")]
    
    async def handle_search_by_tag(self, arguments: dict) -> List[types.TextContent]:
        """Tag filtering with pagination support."""
        try:
            tags = arguments.get("tags", [])
            match_all = arguments.get("match_all", False)
            limit = arguments.get("limit")
            offset = arguments.get("offset")

            if not tags:
                return create_error_response("Tags parameter is required")

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Call storage with pagination
            results, total_count = await storage.search_by_tags(
                tags=tags,
                match_all=match_all,
                limit=limit,
                offset=offset
            )

            # Build pagination metadata
            actual_offset = offset if offset is not None else 0
            has_more = limit and (actual_offset + len(results)) < total_count
            next_offset = actual_offset + len(results) if has_more else None

            # Convert to dicts
            memories_data = [memory_to_dict(m) for m in results]

            response = {
                "success": True,
                "memories": memories_data,
                "pagination": {
                    "total": total_count,
                    "limit": limit,
                    "offset": actual_offset,
                    "has_more": has_more,
                    "next_offset": next_offset
                }
            }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in search_by_tag: {e}")
            return create_error_response(str(e))

    async def handle_delete_memory(self, arguments: dict) -> List[types.TextContent]:
        """Delete memory by hash (single or array)."""
        try:
            hash_param = arguments.get("hash")

            if not hash_param:
                return create_error_response("hash parameter is required")

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Handle single hash or array
            if isinstance(hash_param, list):
                deleted_count = 0
                for h in hash_param:
                    success, _ = await storage.delete(h)
                    if success:
                        deleted_count += 1

                response = {
                    "success": True,
                    "affected_count": deleted_count,
                    "message": f"Deleted {deleted_count} of {len(hash_param)} memories"
                }
            else:
                success, message = await storage.delete(hash_param)
                response = {
                    "success": success,
                    "affected_count": 1 if success else 0,
                    "message": f"Memory {hash_param} deleted" if success else f"Failed to delete {hash_param}: {message}"
                }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in delete_memory: {e}")
            return create_error_response(str(e))

    async def handle_delete_by_tag(self, arguments: dict) -> List[types.TextContent]:
        """Delete memories by tags."""
        try:
            tags = arguments.get("tags", [])
            match_all = arguments.get("match_all", False)

            if not tags:
                return create_error_response("tags parameter is required")

            # Convert single string to array if needed for backward compatibility
            if isinstance(tags, str):
                tags = [tags]

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            deleted_count, message = await storage.delete_by_tag(tags, match_all=match_all)

            response = {
                "success": True,
                "affected_count": deleted_count,
                "message": f"Deleted {deleted_count} memories matching tags: {tags}"
            }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in delete_by_tag: {e}")
            return create_error_response(str(e))


    async def handle_update_memory(self, arguments: dict) -> List[types.TextContent]:
        """Update memory content/tags/metadata."""
        try:
            hash_value = arguments.get("hash")
            content = arguments.get("content")
            tags = arguments.get("tags")
            metadata = arguments.get("metadata")

            if not hash_value:
                return create_error_response("hash parameter is required")

            if content is None and tags is None and metadata is None:
                return create_error_response("At least one of content, tags, or metadata must be provided")

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Perform update
            success, message = await storage.update_memory(
                hash=hash_value,
                content=content,
                tags=tags,
                metadata=metadata
            )

            if success:
                response = {
                    "success": True,
                    "affected_count": 1,
                    "message": f"Memory {hash_value} updated successfully"
                }
                return create_json_response(response)
            else:
                return create_error_response(f"Failed to update memory: {message}")

        except Exception as e:
            logger.error(f"Error in update_memory: {e}")
            return create_error_response(str(e))

    async def handle_get_by_hash(self, arguments: dict) -> List[types.TextContent]:
        """Retrieve single memory by hash."""
        try:
            hash_value = arguments.get("hash")

            if not hash_value:
                return create_error_response("hash parameter is required")

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            memory = await storage.get_by_hash(hash_value)

            if not memory:
                return create_error_response(f"Memory not found: {hash_value}")

            response = {
                "success": True,
                "memory": memory_to_dict(memory)
            }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in get_by_hash: {e}")
            return create_error_response(str(e))

    async def handle_search_by_content(self, arguments: dict) -> List[types.TextContent]:
        """Content search with pagination support."""
        try:
            search_text = arguments.get("search_text")
            limit = arguments.get("limit")  # Optional - returns all if None
            offset = arguments.get("offset", 0)

            if not search_text:
                return create_error_response("search_text parameter is required")

            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Call storage with pagination
            results, total_count = await storage.search_by_content(
                search_text=search_text,
                limit=limit,
                offset=offset
            )

            # Convert to dicts
            memories_data = [memory_to_dict(m) for m in results]

            # Build pagination metadata
            has_more = (offset + len(results)) < total_count
            next_offset = offset + len(results) if has_more else None

            response = {
                "success": True,
                "memories": memories_data,
                "pagination": {
                    "total": total_count,
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "next_offset": next_offset
                }
            }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in search_by_content: {e}")
            return create_error_response(str(e))

    async def handle_recall_memory(self, arguments: dict) -> List[types.TextContent]:
        """
        Handle memory recall requests with natural language time expressions and pagination.

        This handler parses natural language time expressions from the query,
        extracts time ranges, and combines them with optional semantic search.

        Args:
            query: Search query with optional time expressions
            limit: Maximum results to return (optional - returns all if not specified)
            offset: Number of results to skip (optional, default 0)

        Returns:
            JSON response with memories and pagination metadata
        """
        query = arguments.get("query", "")
        limit = arguments.get("limit")  # Optional - returns all if None
        offset = arguments.get("offset", 0)  # Default to 0

        if not query:
            return create_error_response("Query parameter is required")

        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()

            # Parse natural language time expressions
            cleaned_query, (start_timestamp, end_timestamp) = extract_time_expression(query)

            # Log the parsed timestamps and clean query
            logger.info(f"Original query: {query}")
            logger.info(f"Cleaned query for semantic search: {cleaned_query}")
            logger.info(f"Parsed time range: {start_timestamp} to {end_timestamp}")

            # Log more detailed timestamp information for debugging
            if start_timestamp is not None:
                start_dt = datetime.fromtimestamp(start_timestamp)
                logger.info(f"Start timestamp: {start_timestamp} ({start_dt.strftime('%Y-%m-%d %H:%M:%S')})")
            if end_timestamp is not None:
                end_dt = datetime.fromtimestamp(end_timestamp)
                logger.info(f"End timestamp: {end_timestamp} ({end_dt.strftime('%Y-%m-%d %H:%M:%S')})")

            if start_timestamp is None and end_timestamp is None:
                # No time expression found, try direct parsing
                logger.info("No time expression found in query, trying direct parsing")
                start_timestamp, end_timestamp = parse_time_expression(query)
                logger.info(f"Direct parse result: {start_timestamp} to {end_timestamp}")

            # Retrieve memories with timestamp filter and optional semantic search
            # If cleaned_query is empty, just whitespace, or "*" (wildcard for list all),
            # we should perform time-based retrieval only
            cleaned = cleaned_query.strip()
            semantic_query = None if (not cleaned or cleaned == "*") else cleaned

            # Use the enhanced recall method from ChromaMemoryStorage that combines
            # semantic search with time filtering, or just time filtering if no semantic query
            # Fetch a large number for filtering, then paginate
            results = await storage.recall(
                query=semantic_query,
                n_results=10000,  # Fetch large set for time filtering
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )

            # Note: storage.recall doesn't yet support pagination, so we apply it here
            # TODO: Update storage.recall to support limit/offset parameters
            total_count = len(results) if results else 0

            # Apply pagination
            if limit is not None:
                # Paginate: skip offset, take limit
                results = results[offset:offset + limit]
            elif offset > 0:
                # Only offset specified, return all from offset onwards
                results = results[offset:]
            # else: return all results (no limit, no offset)

            # Use helper to create paginated response
            return create_paginated_response(
                memories=results,
                total_count=total_count,
                limit=limit if limit is not None else total_count,
                offset=offset,
                is_query_result=True  # Results are MemoryQueryResult objects
            )

        except Exception as e:
            logger.error(f"Error in recall_memory: {str(e)}\n{traceback.format_exc()}")
            return create_error_response(str(e))

    async def handle_check_memory_health(self, arguments: dict) -> List[types.TextContent]:
        """Handle memory health check requests with performance metrics."""
        logger.info("=== EXECUTING CHECK_MEMORY_HEALTH ===")
        try:
            # Initialize storage lazily when needed
            try:
                storage = await self._ensure_storage_initialized()
            except Exception as init_error:
                # Storage initialization failed
                health_data = {
                    "validation": {
                        "status": "unhealthy",
                        "message": f"Storage initialization failed: {str(init_error)}"
                    },
                    "statistics": {
                        "status": "error",
                        "error": "Cannot get statistics - storage not initialized"
                    },
                    "performance": {
                        "storage": {},
                        "server": {
                            "average_query_time_ms": self.get_average_query_time(),
                            "total_queries": len(self.query_times)
                        }
                    }
                }

                logger.error(f"Storage initialization failed during health check: {str(init_error)}")

                response = {
                    "success": False,
                    "health": health_data,
                    "error": f"Storage initialization failed: {str(init_error)}"
                }

                return create_json_response(response)
            
            # Skip db_utils completely for health check - implement directly here
            # Get storage type for backend-specific handling
            storage_type = storage.__class__.__name__
            
            # Direct health check implementation based on storage type
            is_valid = False
            message = ""
            stats = {}
            
            if storage_type == "SqliteVecMemoryStorage":
                # Direct SQLite-vec validation
                if not hasattr(storage, 'conn') or storage.conn is None:
                    is_valid = False
                    message = "SQLite database connection is not initialized"
                else:
                    try:
                        # Check for required tables
                        cursor = storage.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
                        if not cursor.fetchone():
                            is_valid = False
                            message = "SQLite database is missing required tables"
                        else:
                            # Count memories
                            cursor = storage.conn.execute('SELECT COUNT(*) FROM memories')
                            memory_count = cursor.fetchone()[0]
                            
                            # Check if embedding tables exist
                            cursor = storage.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_embeddings'")
                            has_embeddings = cursor.fetchone() is not None
                            
                            # Check embedding model
                            has_model = hasattr(storage, 'embedding_model') and storage.embedding_model is not None
                            
                            # Collect stats
                            stats = {
                                "status": "healthy",
                                "backend": "sqlite-vec",
                                "total_memories": memory_count,
                                "has_embedding_tables": has_embeddings,
                                "has_embedding_model": has_model,
                                "embedding_model": storage.embedding_model_name if hasattr(storage, 'embedding_model_name') else "none"
                            }
                            
                            # Get database file size
                            db_path = storage.db_path if hasattr(storage, 'db_path') else None
                            if db_path and os.path.exists(db_path):
                                file_size = os.path.getsize(db_path)
                                stats["database_size_bytes"] = file_size
                                stats["database_size_mb"] = round(file_size / (1024 * 1024), 2)
                            
                            is_valid = True
                            message = "SQLite-vec database validation successful"
                    except Exception as e:
                        is_valid = False
                        message = f"SQLite database validation error: {str(e)}"
                        stats = {
                            "status": "error",
                            "error": str(e),
                            "backend": "sqlite-vec" 
                        }
            
            elif hasattr(storage, 'collection'):
                # Standard ChromaDB validation
                if storage.collection is None:
                    is_valid = False
                    message = "Collection is not initialized"
                    stats = {
                        "status": "error",
                        "error": "Collection is not initialized",
                        "backend": "chromadb"
                    }
                else:
                    try:
                        # Count documents
                        collection_count = storage.collection.count()
                        
                        # Get collection metadata
                        metadata = {}
                        if hasattr(storage.collection, 'metadata'):
                            metadata = storage.collection.metadata
                        
                        # Get embedding function info
                        embedding_function = "none"
                        if hasattr(storage, 'embedding_function') and storage.embedding_function:
                            embedding_function = storage.embedding_function.__class__.__name__
                        
                        # Collect stats
                        stats = {
                            "status": "healthy",
                            "backend": "chromadb",
                            "total_memories": collection_count,
                            "embedding_function": embedding_function,
                            "metadata": metadata
                        }
                        
                        # Check database path and size
                        if hasattr(storage, 'path'):
                            db_path = storage.path
                            if os.path.exists(db_path):
                                total_size = 0
                                for dirpath, _, filenames in os.walk(db_path):
                                    for f in filenames:
                                        fp = os.path.join(dirpath, f)
                                        total_size += os.path.getsize(fp)
                                
                                stats["database_size_bytes"] = total_size
                                stats["database_size_mb"] = round(total_size / (1024 * 1024), 2)
                        
                        is_valid = True
                        message = "ChromaDB validation successful"
                    except Exception as e:
                        is_valid = False
                        message = f"ChromaDB validation error: {str(e)}"
                        stats = {
                            "status": "error",
                            "error": str(e),
                            "backend": "chromadb"
                        }
            else:
                is_valid = False
                message = f"Unknown storage type: {storage_type}"
                stats = {
                    "status": "error",
                    "error": f"Unknown storage type: {storage_type}",
                    "backend": "unknown"
                }
            
            # Get performance stats from optimized storage
            performance_stats = {}
            if hasattr(storage, 'get_performance_stats') and callable(storage.get_performance_stats):
                try:
                    performance_stats = storage.get_performance_stats()
                except Exception as perf_error:
                    logger.warning(f"Could not get performance stats: {str(perf_error)}")
                    performance_stats = {"error": str(perf_error)}
            
            # Get server-level performance stats
            server_stats = {
                "average_query_time_ms": self.get_average_query_time(),
                "total_queries": len(self.query_times)
            }
            
            # Add storage type for debugging
            server_stats["storage_type"] = storage_type
            
            # Add storage initialization status for debugging
            if hasattr(storage, 'get_initialization_status') and callable(storage.get_initialization_status):
                try:
                    server_stats["storage_initialization"] = storage.get_initialization_status()
                except Exception:
                    pass
            
            # Combine results with performance data
            health_data = {
                "version": __version__,
                "validation": {
                    "status": "healthy" if is_valid else "unhealthy",
                    "message": message
                },
                "statistics": stats,
                "performance": {
                    "storage": performance_stats,
                    "server": server_stats
                }
            }

            logger.info(f"Database health result with performance data: {health_data}")

            response = {
                "success": True,
                "health": health_data
            }

            return create_json_response(response)

        except Exception as e:
            logger.error(f"Error in check_memory_health: {e}")
            logger.error(traceback.format_exc())
            return create_error_response(str(e))

    async def handle_ingest_document(self, arguments: dict) -> List[types.TextContent]:
        """Handle document ingestion requests."""
        try:
            from pathlib import Path
            from .ingestion import get_loader_for_file
            from .models.memory import Memory
            from .utils import generate_content_hash
            import time
            
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            file_path = Path(arguments["file_path"])
            tags = arguments.get("tags", [])
            chunk_size = arguments.get("chunk_size", 1000)
            chunk_overlap = arguments.get("chunk_overlap", 200)
            
            logger.info(f"Starting document ingestion: {file_path}")
            start_time = time.time()
            
            # Get appropriate document loader
            loader = get_loader_for_file(file_path)
            if loader is None:
                return [types.TextContent(
                    type="text",
                    text=f"Error: Unsupported file format: {file_path.suffix}"
                )]
            
            # Configure loader
            loader.chunk_size = chunk_size
            loader.chunk_overlap = chunk_overlap
            
            chunks_processed = 0
            chunks_stored = 0
            errors = []
            
            # Extract and store chunks
            async for chunk in loader.extract_chunks(file_path):
                chunks_processed += 1
                
                try:
                    # Combine document tags with chunk metadata tags
                    all_tags = tags.copy()
                    if chunk.metadata.get('tags'):
                        all_tags.extend(chunk.metadata['tags'])
                    
                    # Create memory object
                    memory = Memory(
                        content=chunk.content,
                        content_hash=generate_content_hash(chunk.content, chunk.metadata),
                        tags=list(set(all_tags)),  # Remove duplicates
                        metadata=chunk.metadata
                    )
                    
                    # Store the memory
                    success, error = await storage.store(memory)
                    if success:
                        chunks_stored += 1
                    else:
                        errors.append(f"Chunk {chunk.chunk_index}: {error}")
                        
                except Exception as e:
                    errors.append(f"Chunk {chunk.chunk_index}: {str(e)}")
            
            processing_time = time.time() - start_time
            success_rate = (chunks_stored / chunks_processed * 100) if chunks_processed > 0 else 0
            
            # Prepare result message
            result_lines = [
                f"✅ Document ingestion completed: {file_path.name}",
                f"📄 Chunks processed: {chunks_processed}",
                f"💾 Chunks stored: {chunks_stored}",
                f"⚡ Success rate: {success_rate:.1f}%",
                f"⏱️  Processing time: {processing_time:.2f} seconds"
            ]
            
            if errors:
                result_lines.append(f"⚠️  Errors encountered: {len(errors)}")
                if len(errors) <= 5:  # Show first few errors
                    result_lines.extend([f"   - {error}" for error in errors[:5]])
                else:
                    result_lines.extend([f"   - {error}" for error in errors[:3]])
                    result_lines.append(f"   ... and {len(errors) - 3} more errors")
            
            logger.info(f"Document ingestion completed: {chunks_stored}/{chunks_processed} chunks stored")
            return [types.TextContent(type="text", text="\n".join(result_lines))]
            
        except Exception as e:
            logger.error(f"Error in document ingestion: {str(e)}")
            return [types.TextContent(
                type="text",
                text=f"Error ingesting document: {str(e)}"
            )]

    async def handle_ingest_directory(self, arguments: dict) -> List[types.TextContent]:
        """Handle directory ingestion requests."""
        try:
            from pathlib import Path
            from .ingestion import get_loader_for_file, is_supported_file
            from .models.memory import Memory
            from .utils import generate_content_hash
            import time
            
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            directory_path = Path(arguments["directory_path"])
            tags = arguments.get("tags", [])
            recursive = arguments.get("recursive", True)
            file_extensions = arguments.get("file_extensions", ["pdf", "txt", "md", "json"])
            chunk_size = arguments.get("chunk_size", 1000)
            max_files = arguments.get("max_files", 100)
            
            if not directory_path.exists() or not directory_path.is_dir():
                return [types.TextContent(
                    type="text",
                    text=f"Error: Directory not found: {directory_path}"
                )]
            
            logger.info(f"Starting directory ingestion: {directory_path}")
            start_time = time.time()
            
            # Find all supported files
            pattern = "**/*" if recursive else "*"
            all_files = []
            
            for ext in file_extensions:
                ext_pattern = f"*.{ext.lstrip('.')}"
                if recursive:
                    files = list(directory_path.rglob(ext_pattern))
                else:
                    files = list(directory_path.glob(ext_pattern))
                all_files.extend(files)
            
            # Remove duplicates and filter supported files
            unique_files = []
            seen = set()
            for file_path in all_files:
                if file_path not in seen and is_supported_file(file_path):
                    unique_files.append(file_path)
                    seen.add(file_path)
            
            # Limit number of files
            files_to_process = unique_files[:max_files]
            
            if not files_to_process:
                return [types.TextContent(
                    type="text",
                    text=f"No supported files found in directory: {directory_path}"
                )]
            
            total_chunks_processed = 0
            total_chunks_stored = 0
            files_processed = 0
            files_failed = 0
            all_errors = []
            
            # Process each file
            for file_path in files_to_process:
                try:
                    logger.info(f"Processing file {files_processed + 1}/{len(files_to_process)}: {file_path.name}")
                    
                    # Get appropriate document loader
                    loader = get_loader_for_file(file_path)
                    if loader is None:
                        all_errors.append(f"{file_path.name}: Unsupported format")
                        files_failed += 1
                        continue
                    
                    # Configure loader
                    loader.chunk_size = chunk_size
                    
                    file_chunks_processed = 0
                    file_chunks_stored = 0
                    
                    # Extract and store chunks from this file
                    async for chunk in loader.extract_chunks(file_path):
                        file_chunks_processed += 1
                        total_chunks_processed += 1
                        
                        try:
                            # Add directory-level tags and file-specific tags
                            all_tags = tags.copy()
                            all_tags.append(f"source_dir:{directory_path.name}")
                            all_tags.append(f"file_type:{file_path.suffix.lstrip('.')}")
                            
                            if chunk.metadata.get('tags'):
                                all_tags.extend(chunk.metadata['tags'])
                            
                            # Create memory object
                            memory = Memory(
                                content=chunk.content,
                                content_hash=generate_content_hash(chunk.content, chunk.metadata),
                                tags=list(set(all_tags)),  # Remove duplicates
                                metadata=chunk.metadata
                            )
                            
                            # Store the memory
                            success, error = await storage.store(memory)
                            if success:
                                file_chunks_stored += 1
                                total_chunks_stored += 1
                            else:
                                all_errors.append(f"{file_path.name} chunk {chunk.chunk_index}: {error}")
                                
                        except Exception as e:
                            all_errors.append(f"{file_path.name} chunk {chunk.chunk_index}: {str(e)}")
                    
                    if file_chunks_stored > 0:
                        files_processed += 1
                    else:
                        files_failed += 1
                        
                except Exception as e:
                    files_failed += 1
                    all_errors.append(f"{file_path.name}: {str(e)}")
            
            processing_time = time.time() - start_time
            success_rate = (total_chunks_stored / total_chunks_processed * 100) if total_chunks_processed > 0 else 0
            
            # Prepare result message
            result_lines = [
                f"✅ Directory ingestion completed: {directory_path.name}",
                f"📁 Files processed: {files_processed}/{len(files_to_process)}",
                f"📄 Total chunks processed: {total_chunks_processed}",
                f"💾 Total chunks stored: {total_chunks_stored}",
                f"⚡ Success rate: {success_rate:.1f}%",
                f"⏱️  Processing time: {processing_time:.2f} seconds"
            ]
            
            if files_failed > 0:
                result_lines.append(f"❌ Files failed: {files_failed}")
            
            if all_errors:
                result_lines.append(f"⚠️  Total errors: {len(all_errors)}")
                # Show first few errors
                error_limit = 5
                for error in all_errors[:error_limit]:
                    result_lines.append(f"   - {error}")
                if len(all_errors) > error_limit:
                    result_lines.append(f"   ... and {len(all_errors) - error_limit} more errors")
            
            logger.info(f"Directory ingestion completed: {total_chunks_stored}/{total_chunks_processed} chunks from {files_processed} files")
            return [types.TextContent(type="text", text="\n".join(result_lines))]
            
        except Exception as e:
            logger.error(f"Error in directory ingestion: {str(e)}")
            return [types.TextContent(
                type="text",
                text=f"Error ingesting directory: {str(e)}"
            )]

    async def handle_backup_memory(self, arguments: dict) -> List[types.TextContent]:
        """Create memory backup with WAL checkpoint."""
        logger.info("=== EXECUTING BACKUP_MEMORY ===")
        try:
            import os
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"memory_backup_{timestamp}"
            backup_dir = os.path.join(BACKUPS_PATH, backup_name)

            # Use storage backend's backup method
            success, message, info = await self.storage.create_backup(backup_dir)

            if success:
                backup_info = {
                    "backup_path": backup_dir,
                    "backup_size_mb": info.get("file_size_mb", 0),
                    "created_at": datetime.now().isoformat(),
                    "wal_checkpoint_performed": info.get("wal_checkpointed", False),
                    "memory_count": info.get("memory_count", 0),
                    "backend": STORAGE_BACKEND
                }

                logger.info(f"Backup created successfully: {backup_dir}")

                response = {
                    "success": True,
                    "backup": backup_info
                }

                return create_json_response(response)
            else:
                logger.error(f"Backup failed: {message}")
                return create_error_response(f"Failed to create backup: {message}")

        except Exception as e:
            logger.error(f"Error in backup_memory: {e}")
            return create_error_response(str(e))


async def async_main():
    # Compatibility patches removed for minimal build
    
    # Run dependency check before starting
    run_dependency_check()
    
    # Check if running with UV
    check_uv_environment()
    
    # Debug logging is now handled by the CLI layer
    # CHROMA_PATH is now handled by config.py reading from MCP_MEMORY_CHROMA_PATH environment variable
    
    # Print system diagnostics (avoid JSON parsing errors in Claude Desktop)
    system_info = get_system_info()
    
    # Initialize and run the memory server
    logger.info("Creating MemoryServer instance...")
    memory_server = MemoryServer()
    
    # Initialize the server asynchronously
    logger.info("Starting server initialization process...")
    init_success = await memory_server.initialize()
    logger.info(f"Server initialization result: {init_success}")
    if not init_success:
        raise RuntimeError("Failed to initialize memory server")
    
    # Run the server with stdio
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("Server started and ready to handle requests")
        
        await memory_server.server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                # Use the latest protocol version to ensure compatibility with all clients
                protocol_version="2024-11-05",
                capabilities=memory_server.server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={
                        "hardware_info": {
                            "architecture": system_info.architecture,
                            "accelerator": system_info.accelerator,
                            "memory_gb": system_info.memory_gb,
                            "cpu_count": system_info.cpu_count
                        }
                    }
                )
            )
        )
def main():
    import signal
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Check if running in Docker
        if os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER', False):
            logger.info("Running in Docker container")
        
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main()
