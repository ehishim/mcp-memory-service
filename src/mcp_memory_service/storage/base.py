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
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from ..models.memory import Memory, MemoryQueryResult

class MemoryStorage(ABC):
    """Abstract base class for memory storage implementations."""
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend."""
        pass
    
    @abstractmethod
    async def store(self, memory: Memory) -> Tuple[bool, str]:
        """Store a memory. Returns (success, message)."""
        pass
    
    @abstractmethod
    async def retrieve(self, query: str, n_results: int = 5) -> List[MemoryQueryResult]:
        """Retrieve memories by semantic search."""
        pass
    
    @abstractmethod
    async def search_by_tag(self, tags: List[str]) -> List[Memory]:
        """Search memories by tags."""
        pass
    
    @abstractmethod
    async def get_by_id(self, id: str) -> Optional[Memory]:
        """Get a memory by its ID."""
        pass

    @abstractmethod
    async def delete(self, id: str) -> Tuple[bool, str]:
        """Delete a memory by its ID."""
        pass

    @abstractmethod
    async def delete_by_tag(self, tag: str) -> Tuple[int, str]:
        """Delete memories by tag. Returns (count_deleted, message)."""
        pass

    @abstractmethod
    async def cleanup_duplicates(self) -> Tuple[int, str]:
        """Remove duplicate memories. Returns (count_removed, message)."""
        pass

    @abstractmethod
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
        Update memory content, tags, and/or metadata. ID remains stable.

        Args:
            id: ID of the memory to update
            content: New content (optional - enables content editing)
            tags: Tags to apply (optional)
            metadata: Metadata to apply (optional)
            tags_strategy: "replace" (default) replaces all tags, "merge" adds to existing
            metadata_strategy: "replace" (default) replaces all metadata, "merge" preserves existing fields

        Returns:
            Tuple of (success, message)

        Note:
            - Content updates are now supported (hash will be recalculated)
            - Duplicate detection: rejects if new hash matches existing memory
            - Tags strategy controls whether to replace or merge with existing tags
            - Metadata strategy controls whether to replace or merge with existing metadata
            - updated_at timestamp is always refreshed
        """
        pass
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics. Override for specific implementations."""
        return {
            "total_memories": 0,
            "storage_backend": self.__class__.__name__,
            "status": "operational"
        }
    
    async def get_all_tags(self) -> List[str]:
        """Get all unique tags in the storage. Override for specific implementations."""
        return []
    
    async def get_recent_memories(self, n: int = 10) -> List[Memory]:
        """Get n most recent memories. Override for specific implementations."""
        return []
    
    async def recall_memory(self, query: str, n_results: int = 5) -> List[Memory]:
        """Recall memories based on natural language time expression. Override for specific implementations."""
        # Default implementation just uses regular search
        results = await self.retrieve(query, n_results)
        return [r.memory for r in results]
    
    async def search(self, query: str, n_results: int = 5) -> List[MemoryQueryResult]:
        """Search memories. Default implementation uses retrieve."""
        return await self.retrieve(query, n_results)
    
    async def get_all_memories(self) -> List[Memory]:
        """Get all memories in storage. Override for specific implementations."""
        return []
    
    async def get_memories_by_time_range(self, start_time: float, end_time: float) -> List[Memory]:
        """Get memories within a time range. Override for specific implementations."""
        return []
    
    async def get_memory_connections(self) -> Dict[str, int]:
        """Get memory connection statistics. Override for specific implementations."""
        return {}
    
    async def get_access_patterns(self) -> Dict[str, datetime]:
        """Get memory access pattern statistics. Override for specific implementations."""
        return {}