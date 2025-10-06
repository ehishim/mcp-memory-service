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

from ..models.memory import Memory

import chromadb
import json
import sys
import os
import time
import traceback
import warnings
from chromadb.utils import embedding_functions
import logging
from typing import List, Dict, Any, Tuple, Set, Optional
from datetime import datetime, date

# Try to import SentenceTransformer, but don't fail if it's not available
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    print("WARNING: sentence_transformers not available. Using default embeddings.")


from .base import MemoryStorage
from ..models.memory import Memory, MemoryQueryResult
from ..utils.hashing import generate_content_hash
from ..utils.system_detection import (
    get_system_info,
    get_optimal_embedding_settings,
    get_torch_device,
    print_system_diagnostics,
    AcceleratorType
)
import mcp.types as types

logger = logging.getLogger(__name__)

# Global model cache for performance optimization
import threading
import hashlib
from functools import lru_cache

_MODEL_CACHE = {}
_EMBEDDING_CACHE = {}
_QUERY_CACHE = {}
_CACHE_LOCK = threading.Lock()
_PERFORMANCE_STATS = {"query_times": [], "cache_hits": 0, "cache_misses": 0}

# List of models to try in order of preference
# From most capable to least capable
MODEL_FALLBACKS = [
    'all-mpnet-base-v2',      # High quality, larger model
    'all-MiniLM-L6-v2',       # Good balance of quality and size
    'paraphrase-MiniLM-L6-v2', # Alternative with similar size
    'paraphrase-MiniLM-L3-v2', # Smaller model for constrained environments
    'paraphrase-albert-small-v2' # Smallest model, last resort
]

