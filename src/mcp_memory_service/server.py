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

    def register_handlers(self):
        """Register MCP handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> List[types.Tool]:
            logger.info("=== HANDLING LIST_TOOLS REQUEST ===")
            try:
                tools = [
                    types.Tool(
                        name="store_memory",
                        description="""Store new information with optional tags.

                        Accepts two tag formats in metadata:
                        - Array: ["tag1", "tag2"]
                        - String: "tag1,tag2"

                       Examples:
                        # Using array format:
                        {
                            "content": "Memory content",
                            "metadata": {
                                "tags": ["important", "reference"],
                                "type": "note"
                            }
                        }

                        # Using string format(preferred):
                        {
                            "content": "Memory content",
                            "metadata": {
                                "tags": "important,reference",
                                "type": "note"
                            }
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "The memory content to store, such as a fact, note, or piece of information."
                                },
                                "metadata": {
                                    "type": "object",
                                    "description": "Optional metadata about the memory, including tags and type.",
                                    "properties": {
                                        "tags": {
                                            "oneOf": [
                                                {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                    "description": "Tags as an array of strings"
                                                },
                                                {
                                                    "type": "string",
                                                    "description": "Tags as a comma-separated string"
                                                }
                                            ],
                                            "description": "Tags to categorize the memory. Can be provided as an array of strings [\"tag1\", \"tag2\"] or a comma-separated string \"tag1,tag2\"."
                                        },
                                        "type": {
                                            "type": "string",
                                            "description": "Optional type or category label for the memory, e.g., 'note', 'fact', 'reminder'."
                                        }
                                    }
                                }
                            },
                            "required": ["content"]
                        }
                    ),
                    types.Tool(
                        name="recall_memory",
                        description="""Retrieve memories using natural language time expressions and optional semantic search.
                        
                        Supports various time-related expressions such as:
                        - "yesterday", "last week", "2 days ago"
                        - "last summer", "this month", "last January"
                        - "spring", "winter", "Christmas", "Thanksgiving"
                        - "morning", "evening", "yesterday afternoon"
                        
                        Examples:
                        {
                            "query": "recall what I stored last week"
                        }
                        
                        {
                            "query": "find information about databases from two months ago",
                            "n_results": 5
                        }
                        """,
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Natural language query specifying the time frame or content to recall, e.g., 'last week', 'yesterday afternoon', or a topic."
                                },
                                "n_results": {
                                    "type": "number",
                                    "default": 5,
                                    "description": "Maximum number of results to return."
                                }
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="retrieve_memory",
                        description="""Find relevant memories based on query.

                        Example:
                        {
                            "query": "find this memory",
                            "n_results": 5
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query to find relevant memories based on content."
                                },
                                "n_results": {
                                    "type": "number",
                                    "default": 5,
                                    "description": "Maximum number of results to return."
                                }
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="search_by_tag",
                        description="""Search memories by tags with precise AND/OR logic filtering.
                        
                        This tool provides server-side filtering to prevent cross-contamination
                        and enables scalable memory queries for large knowledge bases.

                        Examples:
                        {
                            "tags": ["important", "work"],
                            "match_all": true
                        }
                        
                        {
                            "tags": ["reference", "documentation"],
                            "match_all": false
                        }""",
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
                                    "description": "If true, memory must have ALL tags (AND logic); If false, memory needs ANY tag (OR logic)",
                                    "default": False
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="delete_memory",
                        description="""Delete a specific memory by its hash.

                        Example:
                        {
                            "content_hash": "a1b2c3d4..."
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content_hash": {
                                    "type": "string",
                                    "description": "Hash of the memory content to delete. Obtainable from memory metadata."
                                }
                            },
                            "required": ["content_hash"]
                        }
                    ),
                    types.Tool(
                        name="delete_by_tag",
                        description="""Delete all memories with specific tags.
                        WARNING: Deletes ALL memories containing any of the specified tags.

                        Example:
                        {"tags": ["temporary", "outdated"]}""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "tags": {
                                    "type": "array", 
                                    "items": {"type": "string"},
                                    "description": "Array of tag labels. Memories containing any of these tags will be deleted."
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="delete_by_tags",
                        description="""Delete all memories containing any of the specified tags.
                        This is the explicit multi-tag version for API clarity.
                        WARNING: Deletes ALL memories containing any of the specified tags.

                        Example:
                        {
                            "tags": ["temporary", "outdated", "test"]
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of tag labels. Memories containing any of these tags will be deleted."
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="delete_by_all_tags",
                        description="""Delete memories that contain ALL of the specified tags.
                        WARNING: Only deletes memories that have every one of the specified tags.

                        Example:
                        {
                            "tags": ["important", "urgent"]
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of tag labels. Only memories containing ALL of these tags will be deleted."
                                }
                            },
                            "required": ["tags"]
                        }
                    ),
                    types.Tool(
                        name="cleanup_duplicates",
                        description="Find and remove duplicate entries",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    ),
                    types.Tool(
                        name="get_embedding",
                        description="""Get raw embedding vector for content.

                        Example:
                        {
                            "content": "text to embed"
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Text content to generate an embedding vector for."
                                }
                            },
                            "required": ["content"]
                        }
                    ),
                    types.Tool(
                        name="check_embedding_model",
                        description="Check if embedding model is loaded and working",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    ),
                    types.Tool(
                        name="debug_retrieve",
                        description="""Retrieve memories with debug information.

                        Example:
                        {
                            "query": "debug this",
                            "n_results": 5,
                            "similarity_threshold": 0.0
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query for debugging retrieval, e.g., a phrase or keyword."
                                },
                                "n_results": {
                                    "type": "number",
                                    "default": 5,
                                    "description": "Maximum number of results to return."
                                },
                                "similarity_threshold": {
                                    "type": "number",
                                    "default": 0.0,
                                    "description": "Minimum similarity score threshold for results (0.0 to 1.0)."
                                }
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="exact_match_retrieve",
                        description="""Retrieve memories using exact content match.

                        Example:
                        {
                            "content": "find exactly this"
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Exact content string to match against stored memories."
                                }
                            },
                            "required": ["content"]
                        }
                    ),
                    types.Tool(
                        name="get_by_hash",
                        description="""Retrieve a specific memory by its content hash.

                        Example:
                        {
                            "content_hash": "abc123def456..."
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content_hash": {
                                    "type": "string",
                                    "description": "Content hash of the memory to retrieve."
                                }
                            },
                            "required": ["content_hash"]
                        }
                    ),
                    types.Tool(
                        name="search_by_content",
                        description="""Search memories containing specific text (substring search).

                        Example:
                        {
                            "search_text": "docker",
                            "limit": 10
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "search_text": {
                                    "type": "string",
                                    "description": "Text to search for within memory content."
                                },
                                "limit": {
                                    "type": "integer",
                                    "description": "Maximum number of results to return (default: 10).",
                                    "default": 10
                                }
                            },
                            "required": ["search_text"]
                        }
                    ),
                    types.Tool(
                        name="update_content",
                        description="""Update memory content while preserving metadata and tags.

                        Example:
                        {
                            "content_hash": "abc123def456...",
                            "new_content": "Updated content here"
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content_hash": {
                                    "type": "string",
                                    "description": "Content hash of the memory to update."
                                },
                                "new_content": {
                                    "type": "string",
                                    "description": "New content to replace the existing content."
                                }
                            },
                            "required": ["content_hash", "new_content"]
                        }
                    ),
                    types.Tool(
                        name="check_database_health",
                        description="Check database health and get statistics",
                        inputSchema={
                            "type": "object",
                            "properties": {}
                        }
                    ),
                    types.Tool(
                        name="recall_by_timeframe",
                        description="""Retrieve memories within a specific timeframe.

                        Example:
                        {
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-31",
                            "n_results": 5
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "start_date": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "Start date (inclusive) in YYYY-MM-DD format."
                                },
                                "end_date": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "End date (inclusive) in YYYY-MM-DD format."
                                },
                                "n_results": {
                                    "type": "number",
                                    "default": 5,
                                    "description": "Maximum number of results to return."
                                }
                            },
                            "required": ["start_date"]
                        }
                    ),
                    types.Tool(
                        name="delete_by_timeframe",
                        description="""Delete memories within a specific timeframe.
                        Optional tag parameter to filter deletions.

                        Example:
                        {
                            "start_date": "2024-01-01",
                            "end_date": "2024-01-31",
                            "tag": "temporary"
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "start_date": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "Start date (inclusive) in YYYY-MM-DD format."
                                },
                                "end_date": {
                                    "type": "string",
                                    "format": "date",
                                    "description": "End date (inclusive) in YYYY-MM-DD format."
                                },
                                "tag": {
                                    "type": "string",
                                    "description": "Optional tag to filter deletions. Only memories with this tag will be deleted."
                                }
                            },
                            "required": ["start_date"]
                        }
                    ),
                    types.Tool(
                        name="delete_before_date",
                        description="""Delete memories before a specific date.
                        Optional tag parameter to filter deletions.

                        Example:
                        {
                            "before_date": "2024-01-01",
                            "tag": "temporary"
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "before_date": {"type": "string", "format": "date"},
                                "tag": {"type": "string"}
                            },
                            "required": ["before_date"]
                        }
                    ),
                    types.Tool(
                        name="dashboard_create_backup",
                        description="Dashboard: Create database backup and return JSON format.",
                        inputSchema={"type": "object", "properties": {}}
                    ),
                    types.Tool(
                        name="update_memory_metadata",
                        description="""Update memory metadata without recreating the entire memory entry.
                        
                        This provides efficient metadata updates while preserving the original
                        memory content, embeddings, and optionally timestamps.
                        
                        Examples:
                        # Add tags to a memory
                        {
                            "content_hash": "abc123...",
                            "updates": {
                                "tags": ["important", "reference", "new-tag"]
                            }
                        }
                        
                        # Update memory type and custom metadata
                        {
                            "content_hash": "abc123...",
                            "updates": {
                                "memory_type": "reminder",
                                "metadata": {
                                    "priority": "high",
                                    "due_date": "2024-01-15"
                                }
                            }
                        }
                        
                        # Update custom fields directly
                        {
                            "content_hash": "abc123...",
                            "updates": {
                                "priority": "urgent",
                                "status": "active"
                            }
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "content_hash": {
                                    "type": "string",
                                    "description": "The content hash of the memory to update."
                                },
                                "updates": {
                                    "type": "object",
                                    "description": "Dictionary of metadata fields to update.",
                                    "properties": {
                                        "tags": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Replace existing tags with this list."
                                        },
                                        "memory_type": {
                                            "type": "string",
                                            "description": "Update the memory type (e.g., 'note', 'reminder', 'fact')."
                                        },
                                        "metadata": {
                                            "type": "object",
                                            "description": "Custom metadata fields to merge with existing metadata."
                                        }
                                    }
                                },
                                "preserve_timestamps": {
                                    "type": "boolean",
                                    "default": True,
                                    "description": "Whether to preserve the original created_at timestamp (default: true)."
                                }
                            },
                            "required": ["content_hash", "updates"]
                        }
                    )
                ]
                
                # Add document ingestion tools
                ingestion_tools = [
                    types.Tool(
                        name="ingest_document",
                        description="""Ingest a single document file into the memory database.
                        
                        Supports multiple formats:
                        - PDF files (.pdf)
                        - Text files (.txt, .md, .markdown, .rst)
                        - JSON files (.json)
                        
                        The document will be parsed, chunked intelligently, and stored
                        as multiple memories with appropriate metadata.
                        
                        Example:
                        {
                            "file_path": "/path/to/document.pdf",
                            "tags": ["documentation", "manual"],
                            "chunk_size": 1000
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Path to the document file to ingest."
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional tags to apply to all memories created from this document.",
                                    "default": []
                                },
                                "chunk_size": {
                                    "type": "number",
                                    "description": "Target size for text chunks in characters (default: 1000).",
                                    "default": 1000
                                },
                                "chunk_overlap": {
                                    "type": "number",
                                    "description": "Characters to overlap between chunks (default: 200).",
                                    "default": 200
                                },
                                "memory_type": {
                                    "type": "string",
                                    "description": "Type label for created memories (default: 'document').",
                                    "default": "document"
                                }
                            },
                            "required": ["file_path"]
                        }
                    ),
                    types.Tool(
                        name="ingest_directory",
                        description="""Batch ingest all supported documents from a directory.
                        
                        Recursively processes all supported file types in the directory,
                        creating memories with consistent tagging and metadata.
                        
                        Supported formats: PDF, TXT, MD, JSON
                        
                        Example:
                        {
                            "directory_path": "/path/to/documents",
                            "tags": ["knowledge-base"],
                            "recursive": true,
                            "file_extensions": ["pdf", "md", "txt"]
                        }""",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "directory_path": {
                                    "type": "string",
                                    "description": "Path to the directory containing documents to ingest."
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Optional tags to apply to all memories created.",
                                    "default": []
                                },
                                "recursive": {
                                    "type": "boolean",
                                    "description": "Whether to process subdirectories recursively (default: true).",
                                    "default": True
                                },
                                "file_extensions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "File extensions to process (default: all supported).",
                                    "default": ["pdf", "txt", "md", "json"]
                                },
                                "chunk_size": {
                                    "type": "number",
                                    "description": "Target size for text chunks in characters (default: 1000).",
                                    "default": 1000
                                },
                                "max_files": {
                                    "type": "number",
                                    "description": "Maximum number of files to process (default: 100).",
                                    "default": 100
                                }
                            },
                            "required": ["directory_path"]
                        }
                    )
                ]
                tools.extend(ingestion_tools)
                logger.info(f"Added {len(ingestion_tools)} ingestion tools")
                
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
                elif name == "retrieve_memory":
                    return await self.handle_retrieve_memory(arguments)
                elif name == "recall_memory":
                    return await self.handle_recall_memory(arguments)
                elif name == "search_by_tag":
                    return await self.handle_search_by_tag(arguments)
                elif name == "delete_memory":
                    return await self.handle_delete_memory(arguments)
                elif name == "delete_by_tag":
                    return await self.handle_delete_by_tag(arguments)
                elif name == "delete_by_tags":
                    return await self.handle_delete_by_tags(arguments)
                elif name == "delete_by_all_tags":
                    return await self.handle_delete_by_all_tags(arguments)
                elif name == "cleanup_duplicates":
                    return await self.handle_cleanup_duplicates(arguments)
                elif name == "get_embedding":
                    return await self.handle_get_embedding(arguments)
                elif name == "check_embedding_model":
                    return await self.handle_check_embedding_model(arguments)
                elif name == "debug_retrieve":
                    return await self.handle_debug_retrieve(arguments)
                elif name == "exact_match_retrieve":
                    return await self.handle_exact_match_retrieve(arguments)
                elif name == "get_by_hash":
                    return await self.handle_get_by_hash(arguments)
                elif name == "search_by_content":
                    return await self.handle_search_by_content(arguments)
                elif name == "update_content":
                    return await self.handle_update_content(arguments)
                elif name == "check_database_health":
                    logger.info("Calling handle_check_database_health")
                    return await self.handle_check_database_health(arguments)
                elif name == "recall_by_timeframe":
                    return await self.handle_recall_by_timeframe(arguments)
                elif name == "delete_by_timeframe":
                    return await self.handle_delete_by_timeframe(arguments)
                elif name == "delete_before_date":
                    return await self.handle_delete_before_date(arguments)
                elif name == "dashboard_create_backup":
                    logger.info("Calling handle_dashboard_create_backup")
                    return await self.handle_dashboard_create_backup(arguments)
                elif name == "update_memory_metadata":
                    logger.info("Calling handle_update_memory_metadata")
                    return await self.handle_update_memory_metadata(arguments)
                elif name == "ingest_document":
                    logger.info("Calling handle_ingest_document")
                    return await self.handle_ingest_document(arguments)
                elif name == "ingest_directory":
                    logger.info("Calling handle_ingest_directory")
                    return await self.handle_ingest_directory(arguments)
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
        metadata = arguments.get("metadata", {})
        
        if not content:
            return [types.TextContent(type="text", text="Error: Content is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            # Normalize tags to a list
            tags = metadata.get("tags", "")
            if isinstance(tags, str):
                tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
            elif isinstance(tags, list):
                tags = [str(tag).strip() for tag in tags if str(tag).strip()]
            else:
                tags = []  # If tags is neither string nor list, default to empty list

            sanitized_tags = storage.sanitized(tags)
            
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
                tags=tags,  # keep as a list for easier use in other methods
                memory_type=final_metadata.get("type"),
                metadata = {**final_metadata, "tags":sanitized_tags},  # include the stringified tags in the meta data
                created_at=now,
                created_at_iso=datetime.utcfromtimestamp(now).isoformat() + "Z"
            )
            
            # Store memory
            success, message = await storage.store(memory)
            return [types.TextContent(type="text", text=message)]
        except Exception as e:
            logger.error(f"Error storing memory: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error storing memory: {str(e)}")]
    
    async def handle_retrieve_memory(self, arguments: dict) -> List[types.TextContent]:
        query = arguments.get("query")
        n_results = arguments.get("n_results", 5)
        
        if not query:
            return [types.TextContent(type="text", text="Error: Query is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            # Track performance
            start_time = time.time()
            results = await storage.retrieve(query, n_results)
            query_time_ms = (time.time() - start_time) * 1000
            
            # Record query time for performance monitoring
            self.record_query_time(query_time_ms)
            
            if not results:
                return [types.TextContent(type="text", text="No matching memories found")]
            
            formatted_results = []
            for i, result in enumerate(results):
                memory_info = [
                    f"Memory {i+1}:",
                    f"Content: {result.memory.content}",
                    f"Hash: {result.memory.content_hash}",
                    f"Relevance Score: {result.relevance_score:.2f}"
                ]
                if result.memory.tags:
                    memory_info.append(f"Tags: {', '.join(result.memory.tags)}")
                memory_info.append("---")
                formatted_results.append("\n".join(memory_info))
            
            return [types.TextContent(
                type="text",
                text="Found the following memories:\n\n" + "\n".join(formatted_results)
            )]
        except Exception as e:
            logger.error(f"Error retrieving memories: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error retrieving memories: {str(e)}")]

    async def handle_search_by_tag(self, arguments: dict) -> List[types.TextContent]:
        tags = arguments.get("tags", [])
        match_all = arguments.get("match_all", False)
        
        if not tags:
            return [types.TextContent(type="text", text="Error: Tags are required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            # Use search_by_tags with operation parameter for AND/OR logic
            operation = "AND" if match_all else "OR"
            memories = await storage.search_by_tags(tags, operation=operation)
            
            if not memories:
                return [types.TextContent(
                    type="text",
                    text=f"No memories found with tags: {', '.join(tags)}"
                )]
            
            formatted_results = []
            for i, memory in enumerate(memories):
                memory_info = [
                    f"Memory {i+1}:",
                    f"Content: {memory.content}",
                    f"Hash: {memory.content_hash}",
                    f"Tags: {', '.join(memory.tags)}"
                ]
                if memory.memory_type:
                    memory_info.append(f"Type: {memory.memory_type}")
                memory_info.append("---")
                formatted_results.append("\n".join(memory_info))
            
            return [types.TextContent(
                type="text",
                text="Found the following memories:\n\n" + "\n".join(formatted_results)
            )]
        except Exception as e:
            logger.error(f"Error searching by tags: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error searching by tags: {str(e)}")]

    async def handle_delete_memory(self, arguments: dict) -> List[types.TextContent]:
        content_hash = arguments.get("content_hash")
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            success, message = await storage.delete(content_hash)
            return [types.TextContent(type="text", text=message)]
        except Exception as e:
            logger.error(f"Error deleting memory: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error deleting memory: {str(e)}")]

    async def handle_delete_by_tag(self, arguments: dict) -> List[types.TextContent]:
        """Handler for deleting memories by tags."""
        tags = arguments.get("tags", [])
        
        if not tags:
            return [types.TextContent(type="text", text="Error: Tags array is required")]
        
        # Convert single string to array if needed for backward compatibility
        if isinstance(tags, str):
            tags = [tags]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            count, message = await storage.delete_by_tag(tags)
            return [types.TextContent(type="text", text=message)]
        except Exception as e:
            logger.error(f"Error deleting by tag: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error deleting by tag: {str(e)}")]

    async def handle_delete_by_tags(self, arguments: dict) -> List[types.TextContent]:
        """Handler for explicit multiple tag deletion with progress tracking."""
        tags = arguments.get("tags", [])
        
        if not tags:
            return [types.TextContent(type="text", text="Error: Tags array is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            # Generate operation ID for progress tracking
            import uuid
            operation_id = f"delete_by_tags_{uuid.uuid4().hex[:8]}"
            
            # Send initial progress notification
            await self.send_progress_notification(operation_id, 0, f"Starting deletion of memories with tags: {', '.join(tags)}")
            
            # Execute deletion with progress updates
            await self.send_progress_notification(operation_id, 25, "Searching for memories to delete...")
            
            # If storage supports progress callbacks, use them
            if hasattr(storage, 'delete_by_tags_with_progress'):
                count, message = await storage.delete_by_tags_with_progress(
                    tags, 
                    progress_callback=lambda p, msg: asyncio.create_task(
                        self.send_progress_notification(operation_id, 25 + (p * 0.7), msg)
                    )
                )
            else:
                await self.send_progress_notification(operation_id, 50, "Deleting memories...")
                count, message = await storage.delete_by_tags(tags)
                await self.send_progress_notification(operation_id, 90, f"Deleted {count} memories")
            
            # Complete the operation
            await self.send_progress_notification(operation_id, 100, f"Deletion completed: {message}")
            
            return [types.TextContent(type="text", text=f"{message} (Operation ID: {operation_id})")]
        except Exception as e:
            logger.error(f"Error deleting by tags: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error deleting by tags: {str(e)}")]

    async def handle_delete_by_all_tags(self, arguments: dict) -> List[types.TextContent]:
        """Handler for deleting memories that contain ALL specified tags."""
        tags = arguments.get("tags", [])
        
        if not tags:
            return [types.TextContent(type="text", text="Error: Tags array is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            count, message = await storage.delete_by_all_tags(tags)
            return [types.TextContent(type="text", text=message)]
        except Exception as e:
            logger.error(f"Error deleting by all tags: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error deleting by all tags: {str(e)}")]

    async def handle_cleanup_duplicates(self, arguments: dict) -> List[types.TextContent]:
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            count, message = await storage.cleanup_duplicates()
            return [types.TextContent(type="text", text=message)]
        except Exception as e:
            logger.error(f"Error cleaning up duplicates: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error cleaning up duplicates: {str(e)}")]

    async def handle_update_memory_metadata(self, arguments: dict) -> List[types.TextContent]:
        """Handle memory metadata update requests."""
        try:
            content_hash = arguments.get("content_hash")
            updates = arguments.get("updates")
            preserve_timestamps = arguments.get("preserve_timestamps", True)
            
            if not content_hash:
                return [types.TextContent(type="text", text="Error: content_hash is required")]
            
            if not updates:
                return [types.TextContent(type="text", text="Error: updates dictionary is required")]
            
            if not isinstance(updates, dict):
                return [types.TextContent(type="text", text="Error: updates must be a dictionary")]
            
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            # Call the storage method
            success, message = await storage.update_memory_metadata(
                content_hash=content_hash,
                updates=updates,
                preserve_timestamps=preserve_timestamps
            )
            
            if success:
                logger.info(f"Successfully updated metadata for memory {content_hash}")
                return [types.TextContent(
                    type="text", 
                    text=f"Successfully updated memory metadata. {message}"
                )]
            else:
                logger.warning(f"Failed to update metadata for memory {content_hash}: {message}")
                return [types.TextContent(type="text", text=f"Failed to update memory metadata: {message}")]
                
        except Exception as e:
            error_msg = f"Error updating memory metadata: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=error_msg)]

    async def handle_get_by_hash(self, arguments: dict) -> List[types.TextContent]:
        content_hash = arguments.get("content_hash")
        if not content_hash:
            return [types.TextContent(type="text", text="Error: Content hash is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            memory = await storage.get_by_hash(content_hash)
            
            if not memory:
                return [types.TextContent(type="text", text=f"No memory found with hash: {content_hash}")]
            
            # Format the memory information
            memory_info = [
                f"Content: {memory.content}",
                f"Hash: {memory.content_hash}",
                f"Type: {memory.memory_type}",
                f"Created: {memory.created_at_iso or 'N/A'}",
                f"Updated: {memory.updated_at_iso or 'N/A'}"
            ]
            
            if memory.tags:
                memory_info.append(f"Tags: {', '.join(memory.tags)}")
            
            if memory.metadata:
                memory_info.append(f"Metadata: {json.dumps(memory.metadata, indent=2)}")
            
            return [types.TextContent(
                type="text",
                text="Memory found:\n\n" + "\n".join(memory_info)
            )]
            
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error retrieving memory by hash: {str(e)}")]

    async def handle_search_by_content(self, arguments: dict) -> List[types.TextContent]:
        search_text = arguments.get("search_text")
        limit = arguments.get("limit", 10)
        
        if not search_text:
            return [types.TextContent(type="text", text="Error: Search text is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            memories = await storage.search_by_content(search_text, limit)
            
            if not memories:
                return [types.TextContent(type="text", text=f"No memories found containing: '{search_text}'")]
            
            formatted_results = []
            for i, memory in enumerate(memories):
                memory_info = [
                    f"Memory {i+1}:",
                    f"Content: {memory.content}",
                    f"Hash: {memory.content_hash}"
                ]
                
                if memory.tags:
                    memory_info.append(f"Tags: {', '.join(memory.tags)}")
                
                memory_info.append("---")
                formatted_results.append("\n".join(memory_info))
            
            return [types.TextContent(
                type="text",
                text=f"Found {len(memories)} memories containing '{search_text}':\n\n" + "\n".join(formatted_results)
            )]
            
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error in content search: {str(e)}")]

    async def handle_update_content(self, arguments: dict) -> List[types.TextContent]:
        content_hash = arguments.get("content_hash")
        new_content = arguments.get("new_content")
        
        if not content_hash:
            return [types.TextContent(type="text", text="Error: Content hash is required")]
        
        if not new_content:
            return [types.TextContent(type="text", text="Error: New content is required")]
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            success, message = await storage.update_content(content_hash, new_content)
            
            if success:
                return [types.TextContent(type="text", text=f"✅ {message}")]
            else:
                return [types.TextContent(type="text", text=f"❌ {message}")]
                
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error updating content: {str(e)}")]

    async def handle_recall_memory(self, arguments: dict) -> List[types.TextContent]:
        """
        Handle memory recall requests with natural language time expressions.
        
        This handler parses natural language time expressions from the query,
        extracts time ranges, and combines them with optional semantic search.
        """
        query = arguments.get("query", "")
        n_results = arguments.get("n_results", 5)
        
        if not query:
            return [types.TextContent(type="text", text="Error: Query is required")]
        
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
            
            # Format human-readable time range for response
            time_range_str = ""
            if start_timestamp is not None and end_timestamp is not None:
                start_dt = datetime.fromtimestamp(start_timestamp)
                end_dt = datetime.fromtimestamp(end_timestamp)
                time_range_str = f" from {start_dt.strftime('%Y-%m-%d %H:%M')} to {end_dt.strftime('%Y-%m-%d %H:%M')}"
            
            # Retrieve memories with timestamp filter and optional semantic search
            # If cleaned_query is empty or just whitespace after removing time expressions,
            # we should perform time-based retrieval only
            semantic_query = cleaned_query.strip() if cleaned_query.strip() else None
            
            # Use the enhanced recall method from ChromaMemoryStorage that combines
            # semantic search with time filtering, or just time filtering if no semantic query
            results = await storage.recall(
                query=semantic_query,
                n_results=n_results,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )
            
            if not results:
                no_results_msg = f"No memories found{time_range_str}"
                return [types.TextContent(type="text", text=no_results_msg)]
            
            # Format results
            formatted_results = []
            for i, result in enumerate(results):
                memory_dt = result.memory.timestamp
                
                memory_info = [
                    f"Memory {i+1}:",
                ]
                
                # Add timestamp if available
                if memory_dt:
                    memory_info.append(f"Timestamp: {memory_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Add other memory information
                memory_info.extend([
                    f"Content: {result.memory.content}",
                    f"Hash: {result.memory.content_hash}"
                ])
                
                # Add relevance score if available (may not be for time-only queries)
                if hasattr(result, 'relevance_score') and result.relevance_score is not None:
                    memory_info.append(f"Relevance Score: {result.relevance_score:.2f}")
                
                # Add tags if available
                if result.memory.tags:
                    memory_info.append(f"Tags: {', '.join(result.memory.tags)}")
                
                memory_info.append("---")
                formatted_results.append("\n".join(memory_info))
            
            # Include time range in response if available
            found_msg = f"Found {len(results)} memories{time_range_str}:"
            return [types.TextContent(
                type="text",
                text=f"{found_msg}\n\n" + "\n".join(formatted_results)
            )]
            
        except Exception as e:
            logger.error(f"Error in recall_memory: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(type="text", text=f"Error recalling memories: {str(e)}")]

    async def handle_check_database_health(self, arguments: dict) -> List[types.TextContent]:
        """Handle database health check requests with performance metrics."""
        logger.info("=== EXECUTING CHECK_DATABASE_HEALTH ===")
        try:
            # Initialize storage lazily when needed
            try:
                storage = await self._ensure_storage_initialized()
            except Exception as init_error:
                # Storage initialization failed
                result = {
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
                return [types.TextContent(
                    type="text",
                    text=f"Database Health Check Results:\n{json.dumps(result, indent=2)}"
                )]
            
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
            result = {
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
            
            logger.info(f"Database health result with performance data: {result}")
            return [types.TextContent(
                type="text",
                text=f"Database Health Check Results:\n{json.dumps(result, indent=2)}"
            )]
        except Exception as e:
            logger.error(f"Error in check_database_health: {str(e)}")
            logger.error(traceback.format_exc())
            return [types.TextContent(
                type="text",
                text=f"Error checking database health: {str(e)}"
            )]

    async def handle_recall_by_timeframe(self, arguments: dict) -> List[types.TextContent]:
        """Handle recall by timeframe requests."""
        from datetime import datetime
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            start_date = datetime.fromisoformat(arguments["start_date"]).date()
            end_date = datetime.fromisoformat(arguments.get("end_date", arguments["start_date"])).date()
            n_results = arguments.get("n_results", 5)
            
            # Get timestamp range
            start_timestamp = datetime(start_date.year, start_date.month, start_date.day).timestamp()
            end_timestamp = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59).timestamp()
            
            # Log the timestamp values for debugging
            logger.info(f"Recall by timeframe: {start_date} to {end_date}")
            logger.info(f"Start timestamp: {start_timestamp} ({datetime.fromtimestamp(start_timestamp).strftime('%Y-%m-%d %H:%M:%S')})")
            logger.info(f"End timestamp: {end_timestamp} ({datetime.fromtimestamp(end_timestamp).strftime('%Y-%m-%d %H:%M:%S')})")
            
            # Retrieve memories with proper parameters - query is None because this is pure time-based filtering
            results = await storage.recall(
                query=None,
                n_results=n_results,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )
            
            if not results:
                return [types.TextContent(type="text", text=f"No memories found from {start_date} to {end_date}")]
            
            formatted_results = []
            for i, result in enumerate(results):
                memory_timestamp = result.memory.timestamp
                memory_info = [
                    f"Memory {i+1}:",
                ]
                
                # Add timestamp if available
                if memory_timestamp:
                    memory_info.append(f"Timestamp: {memory_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                
                memory_info.extend([
                    f"Content: {result.memory.content}",
                    f"Hash: {result.memory.content_hash}"
                ])
                
                if result.memory.tags:
                    memory_info.append(f"Tags: {', '.join(result.memory.tags)}")
                memory_info.append("---")
                formatted_results.append("\n".join(memory_info))
            
            return [types.TextContent(
                type="text",
                text=f"Found {len(results)} memories from {start_date} to {end_date}:\n\n" + "\n".join(formatted_results)
            )]
            
        except Exception as e:
            logger.error(f"Error in recall_by_timeframe: {str(e)}\n{traceback.format_exc()}")
            return [types.TextContent(
                type="text",
                text=f"Error recalling memories: {str(e)}"
            )]

    async def handle_delete_by_timeframe(self, arguments: dict) -> List[types.TextContent]:
        """Handle delete by timeframe requests."""
        from datetime import datetime
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            start_date = datetime.fromisoformat(arguments["start_date"]).date()
            end_date = datetime.fromisoformat(arguments.get("end_date", arguments["start_date"])).date()
            tag = arguments.get("tag")
            
            count, message = await storage.delete_by_timeframe(start_date, end_date, tag)
            return [types.TextContent(
                type="text",
                text=f"Deleted {count} memories: {message}"
            )]
            
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error deleting memories: {str(e)}"
            )]

    async def handle_delete_before_date(self, arguments: dict) -> List[types.TextContent]:
        """Handle delete before date requests."""
        from datetime import datetime
        
        try:
            # Initialize storage lazily when needed
            storage = await self._ensure_storage_initialized()
            
            before_date = datetime.fromisoformat(arguments["before_date"]).date()
            tag = arguments.get("tag")
            
            count, message = await storage.delete_before_date(before_date, tag)
            return [types.TextContent(
                type="text",
                text=f"Deleted {count} memories: {message}"
            )]
            
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"Error deleting memories: {str(e)}"
            )]

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
            memory_type = arguments.get("memory_type", "document")
            
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
                        memory_type=memory_type,
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
                                memory_type="document",
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
