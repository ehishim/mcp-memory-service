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

"""Debug utilities for memory service."""
from typing import Dict, Any, List
import json
import numpy as np
from ..models.memory import Memory, MemoryQueryResult

def _get_embedding_model(storage):
    """
    Get the embedding model from storage, handling different backend attribute names.
    
    ChromaDB uses 'model' while SQLite-Vec uses 'embedding_model'.
    """
    if hasattr(storage, 'model') and storage.model is not None:
        return storage.model
    elif hasattr(storage, 'embedding_model') and storage.embedding_model is not None:
        return storage.embedding_model
    else:
        raise AttributeError(f"Storage backend {type(storage).__name__} has no embedding model attribute")

def get_raw_embedding(storage, content: str) -> Dict[str, Any]:
    """Get raw embedding vector for content."""
    try:
        model = _get_embedding_model(storage)
        embedding = model.encode(content).tolist()
        return {
            "status": "success",
            "embedding": embedding,
            "dimension": len(embedding)
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

def check_embedding_model(storage) -> Dict[str, Any]:
    """Check if embedding model is loaded and working."""
    try:
        model = _get_embedding_model(storage)
        test_embedding = model.encode("test").tolist()
        
        # Try to get model name, handling different model types
        model_name = "unknown"
        if hasattr(model, '_model_card_vars'):
            model_name = model._model_card_vars.get('modelname', 'unknown')
        elif hasattr(storage, 'embedding_model_name'):
            model_name = storage.embedding_model_name
        
        return {
            "status": "healthy",
            "model_loaded": True,
            "model_name": model_name,
            "embedding_dimension": len(test_embedding)
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }

async def debug_retrieve_memory(
    storage,
    query: str,
    n_results: int = 5,
    similarity_threshold: float = 0.0
) -> List[MemoryQueryResult]:
    """Retrieve memories with debug information including raw similarity scores."""
    try:
        # Use storage's own retrieve method which is backend-agnostic
        results = await storage.retrieve(query, n_results)
        
        # Filter by similarity threshold and add debug info
        filtered_results = []
        for result in results:
            if result.relevance_score >= similarity_threshold:
                # Enhance debug info
                result.debug_info.update({
                    "similarity_threshold": similarity_threshold,
                    "backend": getattr(storage, '__class__', type(storage)).__name__
                })
                filtered_results.append(result)
        
        return filtered_results
    except Exception as e:
        return []

async def exact_match_retrieve(storage, content: str) -> List[Memory]:
    """Retrieve memories using exact content match."""
    try:
        # SQLite-Vec backend implementation
        if hasattr(storage, 'conn') and storage.conn:
            cursor = storage.conn.execute('''
                SELECT content_hash, content, tags, memory_type, metadata,
                       created_at, updated_at, created_at_iso, updated_at_iso
                FROM memories WHERE content = ?
            ''', (content,))
            
            memories = []
            for row in cursor.fetchall():
                try:
                    content_hash, db_content, tags_str, memory_type, metadata_str = row[:5]
                    created_at, updated_at, created_at_iso, updated_at_iso = row[5:]
                    
                    # Parse tags and metadata
                    tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()] if tags_str else []
                    metadata = json.loads(metadata_str) if metadata_str else {}
                    
                    memory = Memory(
                        content=db_content,
                        content_hash=content_hash,
                        tags=tags,
                        memory_type=memory_type,
                        metadata=metadata,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_at_iso=created_at_iso,
                        updated_at_iso=updated_at_iso
                    )
                    memories.append(memory)
                except Exception as parse_error:
                    continue
            
            return memories
        
        # ChromaDB backend fallback
        elif hasattr(storage, 'collection'):
            results = storage.collection.get(
                where={"content": content}
            )
            
            memories = []
            for i in range(len(results["ids"])):
                memory = Memory.from_dict(
                    {
                        "content": results["documents"][i],
                        **results["metadatas"][i]
                    },
                    embedding=results["embeddings"][i] if "embeddings" in results else None
                )
                memories.append(memory)
            
            return memories
        
        else:
            return []
            
    except Exception as e:
        return []