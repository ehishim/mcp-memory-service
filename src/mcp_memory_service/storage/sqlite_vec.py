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
SQLite-vec storage backend for MCP Memory Service.
Provides a lightweight alternative to ChromaDB using sqlite-vec extension.
"""

import sqlite3
import json
import logging
import traceback
import time
import os
from typing import List, Dict, Any, Tuple, Optional, Set, Callable
from datetime import datetime
import asyncio
import random

# Import sqlite-vec with fallback
try:
    import sqlite_vec
    from sqlite_vec import serialize_float32
    SQLITE_VEC_AVAILABLE = True
except ImportError:
    SQLITE_VEC_AVAILABLE = False
    # Warning will be shown during initialization, not module import

# Import sentence transformers with fallback
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    # Warning will be shown during initialization, not module import

from .base import MemoryStorage
from ..models.memory import Memory, MemoryQueryResult
from ..utils.hashing import generate_content_hash
from ..utils.system_detection import (
    get_system_info,
    get_optimal_embedding_settings,
    get_torch_device,
    AcceleratorType
)

logger = logging.getLogger(__name__)

# Global model cache for performance optimization
_MODEL_CACHE = {}
_EMBEDDING_CACHE = {}


class SqliteVecMemoryStorage(MemoryStorage):
    """
    SQLite-vec based memory storage implementation.
    
    This backend provides a lightweight alternative to ChromaDB using sqlite-vec
    for vector similarity search while maintaining the same interface.
    """
    
    def __init__(self, db_path: str, embedding_model: str = "all-MiniLM-L6-v2"):
        """
        Initialize SQLite-vec storage.
        
        Args:
            db_path: Path to SQLite database file
            embedding_model: Name of sentence transformer model to use
        """
        self.db_path = db_path
        self.embedding_model_name = embedding_model
        self.conn = None
        self.embedding_model = None
        self.embedding_dimension = 384  # Default for all-MiniLM-L6-v2
        
        # Performance settings
        self.enable_cache = True
        self.batch_size = 32
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else '.', exist_ok=True)
        
        logger.info(f"Initialized SQLite-vec storage at: {self.db_path}")
    
    async def _execute_with_retry(self, operation: Callable, max_retries: int = 3, initial_delay: float = 0.1):
        """
        Execute a database operation with exponential backoff retry logic.
        
        Args:
            operation: The database operation to execute
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay in seconds before first retry
            
        Returns:
            The result of the operation
            
        Raises:
            The last exception if all retries fail
        """
        last_exception = None
        delay = initial_delay
        
        for attempt in range(max_retries + 1):
            try:
                return operation()
            except sqlite3.OperationalError as e:
                last_exception = e
                error_msg = str(e).lower()
                
                # Check if error is related to database locking
                if "locked" in error_msg or "busy" in error_msg:
                    if attempt < max_retries:
                        # Add jitter to prevent thundering herd
                        jittered_delay = delay * (1 + random.uniform(-0.1, 0.1))
                        logger.warning(f"Database locked, retrying in {jittered_delay:.2f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(jittered_delay)
                        # Exponential backoff
                        delay *= 2
                        continue
                    else:
                        logger.error(f"Database locked after {max_retries} retries")
                else:
                    # Non-retryable error
                    raise
            except Exception as e:
                # Non-SQLite errors are not retried
                raise
        
        # If we get here, all retries failed
        raise last_exception
    
    async def initialize(self):
        """Initialize the SQLite database with vec0 extension."""
        try:
            if not SQLITE_VEC_AVAILABLE:
                raise ImportError("sqlite-vec is not available. Install with: pip install sqlite-vec")
            
            if not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise ImportError("sentence-transformers is not available. Install with: pip install sentence-transformers torch")
            
            # Connect to database
            self.conn = sqlite3.connect(self.db_path)
            self.conn.enable_load_extension(True)
            
            # Load sqlite-vec extension
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            
            # Apply default pragmas for concurrent access
            default_pragmas = {
                "journal_mode": "WAL",  # Enable WAL mode for concurrent access
                "busy_timeout": "5000",  # 5 second timeout for locked database
                "synchronous": "NORMAL",  # Balanced performance/safety
                "cache_size": "10000",  # Increase cache size
                "temp_store": "MEMORY"  # Use memory for temp tables
            }
            
            # Check for custom pragmas from environment variable
            custom_pragmas = os.environ.get("MCP_MEMORY_SQLITE_PRAGMAS", "")
            if custom_pragmas:
                # Parse custom pragmas (format: "pragma1=value1,pragma2=value2")
                for pragma_pair in custom_pragmas.split(","):
                    pragma_pair = pragma_pair.strip()
                    if "=" in pragma_pair:
                        pragma_name, pragma_value = pragma_pair.split("=", 1)
                        default_pragmas[pragma_name.strip()] = pragma_value.strip()
                        logger.info(f"Custom pragma from env: {pragma_name}={pragma_value}")
            
            # Apply all pragmas
            applied_pragmas = []
            for pragma_name, pragma_value in default_pragmas.items():
                try:
                    self.conn.execute(f"PRAGMA {pragma_name}={pragma_value}")
                    applied_pragmas.append(f"{pragma_name}={pragma_value}")
                except sqlite3.Error as e:
                    logger.warning(f"Failed to set pragma {pragma_name}={pragma_value}: {e}")
            
            logger.info(f"SQLite pragmas applied: {', '.join(applied_pragmas)}")
            
            # Create regular table for memory data (UUID + hash model)
            self.conn.execute('''
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    hash TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT,
                    metadata TEXT,
                    created_at REAL,
                    updated_at REAL,
                    created_at_iso TEXT,
                    updated_at_iso TEXT
                )
            ''')
            
            # Initialize embedding model BEFORE creating vector table
            await self._initialize_embedding_model()
            
            # Now create virtual table with correct dimensions
            self.conn.execute(f'''
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(
                    content_embedding FLOAT[{self.embedding_dimension}]
                )
            ''')
            
            # Create indexes for better performance
            self.conn.execute('CREATE INDEX IF NOT EXISTS idx_hash ON memories(hash)')
            self.conn.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)')
            
            logger.info(f"SQLite-vec storage initialized successfully with embedding dimension: {self.embedding_dimension}")
            
        except Exception as e:
            error_msg = f"Failed to initialize SQLite-vec storage: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            raise RuntimeError(error_msg)
    
    async def _initialize_embedding_model(self):
        """Initialize the embedding model (ONNX or SentenceTransformer based on configuration)."""
        global _MODEL_CACHE
        
        try:
            # Check if we should use ONNX
            use_onnx = os.environ.get('MCP_MEMORY_USE_ONNX', '').lower() in ('1', 'true', 'yes')
            
            if use_onnx:
                # Try to use ONNX embeddings
                logger.info("Attempting to use ONNX embeddings (PyTorch-free)")
                try:
                    from ..embeddings import get_onnx_embedding_model
                    
                    # Check cache first
                    cache_key = f"onnx_{self.embedding_model_name}"
                    if cache_key in _MODEL_CACHE:
                        self.embedding_model = _MODEL_CACHE[cache_key]
                        logger.info(f"Using cached ONNX embedding model: {self.embedding_model_name}")
                        return
                    
                    # Create ONNX model
                    onnx_model = get_onnx_embedding_model(self.embedding_model_name)
                    if onnx_model:
                        self.embedding_model = onnx_model
                        self.embedding_dimension = onnx_model.embedding_dimension
                        _MODEL_CACHE[cache_key] = onnx_model
                        logger.info(f"ONNX embedding model loaded successfully. Dimension: {self.embedding_dimension}")
                        return
                    else:
                        logger.warning("ONNX model creation failed, falling back to SentenceTransformer")
                except ImportError as e:
                    logger.warning(f"ONNX dependencies not available: {e}")
                except Exception as e:
                    logger.warning(f"Failed to initialize ONNX embeddings: {e}")
            
            # Fall back to SentenceTransformer
            if not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise RuntimeError("Neither ONNX nor sentence-transformers available. Install one: pip install onnxruntime tokenizers OR pip install sentence-transformers torch")
            
            # Check cache first
            cache_key = self.embedding_model_name
            if cache_key in _MODEL_CACHE:
                self.embedding_model = _MODEL_CACHE[cache_key]
                logger.info(f"Using cached embedding model: {self.embedding_model_name}")
                return
            
            # Get system info for optimal settings
            system_info = get_system_info()
            device = get_torch_device()
            
            logger.info(f"Loading embedding model: {self.embedding_model_name}")
            logger.info(f"Using device: {device}")
            
            # Configure for offline mode if models are cached
            # Only set offline mode if we detect cached models to prevent initial downloads
            hf_home = os.environ.get('HF_HOME', os.path.expanduser("~/.cache/huggingface"))
            model_cache_path = os.path.join(hf_home, "hub", f"models--sentence-transformers--{self.embedding_model_name.replace('/', '--')}")
            if os.path.exists(model_cache_path):
                os.environ['HF_HUB_OFFLINE'] = '1'
                os.environ['TRANSFORMERS_OFFLINE'] = '1'
            
            # Try to load from cache first, fallback to direct model name
            try:
                # First try loading from Hugging Face cache
                hf_home = os.environ.get('HF_HOME', os.path.expanduser("~/.cache/huggingface"))
                cache_path = os.path.join(hf_home, "hub", f"models--sentence-transformers--{self.embedding_model_name.replace('/', '--')}")
                if os.path.exists(cache_path):
                    # Find the snapshot directory
                    snapshots_path = os.path.join(cache_path, "snapshots")
                    if os.path.exists(snapshots_path):
                        snapshot_dirs = [d for d in os.listdir(snapshots_path) if os.path.isdir(os.path.join(snapshots_path, d))]
                        if snapshot_dirs:
                            model_path = os.path.join(snapshots_path, snapshot_dirs[0])
                            logger.info(f"Loading model from cache: {model_path}")
                            self.embedding_model = SentenceTransformer(model_path, device=device)
                        else:
                            raise FileNotFoundError("No snapshot found")
                    else:
                        raise FileNotFoundError("No snapshots directory")
                else:
                    raise FileNotFoundError("No cache found")
            except Exception as cache_error:
                logger.warning(f"Failed to load from cache: {cache_error}")
                # Fallback to normal loading (may fail if offline)
                logger.info("Attempting normal model loading...")
                self.embedding_model = SentenceTransformer(self.embedding_model_name, device=device)
            
            # Update embedding dimension based on actual model
            test_embedding = self.embedding_model.encode(["test"], convert_to_numpy=True)
            self.embedding_dimension = test_embedding.shape[1]
            
            # Cache the model
            _MODEL_CACHE[cache_key] = self.embedding_model
            
            logger.info(f"Embedding model loaded successfully. Dimension: {self.embedding_dimension}")
            
        except Exception as e:
            logger.error(f"Failed to initialize embedding model: {str(e)}")
            logger.error(traceback.format_exc())
            # Continue without embeddings - some operations may still work
    
    def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        if not self.embedding_model:
            raise RuntimeError("No embedding model available. Ensure sentence-transformers is installed and model is loaded.")
        
        try:
            # Check cache first
            if self.enable_cache:
                cache_key = hash(text)
                if cache_key in _EMBEDDING_CACHE:
                    return _EMBEDDING_CACHE[cache_key]
            
            # Generate embedding
            embedding = self.embedding_model.encode([text], convert_to_numpy=True)[0]
            embedding_list = embedding.tolist()
            
            # Validate embedding
            if not embedding_list:
                raise ValueError("Generated embedding is empty")
            
            if len(embedding_list) != self.embedding_dimension:
                raise ValueError(f"Embedding dimension mismatch: expected {self.embedding_dimension}, got {len(embedding_list)}")
            
            # Validate values are finite
            if not all(isinstance(x, (int, float)) and not (x != x) and x != float('inf') and x != float('-inf') for x in embedding_list):
                raise ValueError("Embedding contains invalid values (NaN or infinity)")
            
            # Cache the result
            if self.enable_cache:
                _EMBEDDING_CACHE[cache_key] = embedding_list
            
            return embedding_list
            
        except Exception as e:
            logger.error(f"Failed to generate embedding: {str(e)}")
            raise RuntimeError(f"Failed to generate embedding: {str(e)}") from e
    
    async def store(self, memory: Memory) -> Tuple[bool, str]:
        """Store a memory in the SQLite-vec database."""
        try:
            if not self.conn:
                return False, "Database not initialized"
            
            # Check for duplicates by hash (deduplication)
            cursor = self.conn.execute(
                'SELECT id FROM memories WHERE hash = ?',
                (memory.hash,)
            )
            existing = cursor.fetchone()
            if existing:
                existing_id = existing[0]
                return False, f"Duplicate content detected (existing ID: {existing_id})"
            
            # Generate and validate embedding
            try:
                embedding = self._generate_embedding(memory.content)
            except Exception as e:
                logger.error(f"Failed to generate embedding for memory {memory.hash}: {str(e)}")
                return False, f"Failed to generate embedding: {str(e)}"
            
            # Prepare metadata
            tags_str = ",".join(memory.tags) if memory.tags else ""
            metadata_str = json.dumps(memory.metadata) if memory.metadata else "{}"
            
            # Insert into memories table (metadata) with retry logic
            def insert_memory():
                cursor = self.conn.execute('''
                    INSERT INTO memories (
                        id, hash, content, tags,
                        metadata, created_at, updated_at, created_at_iso, updated_at_iso
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    memory.id,
                    memory.hash,
                    memory.content,
                    tags_str,
                    metadata_str,
                    memory.created_at,
                    memory.updated_at,
                    memory.created_at_iso,
                    memory.updated_at_iso
                ))
                # Get the rowid for the inserted row
                return self.conn.execute('SELECT rowid FROM memories WHERE id = ?', (memory.id,)).fetchone()[0]
            
            memory_rowid = await self._execute_with_retry(insert_memory)
            
            # Insert into embeddings table with retry logic
            def insert_embedding():
                # Check if we can insert with specific rowid
                try:
                    self.conn.execute('''
                        INSERT INTO memory_embeddings (rowid, content_embedding)
                        VALUES (?, ?)
                    ''', (
                        memory_rowid,
                        serialize_float32(embedding)
                    ))
                except sqlite3.Error as e:
                    # If rowid insert fails, try without specifying rowid
                    logger.warning(f"Failed to insert with rowid {memory_rowid}: {e}. Trying without rowid.")
                    self.conn.execute('''
                        INSERT INTO memory_embeddings (content_embedding)
                        VALUES (?)
                    ''', (
                        serialize_float32(embedding),
                    ))
            
            await self._execute_with_retry(insert_embedding)
            
            # Commit with retry logic
            await self._execute_with_retry(self.conn.commit)

            logger.info(f"Successfully stored memory: {memory.hash}")
            return True, "Memory stored successfully"
            
        except Exception as e:
            error_msg = f"Failed to store memory: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return False, error_msg
    
    async def retrieve(
        self,
        query: str,
        n_results: int = 5,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> Tuple[List[MemoryQueryResult], int]:
        """
        Retrieve memories using semantic search with pagination support.

        Args:
            query: Search query text
            n_results: Number of results (used when limit not provided)
            limit: Maximum results to return (overrides n_results for pagination)
            offset: Number of results to skip (for pagination)

        Returns:
            Tuple of (results, total_count)
        """
        try:
            if not self.conn:
                logger.error("Database not initialized")
                return [], 0

            if not self.embedding_model:
                logger.warning("No embedding model available, cannot perform semantic search")
                return [], 0

            # Generate query embedding
            try:
                query_embedding = self._generate_embedding(query)
            except Exception as e:
                logger.error(f"Failed to generate query embedding: {str(e)}")
                return [], 0

            # First, check if embeddings table has data
            cursor = self.conn.execute('SELECT COUNT(*) FROM memory_embeddings')
            embedding_count = cursor.fetchone()[0]

            if embedding_count == 0:
                logger.warning("No embeddings found in database. Memories may have been stored without embeddings.")
                return [], 0

            # Get total count of memories for pagination
            total_count = embedding_count

            # Apply pagination - use limit if provided, otherwise use n_results
            actual_limit = limit if limit is not None else n_results
            actual_offset = offset if offset is not None else 0

            # Perform vector similarity search using JOIN with retry logic
            def search_memories():
                # Try direct rowid join first with pagination
                # Note: sqlite-vec k parameter controls initial candidates, we filter afterward
                # Use a larger k to ensure we have enough candidates after offset
                k_value = actual_limit + actual_offset + 100

                cursor = self.conn.execute('''
                    SELECT m.id, m.hash, m.content, m.tags, m.metadata,
                           m.created_at, m.updated_at, m.created_at_iso, m.updated_at_iso,
                           e.distance
                    FROM memories m
                    INNER JOIN (
                        SELECT rowid, distance
                        FROM memory_embeddings
                        WHERE content_embedding MATCH ? AND k = ?
                        ORDER BY distance
                    ) e ON m.id = e.rowid
                    ORDER BY e.distance, m.created_at DESC
                    LIMIT ? OFFSET ?
                ''', (serialize_float32(query_embedding), k_value, actual_limit, actual_offset))

                # Check if we got results
                results = cursor.fetchall()
                if not results:
                    # Log debug info
                    logger.debug("No results from vector search. Checking database state...")
                    mem_count = self.conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
                    logger.debug(f"Memories table has {mem_count} rows, embeddings table has {embedding_count} rows")

                return results

            search_results = await self._execute_with_retry(search_memories)

            results = []
            for row in search_results:
                try:
                    # Parse row data
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso, distance = row[5:]

                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    # Create Memory object
                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )

                    # Calculate relevance score (lower distance = higher relevance)
                    relevance_score = max(0.0, 1.0 - distance)

                    results.append(MemoryQueryResult(
                        memory=memory,
                        relevance_score=relevance_score,
                        debug_info={"distance": distance, "backend": "sqlite-vec"}
                    ))

                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue

            logger.info(f"Retrieved {len(results)} of {total_count} memories for query (limit={actual_limit}, offset={actual_offset}): {query}")
            return results, total_count

        except Exception as e:
            logger.error(f"Failed to retrieve memories: {str(e)}")
            logger.error(traceback.format_exc())
            return [], 0
    
    async def search_by_tag(self, tags: List[str]) -> List[Memory]:
        """Search memories by tags."""
        try:
            if not self.conn:
                logger.error("Database not initialized")
                return []
            
            if not tags:
                return []
            
            # Build query for tag search (OR logic)
            tag_conditions = " OR ".join(["tags LIKE ?" for _ in tags])
            tag_params = [f"%{tag}%" for tag in tags]
            
            cursor = self.conn.execute(f'''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                WHERE {tag_conditions}
                ORDER BY created_at DESC
            ''', tag_params)

            results = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

                    # Parse tags and metadata
                    memory_tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=memory_tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )

                    results.append(memory)
                    
                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue
            
            logger.info(f"Found {len(results)} memories with tags: {tags}")
            return results
            
        except Exception as e:
            logger.error(f"Failed to search by tags: {str(e)}")
            logger.error(traceback.format_exc())
            return []
    
    async def search_by_tags(
        self,
        tags: List[str],
        operation: str = "AND",
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> Tuple[List[Memory], int]:
        """
        Search memories by tags with AND/OR operation support and pagination.

        Args:
            tags: List of tags to search for
            operation: "AND" (all tags) or "OR" (any tag)
            limit: Maximum results to return
            offset: Number of results to skip

        Returns:
            Tuple of (memories, total_count)
        """
        try:
            if not self.conn:
                logger.error("Database not initialized")
                return [], 0

            if not tags:
                return [], 0

            # Build query based on operation
            if operation.upper() == "AND":
                # All tags must be present (each tag must appear in the tags field)
                tag_conditions = " AND ".join(["tags LIKE ?" for _ in tags])
            else:  # OR operation (default for backward compatibility)
                tag_conditions = " OR ".join(["tags LIKE ?" for _ in tags])

            tag_params = [f"%{tag}%" for tag in tags]

            # Get total count first
            count_query = f'SELECT COUNT(*) FROM memories WHERE {tag_conditions}'
            cursor = self.conn.execute(count_query, tag_params)
            total_count = cursor.fetchone()[0]

            # Build main query with pagination
            query = f'''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                WHERE {tag_conditions}
                ORDER BY updated_at DESC
            '''

            # Add pagination if provided
            if limit is not None:
                query += ' LIMIT ?'
                tag_params.append(limit)
                if offset is not None:
                    query += ' OFFSET ?'
                    tag_params.append(offset)

            cursor = self.conn.execute(query, tag_params)

            results = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str, created_at, updated_at, created_at_iso, updated_at_iso = row

                    # Parse tags and metadata
                    memory_tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=memory_tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )

                    results.append(memory)

                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue

            logger.info(f"Found {len(results)} of {total_count} memories with tags: {tags} (operation: {operation}, limit={limit}, offset={offset})")
            return results, total_count

        except Exception as e:
            logger.error(f"Failed to search by tags with operation {operation}: {str(e)}")
            logger.error(traceback.format_exc())
            return [], 0
    
    async def delete(self, id: str) -> Tuple[bool, str]:
        """Delete a memory by its ID."""
        try:
            if not self.conn:
                return False, "Database not initialized"

            # Get the rowid first to delete corresponding embedding
            cursor = self.conn.execute('SELECT rowid FROM memories WHERE id = ?', (id,))
            row = cursor.fetchone()

            if row:
                rowid = row[0]
                # Delete from both tables
                self.conn.execute('DELETE FROM memory_embeddings WHERE rowid = ?', (rowid,))
                cursor = self.conn.execute('DELETE FROM memories WHERE id = ?', (id,))
                self.conn.commit()
            else:
                return False, f"Memory with ID {id} not found"

            if cursor.rowcount > 0:
                logger.info(f"Deleted memory: {id}")
                return True, f"Successfully deleted memory {id}"
            else:
                return False, f"Memory with ID {id} not found"

        except Exception as e:
            error_msg = f"Failed to delete memory: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    async def get_by_id(self, id: str) -> Optional[Memory]:
        """Get a memory by its ID."""
        try:
            if not self.conn:
                return None

            cursor = self.conn.execute('''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories WHERE id = ?
            ''', (id,))

            row = cursor.fetchone()
            if not row:
                return None

            id_val, hash_val, content, tags_str, metadata_str = row[:5]
            created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

            # Parse tags and metadata
            tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
            metadata = json.loads(metadata_str) if metadata_str else {}

            memory = Memory(
                id=id_val,
                content=content,
                hash=hash_val,
                tags=tags,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
                created_at_iso=created_at_iso,
                updated_at_iso=updated_at_iso
            )

            return memory

        except Exception as e:
            logger.error(f"Failed to get memory by ID {id}: {str(e)}")
            return None
    
    async def delete_by_tag(self, tags: List[str], operation: str = "OR") -> Tuple[int, str]:
        """Delete memories by tags with AND/OR logic (mirrors search_by_tags).

        Args:
            tags: List of tags to match
            operation: "AND" (all tags) or "OR" (any tag)

        Returns:
            Tuple of (count_deleted, message)
        """
        try:
            if not self.conn:
                return 0, "Database not initialized"

            if not tags:
                return 0, "No tags provided"

            # Build query based on operation (mirrors search_by_tags logic)
            if operation.upper() == "AND":
                # All tags must be present (AND logic)
                tag_conditions = " AND ".join(["tags LIKE ?" for _ in tags])
                operation_desc = "all tags"
            else:  # OR operation
                # Any tag present (OR logic)
                tag_conditions = " OR ".join(["tags LIKE ?" for _ in tags])
                operation_desc = "any tag"

            tag_params = [f"%{tag}%" for tag in tags]

            # Get the ids first to delete corresponding embeddings
            cursor = self.conn.execute(
                f'SELECT id FROM memories WHERE {tag_conditions}',
                tag_params
            )
            memory_ids = [row[0] for row in cursor.fetchall()]

            # Delete from both tables
            for memory_id in memory_ids:
                self.conn.execute('DELETE FROM memory_embeddings WHERE rowid = ?', (memory_id,))

            # Delete memories
            cursor = self.conn.execute(
                f'DELETE FROM memories WHERE {tag_conditions}',
                tag_params
            )
            self.conn.commit()

            count = cursor.rowcount
            logger.info(f"Deleted {count} memories with {operation_desc}: {tags}")

            if count > 0:
                return count, f"Successfully deleted {count} memories with {operation_desc}: {', '.join(tags)}"
            else:
                return 0, f"No memories found with {operation_desc}: {', '.join(tags)}"

        except Exception as e:
            error_msg = f"Failed to delete by tag: {str(e)}"
            logger.error(error_msg)
            return 0, error_msg
    
    async def cleanup_duplicates(self) -> Tuple[int, str]:
        """Remove duplicate memories based on content hash."""
        try:
            if not self.conn:
                return 0, "Database not initialized"
            
            # Find duplicates (keep the first occurrence)
            cursor = self.conn.execute('''
                DELETE FROM memories 
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM memories
                    GROUP BY hash
                )
            ''')
            self.conn.commit()
            
            count = cursor.rowcount
            logger.info(f"Cleaned up {count} duplicate memories")
            
            if count > 0:
                return count, f"Successfully removed {count} duplicate memories"
            else:
                return 0, "No duplicate memories found"
                
        except Exception as e:
            error_msg = f"Failed to cleanup duplicates: {str(e)}"
            logger.error(error_msg)
            return 0, error_msg

    async def create_backup(self, backup_dir: str) -> Tuple[bool, str, dict]:
        """
        Create a complete backup of the database with WAL checkpoint.

        Args:
            backup_dir: Directory path where backup will be created

        Returns:
            Tuple of (success: bool, message: str, info: dict)
            info contains: backup_path, timestamp, file_size, memory_count
        """
        try:
            import shutil
            from datetime import datetime
            from pathlib import Path

            if not self.conn:
                return False, "Database not initialized", {}

            # Create backup directory
            os.makedirs(backup_dir, exist_ok=True)

            # Checkpoint WAL to ensure all data is in main database file
            logger.info("Performing WAL checkpoint before backup...")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            # Copy database file
            db_filename = os.path.basename(self.db_path)
            backup_path = os.path.join(backup_dir, db_filename)
            shutil.copy2(self.db_path, backup_path)

            # Get backup info
            file_size = os.path.getsize(backup_path)
            cursor = self.conn.execute("SELECT COUNT(*) FROM memories")
            memory_count = cursor.fetchone()[0]
            timestamp = datetime.now().isoformat()

            info = {
                "backup_path": backup_path,
                "timestamp": timestamp,
                "file_size_bytes": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "memory_count": memory_count,
                "wal_checkpointed": True
            }

            logger.info(f"Backup created successfully: {backup_path} ({info['file_size_mb']} MB, {memory_count} memories)")
            return True, f"Backup created: {backup_path}", info

        except Exception as e:
            error_msg = f"Failed to create backup: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return False, error_msg, {}

    async def update_memory(
        self,
        id: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags_strategy: str = "replace",
        metadata_strategy: str = "replace"
    ) -> Tuple[bool, str]:
        """
        Update memory content, tags, and/or metadata with hash deduplication.

        Args:
            id: Memory ID to update
            content: New content (optional)
            tags: New tags (optional)
            metadata: New metadata (optional)
            tags_strategy: "replace" or "merge"
            metadata_strategy: "replace" or "merge"

        Returns:
            (success, message) tuple
        """
        try:
            if not self.conn:
                return False, "Database not initialized"

            # Get current memory
            memory = await self.get_by_id(id)
            if not memory:
                return False, f"Memory with ID {id} not found"

            # Validate strategies
            if tags_strategy not in ["replace", "merge"]:
                return False, f"Invalid tags_strategy: {tags_strategy}"
            if metadata_strategy not in ["replace", "merge"]:
                return False, f"Invalid metadata_strategy: {metadata_strategy}"

            updated_fields = []

            # Get current state
            cursor = self.conn.execute('''
                SELECT content, tags, metadata FROM memories WHERE id = ?
            ''', (id,))
            row = cursor.fetchone()

            if not row:
                return False, f"Memory with ID {id} not found"

            current_content, current_tags_str, current_metadata_str = row
            current_tags = [t.strip() for t in current_tags_str.split(",") if t.strip()] if current_tags_str else []
            current_metadata = json.loads(current_metadata_str) if current_metadata_str else {}

            # Determine new values
            new_content = content if content is not None else current_content

            # Process tags
            if tags is not None:
                if tags_strategy == "replace":
                    new_tags = tags
                else:  # merge
                    new_tags = list(set(current_tags + tags))  # Deduplicate
                updated_fields.append(f"tags ({tags_strategy})")
            else:
                new_tags = current_tags

            # Process metadata
            if metadata is not None:
                if metadata_strategy == "replace":
                    new_metadata = metadata
                else:  # merge
                    new_metadata = current_metadata.copy()
                    new_metadata.update(metadata)
                updated_fields.append(f"metadata ({metadata_strategy})")
            else:
                new_metadata = current_metadata

            # Track content update
            if content is not None:
                updated_fields.append("content")

            # Generate new hash with updated data
            from ..utils.hashing import generate_content_hash
            new_hash = generate_content_hash(new_content, new_tags, new_metadata)

            # Check for hash collision with different memory
            existing = self.conn.execute(
                'SELECT id FROM memories WHERE hash = ? AND id != ?',
                (new_hash, id)
            ).fetchone()

            if existing:
                return False, f"Update would create duplicate of memory ID: {existing[0]}"

            # Update database
            if updated_fields:
                new_tags_str = ",".join(new_tags)
                self.conn.execute('''
                    UPDATE memories SET
                        content = ?, hash = ?, tags = ?, metadata = ?,
                        updated_at = ?, updated_at_iso = ?
                    WHERE id = ?
                ''', (
                    new_content,
                    new_hash,
                    new_tags_str,
                    json.dumps(new_metadata),
                    time.time(),
                    datetime.now().isoformat() + 'Z',
                    id
                ))
                self.conn.commit()

                # Update embedding if content changed
                if content is not None:
                    try:
                        embedding = self.model.encode(new_content).tolist()
                        cursor = self.conn.execute('SELECT rowid FROM memories WHERE id = ?', (id,))
                        row = cursor.fetchone()
                        if row:
                            rowid = row[0]
                            self.conn.execute(
                                'UPDATE vec_memories SET embedding = ? WHERE rowid = ?',
                                (serialize_f32(embedding), rowid)
                            )
                            self.conn.commit()
                    except Exception as e:
                        logger.warning(f"Failed to update embedding: {e}")

            if not updated_fields:
                return True, "No changes specified"

            return True, f"Updated: {', '.join(updated_fields)}"

        except Exception as e:
            error_msg = f"Error updating memory: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return False, error_msg

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            if not self.conn:
                return {"error": "Database not initialized"}
            
            cursor = self.conn.execute('SELECT COUNT(*) FROM memories')
            total_memories = cursor.fetchone()[0]
            
            cursor = self.conn.execute('SELECT COUNT(DISTINCT tags) FROM memories WHERE tags != ""')
            unique_tags = cursor.fetchone()[0]
            
            # Get database file size
            file_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            
            return {
                "backend": "sqlite-vec",
                "total_memories": total_memories,
                "unique_tags": unique_tags,
                "database_size_bytes": file_size,
                "database_size_mb": round(file_size / (1024 * 1024), 2),
                "embedding_model": self.embedding_model_name,
                "embedding_dimension": self.embedding_dimension
            }
            
        except Exception as e:
            logger.error(f"Failed to get stats: {str(e)}")
            return {"error": str(e)}
    
    def sanitized(self, tags):
        """Sanitize and normalize tags to a JSON string.
        
        This method provides compatibility with the ChromaMemoryStorage interface.
        """
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
    
    async def recall(
        self,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None
    ) -> Tuple[List[MemoryQueryResult], int]:
        """
        Retrieve memories with combined time filtering and optional semantic search.

        Args:
            query: Optional semantic search query. If None, only time filtering is applied.
            limit: Maximum results to return (None = return all, capped at 4096 for semantic search).
            offset: Number of results to skip for pagination (default 0).
            start_timestamp: Optional start time for filtering.
            end_timestamp: Optional end time for filtering.

        Returns:
            Tuple of (results, total_count) where total_count is actual matches in database.
        """
        try:
            if not self.conn:
                logger.error("Database not initialized, cannot retrieve memories")
                return [], 0

            # Set defaults
            if offset is None:
                offset = 0

            # Build time filtering WHERE clause
            time_conditions = []
            params = []

            if start_timestamp is not None:
                time_conditions.append("created_at >= ?")
                params.append(float(start_timestamp))

            if end_timestamp is not None:
                time_conditions.append("created_at <= ?")
                params.append(float(end_timestamp))

            time_where = " AND ".join(time_conditions) if time_conditions else ""

            logger.info(f"Time filtering conditions: {time_where}, limit: {limit}, offset: {offset}")
            
            # Determine whether to use semantic search or just time-based filtering
            if query and self.embedding_model:
                # Combined semantic search with time filtering
                try:
                    # Generate query embedding
                    query_embedding = self._generate_embedding(query)

                    # Cap k value at 4096 (sqlite-vec limit)
                    k_value = min(4096, (limit or 100) + offset) if limit is not None else 4096

                    # First, get total count of matching results
                    count_query = '''
                        SELECT COUNT(*) FROM memories m
                        JOIN (
                            SELECT rowid
                            FROM memory_embeddings
                            WHERE content_embedding MATCH ? AND k = ?
                        ) e ON m.rowid = e.rowid
                    '''
                    if time_where:
                        count_query += f" WHERE {time_where}"

                    count_params = [serialize_float32(query_embedding), k_value] + params
                    total_count = self.conn.execute(count_query, count_params).fetchone()[0]

                    # Build SQL query with time filtering and pagination
                    base_query = '''
                        SELECT m.id, m.hash, m.content, m.tags, m.metadata,
                               m.created_at, m.updated_at, m.created_at_iso, m.updated_at_iso,
                               e.distance
                        FROM memories m
                        JOIN (
                            SELECT rowid, distance
                            FROM memory_embeddings
                            WHERE content_embedding MATCH ? AND k = ?
                            ORDER BY distance
                        ) e ON m.rowid = e.rowid
                    '''

                    if time_where:
                        base_query += f" WHERE {time_where}"

                    base_query += " ORDER BY e.distance, m.created_at DESC"

                    # Add LIMIT/OFFSET for pagination
                    if limit is not None:
                        base_query += " LIMIT ? OFFSET ?"
                        query_params = [serialize_float32(query_embedding), k_value] + params + [limit, offset]
                    else:
                        query_params = [serialize_float32(query_embedding), k_value] + params

                    cursor = self.conn.execute(base_query, query_params)

                    results = []
                    for row in cursor.fetchall():
                        try:
                            # Parse row data
                            id_val, hash_val, content, tags_str, metadata_str = row[:5]
                            created_at, updated_at, created_at_iso, updated_at_iso, distance = row[5:]

                            # Parse tags and metadata
                            tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                            metadata = json.loads(metadata_str) if metadata_str else {}

                            # Create Memory object
                            memory = Memory(
                                id=id_val,
                                content=content,
                                hash=hash_val,
                                tags=tags,
                                metadata=metadata,
                                created_at=created_at,
                                updated_at=updated_at,
                                created_at_iso=created_at_iso,
                                updated_at_iso=updated_at_iso
                            )

                            # Calculate relevance score (lower distance = higher relevance)
                            relevance_score = max(0.0, 1.0 - distance)

                            results.append(MemoryQueryResult(
                                memory=memory,
                                relevance_score=relevance_score,
                                debug_info={"distance": distance, "backend": "sqlite-vec", "time_filtered": bool(time_where)}
                            ))
                            
                        except Exception as parse_error:
                            logger.warning(f"Failed to parse memory result: {parse_error}")
                            continue

                    logger.info(f"Retrieved {len(results)}/{total_count} memories for semantic query (limit={limit}, offset={offset})")
                    return results, total_count
                    
                except Exception as query_error:
                    logger.error(f"Error in semantic search with time filter: {str(query_error)}")
                    # Fall back to time-based retrieval on error
                    logger.info("Falling back to time-based retrieval")
            
            # Time-based filtering only (or fallback from failed semantic search)
            # First get total count
            count_query = "SELECT COUNT(*) FROM memories"
            if time_where:
                count_query += f" WHERE {time_where}"

            total_count = self.conn.execute(count_query, params).fetchone()[0]

            # Build main query with pagination
            base_query = '''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
            '''

            if time_where:
                base_query += f" WHERE {time_where}"

            base_query += " ORDER BY created_at DESC"

            # Add LIMIT/OFFSET for pagination
            if limit is not None:
                base_query += " LIMIT ? OFFSET ?"
                query_params = params + [limit, offset]
            else:
                # No limit = return all (for wildcard "*")
                query_params = params

            cursor = self.conn.execute(base_query, query_params)

            results = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )

                    # For time-based retrieval, we don't have a relevance score
                    results.append(MemoryQueryResult(
                        memory=memory,
                        relevance_score=None,
                        debug_info={"backend": "sqlite-vec", "time_filtered": bool(time_where), "query_type": "time_based"}
                    ))
                    
                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue

            logger.info(f"Retrieved {len(results)}/{total_count} memories for time-based query (limit={limit}, offset={offset})")
            return results, total_count
            
        except Exception as e:
            logger.error(f"Error in recall: {str(e)}")
            logger.error(traceback.format_exc())
            return [], 0
    
    async def search_by_content(
        self,
        search_text: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> Tuple[List[Memory], int]:
        """
        Search memories containing specific text (substring search) with pagination.

        Args:
            search_text: Text to search for in content
            limit: Maximum results to return (default 10 if not specified)
            offset: Number of results to skip (default 0)

        Returns:
            Tuple of (memories, total_count)
        """
        try:
            if not self.conn:
                logger.error("Database not initialized")
                return [], 0

            if not search_text:
                return [], 0

            # Get total count first
            count_query = 'SELECT COUNT(*) FROM memories WHERE content LIKE ?'
            cursor = self.conn.execute(count_query, (f'%{search_text}%',))
            total_count = cursor.fetchone()[0]

            # Apply pagination - default limit is 10
            actual_limit = limit if limit is not None else 10
            actual_offset = offset if offset is not None else 0

            cursor = self.conn.execute('''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                WHERE content LIKE ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            ''', (f'%{search_text}%', actual_limit, actual_offset))

            memories = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )
                    memories.append(memory)
                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue

            logger.info(f"Found {len(memories)} of {total_count} memories containing '{search_text}' (limit={actual_limit}, offset={actual_offset})")
            return memories, total_count

        except Exception as e:
            logger.error(f"Error in content search: {str(e)}")
            logger.error(traceback.format_exc())
            return [], 0
    
    async def update_content(self, hash: str, new_content: str) -> Tuple[bool, str]:
        """Update memory content while preserving metadata and regenerating embeddings."""
        try:
            if not self.conn:
                return False, "Database not initialized"

            # Get current memory to preserve metadata
            memory = await self.get_by_hash(hash)
            if not memory:
                return False, f"Memory with hash {hash} not found"

            # Generate new hash
            from ..utils.hashing import generate_content_hash
            new_hash = generate_content_hash(new_content)

            # Generate new embedding
            new_embedding = self._generate_embedding(new_content)

            # Update memory table
            cursor = self.conn.execute('''
                UPDATE memories
                SET content = ?, hash = ?, updated_at = ?, updated_at_iso = ?
                WHERE hash = ?
            ''', (
                new_content,
                new_hash,
                time.time(),
                datetime.now().isoformat() + 'Z',
                hash
            ))

            if cursor.rowcount == 0:
                return False, "Failed to update memory content"

            # Update embedding (get memory id first)
            cursor = self.conn.execute('SELECT id FROM memories WHERE hash = ?', (new_hash,))
            row = cursor.fetchone()

            if row:
                memory_id = row[0]
                # Update embedding table
                self.conn.execute('''
                    UPDATE memory_embeddings
                    SET content_embedding = ?
                    WHERE rowid = ?
                ''', (serialize_float32(new_embedding), memory_id))

            self.conn.commit()
            return True, f"Content updated successfully. New hash: {new_hash}"
            
        except Exception as e:
            logger.error(f"Error updating content: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"Error updating content: {str(e)}"
    
    async def get_all_memories(self) -> List[Memory]:
        """
        Get all memories from the database.
        
        Returns:
            List of all Memory objects in the database.
        """
        try:
            if not self.conn:
                logger.error("Database not initialized, cannot retrieve memories")
                return []
            
            cursor = self.conn.execute('''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                ORDER BY created_at DESC
            ''')

            results = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )
                    
                    results.append(memory)
                    
                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue
            
            logger.info(f"Retrieved {len(results)} total memories")
            return results
            
        except Exception as e:
            logger.error(f"Error getting all memories: {str(e)}")
            return []

    async def get_memories_by_time_range(self, start_time: float, end_time: float) -> List[Memory]:
        """Get memories within a specific time range."""
        try:
            await self.initialize()
            cursor = self.conn.execute('''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                WHERE created_at BETWEEN ? AND ?
                ORDER BY created_at DESC
            ''', (start_time, end_time))

            results = []
            for row in cursor.fetchall():
                try:
                    id_val, hash_val, content, tags_str, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]

                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}

                    memory = Memory(
                        id=id_val,
                        content=content,
                        hash=hash_val,
                        tags=tags,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )
                    
                    results.append(memory)
                    
                except Exception as parse_error:
                    logger.warning(f"Failed to parse memory result: {parse_error}")
                    continue
            
            logger.info(f"Retrieved {len(results)} memories in time range {start_time}-{end_time}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting memories by time range: {str(e)}")
            return []

    async def get_memory_connections(self) -> Dict[str, int]:
        """Get memory connection statistics."""
        try:
            await self.initialize()
            # For now, return basic statistics based on tags and content similarity
            cursor = self.conn.execute('''
                SELECT tags, COUNT(*) as count
                FROM memories
                WHERE tags IS NOT NULL AND tags != ''
                GROUP BY tags
            ''')
            
            connections = {}
            for row in cursor.fetchall():
                tags_str, count = row
                if tags_str:
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]
                    for tag in tags:
                        connections[f"tag:{tag}"] = connections.get(f"tag:{tag}", 0) + count
            
            return connections
            
        except Exception as e:
            logger.error(f"Error getting memory connections: {str(e)}")
            return {}

    async def get_access_patterns(self) -> Dict[str, datetime]:
        """Get memory access pattern statistics."""
        try:
            await self.initialize()
            # Return recent access patterns based on updated_at timestamps
            cursor = self.conn.execute('''
                SELECT hash, updated_at_iso
                FROM memories
                WHERE updated_at_iso IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 100
            ''')

            patterns = {}
            for row in cursor.fetchall():
                hash, updated_at_iso = row
                try:
                    patterns[hash] = datetime.fromisoformat(updated_at_iso.replace('Z', '+00:00'))
                except Exception:
                    # Fallback for timestamp parsing issues
                    patterns[hash] = datetime.now()
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error getting access patterns: {str(e)}")
            return {}

    def _row_to_memory(self, row) -> Optional[Memory]:
        """Convert database row to Memory object."""
        try:
            id_val, hash_val, content, tags_str, metadata_str, created_at, updated_at, created_at_iso, updated_at_iso = row

            # Parse tags (stored as comma-separated string)
            tags = []
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]

            # Parse metadata
            metadata = {}
            if metadata_str:
                try:
                    metadata = json.loads(metadata_str)
                    if not isinstance(metadata, dict):
                        metadata = {}
                except json.JSONDecodeError:
                    metadata = {}

            return Memory(
                id=id_val,
                content=content,
                hash=hash_val,
                tags=tags,
                metadata=metadata,
                created_at=created_at,
                updated_at=updated_at,
                created_at_iso=created_at_iso,
                updated_at_iso=updated_at_iso
            )
            
        except Exception as e:
            logger.error(f"Error converting row to memory: {str(e)}")
            return None

    async def get_all_memories(self, limit: int = None, offset: int = 0) -> List[Memory]:
        """
        Get all memories in storage ordered by creation time (newest first).
        
        Args:
            limit: Maximum number of memories to return (None for all)
            offset: Number of memories to skip (for pagination)
            
        Returns:
            List of Memory objects ordered by created_at DESC
        """
        try:
            await self.initialize()
            
            # Build query with optional limit and offset
            query = '''
                SELECT id, hash, content, tags, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories
                ORDER BY created_at DESC
            '''
            
            params = []
            if limit is not None:
                query += ' LIMIT ?'
                params.append(limit)
                
            if offset > 0:
                query += ' OFFSET ?'
                params.append(offset)
            
            cursor = self.conn.execute(query, params)
            memories = []
            
            for row in cursor.fetchall():
                memory = self._row_to_memory(row)
                if memory:
                    memories.append(memory)
            
            return memories
            
        except Exception as e:
            logger.error(f"Error getting all memories: {str(e)}")
            return []

    async def get_recent_memories(self, n: int = 10) -> List[Memory]:
        """
        Get n most recent memories.
        
        Args:
            n: Number of recent memories to return
            
        Returns:
            List of the n most recent Memory objects
        """
        return await self.get_all_memories(limit=n, offset=0)

    async def count_all_memories(self) -> int:
        """
        Get total count of memories in storage.
        
        Returns:
            Total number of memories
        """
        try:
            await self.initialize()
            
            cursor = self.conn.execute('SELECT COUNT(*) FROM memories')
            result = cursor.fetchone()
            return result[0] if result else 0
            
        except Exception as e:
            logger.error(f"Error counting memories: {str(e)}")
            return 0

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("SQLite-vec storage connection closed")