class ChromaMemoryStorage(MemoryStorage):
    def __init__(self, path: str, preload_model: bool = True):
        """Initialize ChromaDB storage with hardware-aware embedding function and performance optimizations."""
        # Issue deprecation warning
        warnings.warn(
            "ChromaDB backend is deprecated and will be removed in v6.0.0. "
            "Please migrate to SQLite-vec backend for better performance and reliability. "
            "See migration guide at: https://github.com/doobidoo/mcp-memory-service#migration",
            DeprecationWarning,
            stacklevel=2
        )
        logger.warning(
            "DEPRECATION: ChromaDB backend is deprecated. Consider migrating to SQLite-vec backend. "
            "Run 'python scripts/migrate_to_sqlite_vec.py' to migrate your data."
        )
        
        self.path = path
        self.model = None
        self.embedding_function = None
        self.client = None
        self.collection = None
        self.system_info = get_system_info()
        self.embedding_settings = get_optimal_embedding_settings()
        
        # Performance settings
        self.enable_query_cache = True
        self.cache_ttl = 300  # 5 minutes
        self.batch_size = self.embedding_settings.get('batch_size', 32)
        
        # Log system information (reduced logging for performance)
        if logger.isEnabledFor(logging.INFO):
            logger.info(f"System: {self.system_info.os_name} {self.system_info.architecture}, "
                       f"Accelerator: {self.system_info.accelerator}, "
                       f"Memory: {self.system_info.memory_gb:.2f}GB, "
                       f"Device: {self.embedding_settings['device']}")
        
        # Configure environment for performance
        self._configure_performance_environment()
        
        try:
            if preload_model:
                # Use cached model if available, otherwise load and cache
                self._initialize_with_cache()
            else:
                # Initialize with hardware-aware settings (legacy mode)
                self._initialize_embedding_model()
            
            # Initialize ChromaDB with performance optimizations
            self._initialize_chromadb_optimized()
            
            # Verify initialization was successful
            if self.collection is None:
                raise RuntimeError("Collection initialization failed - collection is None")
            if self.embedding_function is None:
                raise RuntimeError("Embedding function initialization failed - embedding function is None")
            
            logger.info("ChromaMemoryStorage initialization completed successfully")
            
        except Exception as e:
            logger.error(f"Error initializing ChromaDB: {str(e)}")
            logger.error(traceback.format_exc())
            print(f"ChromaDB initialization error: {str(e)}", file=sys.stderr)
            
            # Set objects to None to indicate failed state
            self.collection = None
            self.embedding_function = None
            self.client = None
            self.model = None
            
            # Re-raise the exception so callers know initialization failed
            raise RuntimeError(f"ChromaMemoryStorage initialization failed: {str(e)}") from e
    
    def _configure_performance_environment(self):
        """Set optimal environment variables for performance."""
        os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
        
        # For Apple Silicon, ensure we use MPS when available
        if self.system_info.architecture == "arm64" and self.system_info.os_name == "darwin":
            os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
        
        # For Windows with limited GPU memory, use smaller chunks
        if self.system_info.os_name == "windows" and self.system_info.accelerator == AcceleratorType.CUDA:
            os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:128,garbage_collection_threshold:0.6"
        
        # CPU optimizations
        os.environ["OMP_NUM_THREADS"] = str(min(8, os.cpu_count() or 1))
        os.environ["MKL_NUM_THREADS"] = str(min(8, os.cpu_count() or 1))
    
    def _get_model_cache_key(self) -> str:
        """Generate cache key for model based on system settings."""
        settings = self.embedding_settings
        return f"{settings['model_name']}_{settings['device']}_{settings.get('batch_size', 32)}"
    
    def _initialize_with_cache(self):
        """Initialize with model caching for better performance."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("SentenceTransformer not available, using default embedding function")
            self.model = None
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            return
            
        model_key = self._get_model_cache_key()
        
        with _CACHE_LOCK:
            if model_key in _MODEL_CACHE:
                logger.info("Using cached embedding model")
                cached_data = _MODEL_CACHE[model_key]
                self.model = cached_data['model']
                self.embedding_function = cached_data['function']
                return
        
        # Model not in cache, load and cache it
        logger.info("Loading and caching new embedding model")
        self._load_and_cache_model(model_key)
    
    def _load_and_cache_model(self, cache_key: str):
        """Load model and cache it for reuse."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("SentenceTransformer not available, using default embedding function")
            self.model = None
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            return
            
        preferred_model = self.embedding_settings["model_name"]
        device = self.embedding_settings["device"]
        batch_size = self.embedding_settings["batch_size"]
        
        # Configure offline mode if models are cached
        hf_home = os.environ.get('HF_HOME', os.path.expanduser("~/.cache/huggingface"))
        model_cache_path = os.path.join(hf_home, "hub", f"models--sentence-transformers--{preferred_model.replace('/', '--')}")
        if os.path.exists(model_cache_path):
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TRANSFORMERS_OFFLINE'] = '1'
            logger.info(f"Using offline mode for cached model: {preferred_model}")
        
        # Try the preferred model first, then fall back to alternatives
        models_to_try = [preferred_model] + [m for m in MODEL_FALLBACKS if m != preferred_model]
        
        for model_name in models_to_try:
            try:
                logger.info(f"Loading model: {model_name} on {device}")
                start_time = time.time()
                
                # Load model with optimizations
                model = SentenceTransformer(model_name, device=device)
                model.eval()  # Set to evaluation mode for inference
                
                # Try to use half precision for faster inference on GPU
                if device != "cpu" and hasattr(model, 'half'):
                    try:
                        model = model.half()
                    except:
                        pass  # Fallback to full precision
                
                # Test the model
                _ = model.encode("Test encoding", batch_size=1, show_progress_bar=False)
                
                load_time = time.time() - start_time
                logger.info(f"Successfully loaded model {model_name} in {load_time:.2f}s")
                
                # Create embedding function
                embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=model_name,
                    device=device
                )
                
                # Cache the loaded model
                with _CACHE_LOCK:
                    _MODEL_CACHE[cache_key] = {
                        'model': model,
                        'function': embedding_function,
                        'loaded_at': time.time(),
                        'model_name': model_name,
                        'device': device
                    }
                
                self.model = model
                self.embedding_function = embedding_function
                return
                
            except Exception as e:
                logger.warning(f"Failed to load model {model_name} on {device}: {str(e)}")
                continue
        
        # If all models failed, fall back to default embedding function
        logger.warning("All optimized model loading failed, falling back to default embedding function")
        self.model = None
        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
        self._initialize_embedding_model()
    
    def _initialize_chromadb_optimized(self):
        """Initialize ChromaDB with performance optimizations."""
        try:
            # Check if remote ChromaDB configuration is provided
            remote_host = os.getenv('MCP_MEMORY_CHROMADB_HOST')
            remote_port = os.getenv('MCP_MEMORY_CHROMADB_PORT', '8000')
            use_ssl = os.getenv('MCP_MEMORY_CHROMADB_SSL', 'false').lower() == 'true'
            api_key = os.getenv('MCP_MEMORY_CHROMADB_API_KEY')
            
            if remote_host:
                # Use HttpClient for remote ChromaDB server
                logger.info(f"Initializing ChromaDB HTTP client: {remote_host}:{remote_port} (SSL: {use_ssl})")
                
                # Prepare headers for authentication
                headers = {}
                if api_key:
                    headers['X_CHROMA_TOKEN'] = api_key
                    logger.info("Using API key authentication for remote ChromaDB")
                
                self.client = chromadb.HttpClient(
                    host=remote_host,
                    port=int(remote_port),
                    ssl=use_ssl,
                    headers=headers if headers else None
                )
            else:
                # Use PersistentClient for local ChromaDB (existing behavior)
                logger.info(f"Initializing ChromaDB persistent client at path: {self.path}")
                self.client = chromadb.PersistentClient(path=self.path)
            
            # Create collection with optimized HNSW settings
            collection_metadata = {
                "hnsw:space": "cosine",
                "hnsw:construction_ef": 200,  # Higher for better accuracy
                "hnsw:search_ef": 100,        # Balanced search performance
                "hnsw:M": 16,                 # Better graph connectivity
            }
            
            # If embedding_function is None, create a simple pass-through function for testing
            if self.embedding_function is None:
                # Simple pass-through function for testing
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
                self.embedding_function = DefaultEmbeddingFunction()
            
            # Allow custom collection name for remote ChromaDB deployments
            collection_name = os.getenv('MCP_MEMORY_COLLECTION_NAME', 'memory_collection')
            logger.info(f"Using ChromaDB collection: {collection_name}")
            
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata=collection_metadata,
                embedding_function=self.embedding_function
            )
            logger.info("Collection initialized with performance optimizations")
        except Exception as e:
            logger.error(f"Error in _initialize_chromadb_optimized: {str(e)}")
            raise
    
    @lru_cache(maxsize=1000)
    def _cached_embed_query(self, query: str) -> tuple:
        """Cache embeddings for identical queries to improve performance."""
        if self.model:
            try:
                embedding = self.model.encode(
                    query, 
                    batch_size=1,
                    show_progress_bar=False,
                    convert_to_numpy=True
                )
                return tuple(embedding.tolist())
            except Exception as e:
                logger.warning(f"Error in cached embedding: {e}")
                return None
        return None
    
    def _initialize_embedding_model(self):
        """Initialize the embedding model with fallbacks for different hardware."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("SentenceTransformer not available, using default embedding function")
            self.model = None
            self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
            return
            
        # Configure offline mode for cached models
        preferred_model = self.embedding_settings.get("model_name", "all-MiniLM-L6-v2")
        hf_home = os.environ.get('HF_HOME', os.path.expanduser("~/.cache/huggingface"))
        model_cache_path = os.path.join(hf_home, "hub", f"models--sentence-transformers--{preferred_model.replace('/', '--')}")
        if os.path.exists(model_cache_path):
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TRANSFORMERS_OFFLINE'] = '1'
            logger.info(f"Using offline mode for cached model: {preferred_model}")
            
        # Start with the optimal model for this system
        preferred_model = self.embedding_settings["model_name"]
        device = self.embedding_settings["device"]
        batch_size = self.embedding_settings["batch_size"]
        
        # Try the preferred model first, then fall back to alternatives
        models_to_try = [preferred_model] + [m for m in MODEL_FALLBACKS if m != preferred_model]
        
        for model_name in models_to_try:
            try:
                logger.info(f"Attempting to load model: {model_name} on {device}")
                start_time = time.time()
                
                # Try to initialize the model with the current settings
                self.model = SentenceTransformer(
                    model_name,
                    device=device
                )
                
                # Set batch size based on available resources
                self.model.max_seq_length = 384  # Default max sequence length
                
                # Test the model with a simple encoding
                _ = self.model.encode("Test encoding", batch_size=batch_size)
                
                load_time = time.time() - start_time
                logger.info(f"Successfully loaded model {model_name} in {load_time:.2f}s")
                
                # Create embedding function for ChromaDB
                self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=model_name,
                    device=device
                )
                
                logger.info(f"Embedding function initialized with model {model_name}")
                return
                
            except Exception as e:
                logger.warning(f"Failed to initialize model {model_name} on {device}: {str(e)}")
                
                # If we're not on CPU already, try falling back to CPU
                if device != "cpu":
                    try:
                        logger.info(f"Falling back to CPU for model: {model_name}")
                        self.model = SentenceTransformer(model_name, device="cpu")
                        _ = self.model.encode("Test encoding", batch_size=max(1, batch_size // 2))
                        
                        # Update settings to reflect CPU usage
                        self.embedding_settings["device"] = "cpu"
                        self.embedding_settings["batch_size"] = max(1, batch_size // 2)
                        
                        # Create embedding function
                        self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                            model_name=model_name,
                            device="cpu"
                        )
                        
                        logger.info(f"Successfully loaded model {model_name} on CPU")
                        return
                    except Exception as cpu_e:
                        logger.warning(f"Failed to initialize model {model_name} on CPU: {str(cpu_e)}")
        
        # If we've tried all models and none worked, raise an exception
        error_msg = "Failed to initialize any embedding model. Service may not function correctly."
        logger.error(error_msg)
        print(error_msg, file=sys.stderr)
        
        # Create a minimal dummy embedding function as last resort
        try:
            logger.warning("Creating minimal dummy embedding function as last resort")
            from sentence_transformers.util import normalize_embeddings
            import numpy as np
            
            # Define a minimal embedding function that returns random vectors
            class MinimalEmbeddingFunction:
                def __call__(self, texts):
                    vectors = [np.random.rand(384) for _ in texts]
                    return normalize_embeddings(np.array(vectors))
            
            self.embedding_function = MinimalEmbeddingFunction()
            logger.warning("Minimal dummy embedding function created. Search quality will be poor.")
        except Exception as e:
            logger.error(f"Failed to create minimal embedding function: {str(e)}")

    def sanitized(self, tags):
        if tags is None:
            return json.dumps([])
        
        # If we get a string, split it into an array
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        # If we get an array, use it directly
        elif isinstance(tags, list):
            tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        else:
            return json.dumps([])
                
        # Return JSON string representation of the array
        return json.dumps(tags)

    @staticmethod
    def normalize_timestamp(ts) -> float:
        """Convert datetime or float-like timestamp into float seconds."""
        if isinstance(ts, datetime):
            return time.mktime(ts.timetuple())
        if isinstance(ts, (float, int)):
            return float(ts)
        logger.error(f"Invalid timestamp type: {type(ts)}")
        return time.time()
    
    async def store(self, memory: Memory) -> Tuple[bool, str]:
        """Store a memory with optimized performance."""
        try:
            # Check if collection is initialized
            if self.collection is None:
                error_msg = "Collection not initialized, cannot store memory"
                logger.error(error_msg)
                return False, error_msg

            # Check for duplicates by hash (not by ID)
            existing = self.collection.get(
                where={"hash": memory.content_hash}
            )
            if existing["ids"]:
                # Return existing ID when duplicate found
                return False, f"Duplicate content detected (existing ID: {existing['ids'][0]})"

            # Format metadata using optimized method
            metadata = self._optimize_metadata_for_chroma(memory)

            # Add additional metadata
            metadata.update(memory.metadata)

            # Use the memory's UUID as the ChromaDB ID
            memory_id = memory.id

            # Add to collection - embedding will be automatically generated
            self.collection.add(
                documents=[memory.content],
                metadatas=[metadata],
                ids=[memory_id]
            )

            return True, f"Successfully stored memory with ID: {memory_id}"

        except Exception as e:
            error_msg = f"Error storing memory: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
            
    async def store_batch(self, memories: List[Memory]) -> List[Tuple[bool, str]]:
        """Batch store operation for improved performance."""
        if not memories:
            return []

        try:
            documents = []
            metadatas = []
            ids = []
            results = []
            seen_hashes = set()

            for memory in memories:
                # Check for duplicates in batch by hash
                if memory.content_hash not in seen_hashes:
                    # Check existing in database by hash
                    existing = self.collection.get(
                        where={"hash": memory.content_hash}
                    )
                    if existing["ids"]:
                        results.append((False, f"Duplicate content detected: {memory.content_hash} (existing ID: {existing['ids'][0]})"))
                        continue

                    documents.append(memory.content)
                    metadatas.append(self._optimize_metadata_for_chroma(memory))
                    ids.append(memory.id)
                    seen_hashes.add(memory.content_hash)
                    results.append((True, f"Queued for batch storage: {memory.id}"))
                else:
                    results.append((False, f"Duplicate in batch: {memory.content_hash}"))

            if documents:
                # Batch add to collection
                self.collection.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids
                )

                # Update success messages
                for i, (success, msg) in enumerate(results):
                    if success and "Queued for batch storage" in msg:
                        results[i] = (True, f"Successfully stored in batch: {ids[i % len(ids)]}")

            return results

        except Exception as e:
            error_msg = f"Error in batch store: {str(e)}"
            logger.error(error_msg)
            return [(False, error_msg)] * len(memories)


    async def search_by_tag(self, tags: List[str]) -> List[Memory]:
        """Search memories by tags with optimized performance.
        Handles both new comma-separated and old JSON array tag formats."""
        try:
            results = self.collection.get(
                include=["metadatas", "documents"]
            )

            memories = []
            if results["ids"]:
                # Normalize search tags once
                search_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
                
                for i, doc in enumerate(results["documents"]):
                    memory_meta = results["metadatas"][i]
                    
                    # Use enhanced tag parsing that handles both formats
                    stored_tags = self._parse_tags_fast(memory_meta.get("tags", ""))
                    
                    # Fast tag matching
                    if any(search_tag in stored_tags for search_tag in search_tags):
                        # Use stored timestamps or fall back to legacy timestamp field
                        created_at = memory_meta.get("created_at") or memory_meta.get("timestamp_float") or memory_meta.get("timestamp")
                        created_at_iso = memory_meta.get("created_at_iso") or memory_meta.get("timestamp_str")
                        updated_at = memory_meta.get("updated_at") or created_at
                        updated_at_iso = memory_meta.get("updated_at_iso") or created_at_iso

                        memory = Memory(
                            id=results["ids"][i],
                            content=doc,
                            content_hash=memory_meta["hash"],
                            tags=stored_tags,
                            # Restore timestamps with fallback logic
                            created_at=created_at,
                            created_at_iso=created_at_iso,
                            updated_at=updated_at,
                            updated_at_iso=updated_at_iso,
                            # Include additional metadata
                            metadata={k: v for k, v in memory_meta.items()
                                     if k not in ["id", "hash", "tags", "type", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
                        )
                        memories.append(memory)
            
            return memories
            
        except Exception as e:
            logger.error(f"Error searching by tags: {e}")
            logger.error(traceback.format_exc())
            return []
    
    async def search_by_tags(self, tags: List[str], operation: str = "AND") -> List[Memory]:
        """Search memories by tags with AND/OR operation support.
        
        Args:
            tags: List of tags to search for
            operation: "AND" for all tags required, "OR" for any tag (default: "AND")
        """
        try:
            results = self.collection.get(
                include=["metadatas", "documents"]
            )
            memories = []
            if results["ids"]:
                # Normalize search tags once
                search_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
                
                for i, doc in enumerate(results["documents"]):
                    memory_meta = results["metadatas"][i]
                    
                    # Use enhanced tag parsing that handles both formats
                    stored_tags = self._parse_tags_fast(memory_meta.get("tags", ""))
                    
                    # Apply AND/OR logic
                    if operation.upper() == "AND":
                        # All tags must be present
                        match = all(search_tag in stored_tags for search_tag in search_tags)
                    else:  # OR operation
                        # Any tag must be present
                        match = any(search_tag in stored_tags for search_tag in search_tags)
                    
                    if match:
                        # Use stored timestamps or fall back to legacy timestamp field
                        created_at = memory_meta.get("created_at") or memory_meta.get("timestamp_float") or memory_meta.get("timestamp")
                        created_at_iso = memory_meta.get("created_at_iso") or memory_meta.get("timestamp_str")
                        updated_at = memory_meta.get("updated_at") or created_at
                        updated_at_iso = memory_meta.get("updated_at_iso") or created_at_iso

                        memory = Memory(
                            id=results["ids"][i],
                            content=doc,
                            content_hash=memory_meta["hash"],
                            tags=stored_tags,
                            # Restore timestamps with fallback logic
                            created_at=created_at,
                            created_at_iso=created_at_iso,
                            updated_at=updated_at,
                            updated_at_iso=updated_at_iso,
                            # Include additional metadata
                            metadata={k: v for k, v in memory_meta.items()
                                     if k not in ["id", "hash", "tags", "type", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
                        )
                        memories.append(memory)
            
            logger.info(f"Found {len(memories)} memories with tags: {tags} (operation: {operation})")
            return memories
            
        except Exception as e:
            logger.error(f"Error searching by tags with operation {operation}: {e}")
            logger.error(traceback.format_exc())
            return []

    async def delete_by_tag(self, tags: List[str], match_all: bool = False) -> Tuple[int, str]:
        """Delete memories by tags with AND/OR logic (mirrors search_by_tag).

        Args:
            tags: List of tags to match
            match_all: If True, memory must have ALL tags (AND logic);
                      If False, memory needs ANY tag (OR logic)

        Returns:
            Tuple of (count_deleted, message)
        """
        try:
            if not tags:
                return 0, "No tags provided"

            # Normalize tags
            tags_to_match = [str(tag).strip() for tag in tags if str(tag).strip()]
            if not tags_to_match:
                return 0, "No valid tags provided"

            # Get all documents from ChromaDB
            results = self.collection.get(include=["metadatas"])

            ids_to_delete = []
            operation_desc = "all tags" if match_all else "any tag"

            if results["ids"]:
                for i, meta in enumerate(results["metadatas"]):
                    try:
                        # Handle both comma-separated and JSON formats
                        retrieved_tags = self._parse_tags_fast(meta.get("tags", ""))
                    except Exception:
                        retrieved_tags = []

                    # Apply AND/OR logic
                    should_delete = False
                    if match_all:
                        # ALL tags must be present (AND logic)
                        should_delete = all(tag in retrieved_tags for tag in tags_to_match)
                    else:
                        # ANY tag present (OR logic)
                        should_delete = any(tag in retrieved_tags for tag in tags_to_match)

                    if should_delete:
                        ids_to_delete.append(results["ids"][i])

            if not ids_to_delete:
                return 0, f"No memories found with {operation_desc}: {', '.join(tags_to_match)}"

            # Delete memories
            self.collection.delete(ids=ids_to_delete)

            message = f"Successfully deleted {len(ids_to_delete)} memories with {operation_desc}: {', '.join(tags_to_match)}"
            return len(ids_to_delete), message

        except Exception as e:
            logger.error(f"Error deleting memories by tag: {e}")
            return 0, f"Error deleting memories by tag: {e}"

    async def delete(self, id: str) -> Tuple[bool, str]:
        """Delete a memory by its ID."""
        try:
            # First check if the memory exists
            existing = self.collection.get(
                ids=[id]
            )

            if not existing["ids"]:
                return False, f"No memory found with ID {id}"

            # Delete the memory by ID
            self.collection.delete(
                ids=[id]
            )

            return True, f"Successfully deleted memory with ID {id}"
        except Exception as e:
            logger.error(f"Error deleting memory: {str(e)}")
            return False, f"Error deleting memory: {str(e)}"

    async def cleanup_duplicates(self) -> Tuple[int, str]:
        """Remove duplicate memories based on hash."""
        try:
            # Get all memories
            results = self.collection.get()

            if not results["ids"]:
                return 0, "No memories found in database"

            # Track seen hashes and duplicates
            seen_hashes: Set[str] = set()
            duplicates = []

            for i, metadata in enumerate(results["metadatas"]):
                hash_value = metadata.get("hash")
                if not hash_value:
                    # Generate hash if missing
                    hash_value = generate_content_hash(results["documents"][i], metadata)

                if hash_value in seen_hashes:
                    duplicates.append(results["ids"][i])
                else:
                    seen_hashes.add(hash_value)

            # Delete duplicates if found
            if duplicates:
                self.collection.delete(
                    ids=duplicates
                )
                return len(duplicates), f"Successfully removed {len(duplicates)} duplicate memories"

            return 0, "No duplicate memories found"

        except Exception as e:
            logger.error(f"Error cleaning up duplicates: {str(e)}")
            return 0, f"Error cleaning up duplicates: {str(e)}"
    
    async def get_by_id(self, id: str) -> Optional[Memory]:
        """Get a memory by its ID."""
        try:
            # Check if collection is initialized
            if self.collection is None:
                logger.error("Collection not initialized, cannot get memory by ID")
                return None

            # Find the memory by ID
            existing = self.collection.get(
                ids=[id],
                include=["metadatas", "documents"]
            )

            if not existing["ids"]:
                return None

            # Get the memory data
            memory_meta = existing["metadatas"][0]
            doc = existing["documents"][0]

            # Parse tags
            stored_tags = self._parse_tags_fast(memory_meta.get("tags", ""))

            # Use stored timestamps or fall back to legacy timestamp field
            created_at = memory_meta.get("created_at") or memory_meta.get("timestamp_float") or memory_meta.get("timestamp")
            created_at_iso = memory_meta.get("created_at_iso") or memory_meta.get("timestamp_str")
            updated_at = memory_meta.get("updated_at") or created_at
            updated_at_iso = memory_meta.get("updated_at_iso") or created_at_iso

            memory = Memory(
                id=existing["ids"][0],
                content=doc,
                content_hash=memory_meta["hash"],
                tags=stored_tags,
                # Restore timestamps with fallback logic
                created_at=created_at,
                created_at_iso=created_at_iso,
                updated_at=updated_at,
                updated_at_iso=updated_at_iso,
                # Include additional metadata
                metadata={k: v for k, v in memory_meta.items()
                         if k not in ["id", "hash", "tags", "type", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
            )

            return memory

        except Exception as e:
            logger.error(f"Error getting memory by ID: {str(e)}")
            logger.error(traceback.format_exc())
            return None

    async def update_memory(
        self,
        id: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags_strategy: str = "replace",
        metadata_strategy: str = "replace"
    ) -> Tuple[bool, str]:
        """Update memory content, tags, and/or metadata with configurable strategies."""
        try:
            if self.collection is None:
                return False, "Collection not initialized"

            # Validate strategies
            if tags_strategy not in ["replace", "merge"]:
                return False, f"Invalid tags_strategy: {tags_strategy}"
            if metadata_strategy not in ["replace", "merge"]:
                return False, f"Invalid metadata_strategy: {metadata_strategy}"

            # Find existing memory by ID
            existing = self.collection.get(
                ids=[id],
                include=["metadatas", "documents"]
            )

            if not existing["ids"]:
                return False, f"Memory with ID {id} not found"

            current_metadata = existing["metadatas"][0]
            current_document = existing["documents"][0]
            updated_fields = []

            # Prepare new metadata
            new_metadata = current_metadata.copy()
            new_document = current_document

            # Process content update
            if content is not None:
                new_document = content
                updated_fields.append("content")

            # Process tags
            current_tags_str = current_metadata.get("tags", "")
            current_tags = [t.strip() for t in current_tags_str.split(",") if t.strip()] if current_tags_str else []
            new_tags = current_tags

            if tags is not None:
                if tags_strategy == "replace":
                    new_tags = tags
                else:  # merge
                    new_tags = list(set(current_tags + tags))  # Deduplicate
                updated_fields.append(f"tags ({tags_strategy})")

            new_metadata["tags"] = ",".join(new_tags)

            # Process metadata (extract custom metadata only)
            custom_metadata = {k: v for k, v in current_metadata.items()
                             if k not in {"id", "hash", "tags", "created_at", "created_at_iso", "updated_at", "updated_at_iso"}}

            if metadata is not None:
                if metadata_strategy == "replace":
                    custom_metadata = metadata
                else:  # merge
                    custom_metadata.update(metadata)
                updated_fields.append(f"metadata ({metadata_strategy})")

            # Recalculate hash with all updated data
            from ..utils.hashing import generate_content_hash
            new_hash = generate_content_hash(new_document, new_tags, custom_metadata)

            # Check for duplicate hash with different ID
            duplicate_check = self.collection.get(where={"hash": new_hash})
            if duplicate_check["ids"] and duplicate_check["ids"][0] != id:
                return False, f"Update would create duplicate of memory ID: {duplicate_check['ids'][0]}"

            new_metadata["hash"] = new_hash

            # Merge custom metadata back
            new_metadata.update(custom_metadata)

            # Update timestamps
            import time
            now = time.time()
            now_iso = datetime.utcfromtimestamp(now).isoformat() + "Z"
            new_metadata["updated_at"] = now
            new_metadata["updated_at_iso"] = now_iso

            # Upsert the updated memory
            self.collection.upsert(
                ids=[id],
                documents=[new_document],
                metadatas=[new_metadata]
            )

            if not updated_fields:
                return True, "No changes specified"

            return True, f"Updated: {', '.join(updated_fields)}"

        except Exception as e:
            error_msg = f"Error updating memory: {str(e)}"
            logger.error(error_msg)
            traceback.print_exc()
            return False, error_msg

    async def recall(self, query: Optional[str] = None, n_results: int = 5, start_timestamp: Optional[float] = None, end_timestamp: Optional[float] = None) -> List[MemoryQueryResult]:
        """
        Retrieve memories with combined time filtering and optional semantic search.
        
        Args:
            query: Optional semantic search query. If None, only time filtering is applied.
            n_results: Maximum number of results to return.
            start_timestamp: Optional start time for filtering.
            end_timestamp: Optional end time for filtering.
            
        Returns:
            List of MemoryQueryResult objects.
        """
        try:
            # Check if collection is initialized
            if self.collection is None:
                logger.error("Collection not initialized, cannot retrieve memories")
                return []
                
            # Build time filtering where clause
            where_clause = {}
            if start_timestamp is not None or end_timestamp is not None:
                where_clause = {"$and": []}
                
            if start_timestamp is not None:
                start_timestamp = self.normalize_timestamp(start_timestamp)
                where_clause["$and"].append({"timestamp": {"$gte": float(start_timestamp)}})

            if end_timestamp is not None:
                end_timestamp = self.normalize_timestamp(end_timestamp)
                where_clause["$and"].append({"timestamp": {"$lte": float(end_timestamp)}})

            # If there's no valid where clause, set it to None to avoid ChromaDB errors
            if not where_clause.get("$and", []):
                where_clause = None
                
            # Log the where clause for debugging
            logger.info(f"Time filtering where clause: {where_clause}")
                
            # Determine whether to use semantic search or just time-based filtering
            if query:
                # Combined semantic search with time filtering
                try:
                    results = self.collection.query(
                        query_texts=[query],
                        n_results=n_results,
                        where=where_clause,
                        include=["documents", "metadatas", "distances"]
                    )
                    
                    if not results["ids"] or not results["ids"][0]:
                        return []
                    
                    memory_results = []
                    for i in range(len(results["ids"][0])):
                        metadata = results["metadatas"][0][i]
                        
                        # Parse tags from JSON string or comma-separated format
                        tags = self._parse_tags_fast(metadata.get("tags", ""))
                        
                        # Reconstruct memory object with proper timestamp handling
                        # Use stored timestamps or fall back to legacy timestamp field
                        created_at = metadata.get("created_at") or metadata.get("timestamp_float") or metadata.get("timestamp")
                        created_at_iso = metadata.get("created_at_iso") or metadata.get("timestamp_str")
                        updated_at = metadata.get("updated_at") or created_at
                        updated_at_iso = metadata.get("updated_at_iso") or created_at_iso
                        
                        memory = Memory(
                            id=results["ids"][0][i],
                            content=results["documents"][0][i],
                            content_hash=metadata["hash"],
                            tags=tags,
                            # Restore timestamps with fallback logic
                            created_at=created_at,
                            created_at_iso=created_at_iso,
                            updated_at=updated_at,
                            updated_at_iso=updated_at_iso,
                            # Include additional metadata
                            metadata={k: v for k, v in metadata.items()
                                    if k not in ["id", "hash", "tags", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
                        )
                        
                        # Calculate cosine similarity from distance
                        similarity = 1.0 - results["distances"][0][i]
                        
                        memory_results.append(MemoryQueryResult(memory=memory, relevance_score=similarity))
                    
                    return memory_results
                except Exception as query_error:
                    logger.error(f"Error in semantic search: {str(query_error)}")
                    # Fall back to time-based retrieval on error
                    logger.info("Falling back to time-based retrieval")
            
            # Time-based filtering only (or fallback from failed semantic search)
            results = self.collection.get(
                where=where_clause,
                limit=n_results,
                include=["metadatas", "documents"]
            )

            if not results["ids"]:
                return []
                
            memory_results = []
            for i in range(len(results["ids"])):
                metadata = results["metadatas"][i]
                try:
                    tags = self._parse_tags_fast(metadata.get("tags", ""))
                except Exception:
                    tags = []
                
                # Reconstruct memory object with proper timestamp handling
                # Use stored timestamps or fall back to legacy timestamp field
                created_at = metadata.get("created_at") or metadata.get("timestamp_float") or metadata.get("timestamp")
                created_at_iso = metadata.get("created_at_iso") or metadata.get("timestamp_str")
                updated_at = metadata.get("updated_at") or created_at
                updated_at_iso = metadata.get("updated_at_iso") or created_at_iso
                
                memory = Memory(
                    id=results["ids"][i],
                    content=results["documents"][i],
                    content_hash=metadata["hash"],
                    tags=tags,
                    # Restore timestamps with fallback logic
                    created_at=created_at,
                    created_at_iso=created_at_iso,
                    updated_at=updated_at,
                    updated_at_iso=updated_at_iso,
                    # Include additional metadata
                    metadata={k: v for k, v in metadata.items()
                             if k not in ["id", "type", "hash", "tags", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
                )
                # For time-based retrieval, we don't have a relevance score
                memory_results.append(MemoryQueryResult(memory=memory, relevance_score=None))

            return memory_results

        except Exception as e:
            logger.error(f"Error in recall: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def initialize(self) -> None:
        """
        Initialize the storage backend (async method for compatibility).
        
        Since ChromaMemoryStorage initialization happens in __init__,
        this method just verifies that initialization was successful.
        """
        if not self.is_initialized():
            raise RuntimeError("ChromaMemoryStorage initialization incomplete")
        logger.info("ChromaMemoryStorage async initialization verified")
    
    def is_initialized(self) -> bool:
        """Check if the storage is properly initialized."""
        return (self.collection is not None and 
                self.embedding_function is not None and 
                self.client is not None)
    
    def get_initialization_status(self) -> Dict[str, Any]:
        """Get detailed initialization status for debugging."""
        return {
            "collection_initialized": self.collection is not None,
            "embedding_function_initialized": self.embedding_function is not None,
            "client_initialized": self.client is not None,
            "model_initialized": self.model is not None,
            "path": self.path,
            "is_fully_initialized": self.is_initialized()
        }
    
    def _optimize_metadata_for_chroma(self, memory: Memory) -> Dict[str, Any]:
        """Optimized metadata formatting with minimal serialization overhead."""
        # Ensure timestamps are properly synchronized
        memory._sync_timestamps(
            created_at=memory.created_at,
            created_at_iso=memory.created_at_iso,
            updated_at=memory.updated_at,
            updated_at_iso=memory.updated_at_iso
        )

        # Use streamlined metadata structure
        # IMPORTANT: Store timestamp as float to preserve sub-second precision
        metadata = {
            "id": memory.id,
            "hash": memory.content_hash,

            "timestamp": float(memory.created_at),  # Changed from int() to float()
            "created_at_iso": memory.created_at_iso,
        }

        # Optimize tag storage - use comma-separated string for performance
        if memory.tags:
            if isinstance(memory.tags, list):
                # Store as comma-separated for ChromaDB compatibility
                metadata["tags"] = ",".join(str(tag).strip() for tag in memory.tags if str(tag).strip())
            elif isinstance(memory.tags, str):
                # Clean up the string
                tags = [tag.strip() for tag in memory.tags.split(",") if tag.strip()]
                metadata["tags"] = ",".join(tags)
        else:
            metadata["tags"] = ""

        # Add additional metadata efficiently
        for key, value in memory.metadata.items():
            if isinstance(value, (str, int, float, bool)):
                metadata[key] = value

        return metadata
    
    def _parse_tags_fast(self, tag_string: str) -> List[str]:
        """Fast tag parsing from comma-separated string or JSON array.
        
        Provides backward compatibility with both storage formats.
        """
        if not tag_string:
            return []
            
        # Try to parse as JSON first (old format)
        if tag_string.startswith("[") and tag_string.endswith("]"):
            try:
                return json.loads(tag_string)
            except json.JSONDecodeError:
                pass
                
        # If not JSON or parsing fails, treat as comma-separated (new format)
        return [tag.strip() for tag in tag_string.split(",") if tag.strip()]

    async def retrieve(self, query: str, n_results: int = 5) -> List[MemoryQueryResult]:
        """Retrieve memories using semantic search with performance optimizations."""
        try:
            # Check if collection is initialized
            if self.collection is None:
                logger.error("Collection not initialized, cannot retrieve memories")
                return []
            
            # Check if embedding function is available
            if self.embedding_function is None:
                logger.error("Embedding function not initialized, cannot retrieve memories")
                return []
            
            # Performance tracking
            start_time = time.time()
            
            # Try cached embedding first for better performance
            cached_embedding = None
            cache_hit = False
            
            if self.enable_query_cache:
                cached_embedding = self._cached_embed_query(query)
                if cached_embedding:
                    cache_hit = True
                    _PERFORMANCE_STATS["cache_hits"] += 1
                else:
                    _PERFORMANCE_STATS["cache_misses"] += 1
            
            try:
                if cached_embedding:
                    # Use cached embedding for faster query
                    results = self.collection.query(
                        query_embeddings=[list(cached_embedding)],
                        n_results=n_results,
                        include=["documents", "metadatas", "distances"]
                    )
                else:
                    # Standard query with text
                    results = self.collection.query(
                        query_texts=[query],
                        n_results=n_results,
                        include=["documents", "metadatas", "distances"]
                    )
            except Exception as query_error:
                logger.warning(f"Error during query operation: {str(query_error)}")
                
                # Fallback: Try with direct embedding if available
                if self.model and not cached_embedding:
                    try:
                        logger.info("Attempting fallback query with direct embedding")
                        query_embedding = self.model.encode(
                            query,
                            batch_size=self.batch_size,
                            show_progress_bar=False
                        ).tolist()
                        
                        results = self.collection.query(
                            query_embeddings=[query_embedding],
                            n_results=n_results,
                            include=["documents", "metadatas", "distances"]
                        )
                    except Exception as fallback_error:
                        logger.error(f"Fallback query also failed: {str(fallback_error)}")
                        return []
                else:
                    return []
            
            query_time = time.time() - start_time
            _PERFORMANCE_STATS["query_times"].append(query_time)
            
            # Keep only last 100 query times
            if len(_PERFORMANCE_STATS["query_times"]) > 100:
                _PERFORMANCE_STATS["query_times"] = _PERFORMANCE_STATS["query_times"][-100:]
            
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Query completed in {query_time:.4f}s (cache_hit: {cache_hit})")
            
            if not results["ids"] or not results["ids"][0]:
                return []
            
            # Process results efficiently
            memory_results = []
            for i in range(len(results["ids"][0])):
                metadata = results["metadatas"][0][i]
                
                # Fast tag parsing using optimized method that handles both formats
                tags = self._parse_tags_fast(metadata.get("tags", ""))
                
                # Reconstruct memory object with proper timestamp handling
                # Use stored timestamps or fall back to legacy timestamp field
                created_at = metadata.get("created_at") or metadata.get("timestamp_float") or metadata.get("timestamp")
                created_at_iso = metadata.get("created_at_iso") or metadata.get("timestamp_str")
                updated_at = metadata.get("updated_at") or created_at
                updated_at_iso = metadata.get("updated_at_iso") or created_at_iso
                
                memory = Memory(
                    id=results["ids"][0][i],
                    content=results["documents"][0][i],
                    content_hash=metadata["hash"],
                    tags=tags,
                    # Restore timestamps with fallback logic
                    created_at=created_at,
                    created_at_iso=created_at_iso,
                    updated_at=updated_at,
                    updated_at_iso=updated_at_iso,
                    # Include additional metadata
                    metadata={k: v for k, v in metadata.items()
                             if k not in ["id", "hash", "tags", "created_at", "created_at_iso", "updated_at", "updated_at_iso", "timestamp", "timestamp_float", "timestamp_str"]}
                )
                
                # Calculate cosine similarity from distance
                distance = results["distances"][0][i]
                similarity = 1 - distance
                
                memory_results.append(MemoryQueryResult(memory, similarity))
            
            return memory_results
            
        except Exception as e:
            logger.error(f"Error retrieving memories: {str(e)}")
            logger.error(traceback.format_exc())
            return []
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics for monitoring."""
        with _CACHE_LOCK:
            stats = {
                "model_cache_size": len(_MODEL_CACHE),
                "embedding_cache_size": len(_EMBEDDING_CACHE),
                "query_cache_size": len(_QUERY_CACHE),
                "cache_hits": _PERFORMANCE_STATS["cache_hits"],
                "cache_misses": _PERFORMANCE_STATS["cache_misses"],
                "avg_query_time": 0.0,
                "recent_query_times": _PERFORMANCE_STATS["query_times"][-10:],  # Last 10
                "cache_hit_ratio": 0.0
            }
            
            # Calculate average query time
            if _PERFORMANCE_STATS["query_times"]:
                stats["avg_query_time"] = sum(_PERFORMANCE_STATS["query_times"]) / len(_PERFORMANCE_STATS["query_times"])
            
            # Calculate cache hit ratio
            total_requests = stats["cache_hits"] + stats["cache_misses"]
            if total_requests > 0:
                stats["cache_hit_ratio"] = stats["cache_hits"] / total_requests
            
            return stats
    
    def clear_caches(self):
        """Clear all caches for memory management."""
        with _CACHE_LOCK:
            _EMBEDDING_CACHE.clear()
            _QUERY_CACHE.clear()
            # Don't clear model cache as it's expensive to reload
            _PERFORMANCE_STATS["cache_hits"] = 0
            _PERFORMANCE_STATS["cache_misses"] = 0
        
        # Clear LRU cache
        if hasattr(self._cached_embed_query, 'cache_clear'):
            self._cached_embed_query.cache_clear()
        
        logger.info("Cleared embedding and query caches")
    
    def record_query_time(self, duration: float):
        """Record a query time for performance tracking."""
        _PERFORMANCE_STATS["query_times"].append(duration)
        if len(_PERFORMANCE_STATS["query_times"]) > 100:
            _PERFORMANCE_STATS["query_times"] = _PERFORMANCE_STATS["query_times"][-100:]