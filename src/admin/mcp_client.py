"""
MCP HTTP/SSE Client for Admin UI
Communicates with MCP server via HTTP/JSON-RPC
"""

import aiohttp
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from mcp_memory_service.models.memory import Memory

logger = logging.getLogger(__name__)


@dataclass
class MemoryWithScore(Memory):
    """Extended Memory class for admin UI that includes relevance score from search results."""
    relevance_score: Optional[float] = None


class MCPHttpClient:
    """HTTP client for MCP server communication via JSON-RPC"""

    def __init__(self, base_url: str, auth_token: Optional[str] = None, timeout: int = 30):
        """
        Initialize MCP client

        Args:
            base_url: MCP server URL (e.g., http://mevault:8030/mcp)
            auth_token: Optional authorization token for Bearer authentication
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.auth_token = auth_token
        self.timeout_seconds = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._message_id = 0

    async def _ensure_session(self):
        """Ensure aiohttp session is created"""
        # Always close existing session to avoid event loop conflicts
        if self._session and not self._session.closed:
            await self._session.close()

        headers = {
            'Content-Type': 'application/json',
            # MCP over HTTP requires accepting both JSON and SSE
            'Accept': 'application/json, text/event-stream'
        }

        # Add Authorization header if token provided
        if self.auth_token:
            headers['Authorization'] = f'Bearer {self.auth_token}'

        # Create fresh session for each operation to avoid event loop issues
        self._session = aiohttp.ClientSession(
            headers=headers
        )

    async def close(self):
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _next_id(self) -> int:
        """Generate next JSON-RPC message ID"""
        self._message_id += 1
        return self._message_id

    async def test_connection(self) -> tuple[bool, str]:
        """Test connection to MCP server"""
        try:
            tools = await self.list_tools()
            return True, f"Connected - {len(tools)} tools available"
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False, f"Connection failed: {str(e)}"

    def _parse_sse_response(self, sse_text: str) -> Dict[str, Any]:
        """Parse Server-Sent Events response to extract JSON data"""
        for line in sse_text.split('\n'):
            if line.startswith('data: '):
                json_str = line[6:]  # Remove 'data: ' prefix
                return json.loads(json_str)
        raise Exception("No data found in SSE response")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Get available MCP tools from server"""
        await self._ensure_session()

        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list"
        }

        try:
            async with self._session.post(self.base_url, json=request) as response:
                response.raise_for_status()

                # MCP over HTTP uses SSE format, parse it
                sse_text = await response.text()
                result = self._parse_sse_response(sse_text)

                if "error" in result:
                    raise Exception(f"MCP error: {result['error']}")

                return result.get("result", {}).get("tools", [])
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            raise
        finally:
            # Close session after each operation
            if self._session and not self._session.closed:
                await self._session.close()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute MCP tool and return result

        Args:
            name: Tool name (e.g., 'recall_memory')
            arguments: Tool arguments as dict

        Returns:
            Parsed JSON result dict

        Raises:
            Exception: If tool execution fails or returns error
        """
        await self._ensure_session()

        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }

        try:
            async with self._session.post(self.base_url, json=request) as response:
                response.raise_for_status()

                # MCP over HTTP uses SSE format, parse it
                sse_text = await response.text()
                result = self._parse_sse_response(sse_text)

                if "error" in result:
                    raise Exception(f"MCP tool error: {result['error']}")

                # Extract the actual tool result
                tool_result = result.get("result", {})

                # Parse content - expecting JSON string in TextContent
                if "content" in tool_result and isinstance(tool_result["content"], list):
                    for item in tool_result["content"]:
                        if item.get("type") == "text":
                            try:
                                # Parse JSON from text content
                                parsed = json.loads(item["text"])

                                # Check for error in response
                                if not parsed.get("success", True):
                                    error_msg = parsed.get("error", "Unknown error")
                                    raise Exception(f"Tool execution failed: {error_msg}")

                                return parsed

                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse JSON response: {item['text']}")
                                raise Exception(f"Invalid JSON response from tool {name}: {e}")

                # Fallback if no content found
                logger.warning(f"Unexpected result format from tool {name}")
                return tool_result

        except Exception as e:
            logger.error(f"Failed to call tool {name}: {e}")
            raise
        finally:
            # Close session after each operation
            if self._session and not self._session.closed:
                await self._session.close()

    # High-level tool wrappers for admin UI

    async def store_memory(
        self,
        content: str,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Store new memory"""
        args = {'content': content}
        if tags is not None:
            args['tags'] = tags
        if metadata is not None:
            args['metadata'] = metadata

        return await self.call_tool('store_memory', args)

    async def recall_memory(
        self,
        query: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> tuple[List[Memory], Dict[str, Any]]:
        """
        Semantic search with natural language time filtering

        Args:
            query: Search query
            limit: Maximum results for pagination (default: 100)
            offset: Skip N results (for pagination)

        Returns:
            (memories, pagination_metadata) tuple
        """
        args = {
            'query': query
        }

        if limit is not None:
            args['limit'] = limit
        if offset is not None:
            args['offset'] = offset

        result = await self.call_tool('recall_memory', args)
        memories = self._parse_memories(result)
        pagination = result.get('pagination', {})

        return memories, pagination

    async def search_by_tag(
        self,
        tags: List[str],
        match_all: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> tuple[List[Memory], Dict[str, Any]]:
        """
        Filter memories by tags with AND/OR logic

        Args:
            tags: List of tags to search for
            match_all: If True, match all tags (AND). If False, match any tag (OR)
            limit: Maximum results for pagination
            offset: Skip N results (for pagination)

        Returns:
            (memories, pagination_metadata) tuple
        """
        args = {
            'tags': tags,
            'match_all': match_all
        }

        if limit is not None:
            args['limit'] = limit
        if offset is not None:
            args['offset'] = offset

        result = await self.call_tool('search_by_tag', args)
        memories = self._parse_memories(result)
        pagination = result.get('pagination', {})

        return memories, pagination

    async def get_by_id(self, id: str) -> Optional[Memory]:
        """
        Retrieve specific memory by ID

        Returns:
            Memory object or None if not found
        """
        result = await self.call_tool('get_memory', {'id': id})
        memories = self._parse_memories(result)
        return memories[0] if memories else None

    async def search_by_content(
        self,
        search_text: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> tuple[List[Memory], Dict[str, Any]]:
        """
        Substring text search in memory content

        Args:
            search_text: Text to search for
            limit: Maximum results (default 10)
            offset: Skip N results (default 0)

        Returns:
            (memories, pagination_metadata) tuple
        """
        args = {
            'search_text': search_text
        }

        if limit is not None:
            args['limit'] = limit
        if offset is not None:
            args['offset'] = offset

        result = await self.call_tool('search_by_content', args)
        memories = self._parse_memories(result)
        pagination = result.get('pagination', {})

        return memories, pagination

    async def update_memory(
        self,
        id: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags_strategy: str = "replace",
        metadata_strategy: str = "replace",
        preserve_updated_at: bool = False
    ) -> Dict[str, Any]:
        """Update memory content, tags, and/or metadata with configurable strategies"""
        updates = {}
        if content is not None:
            updates['content'] = content
        if tags is not None:
            updates['tags'] = tags
        if metadata is not None:
            updates['metadata'] = metadata

        args = {
            'id': id,
            'updates': updates,
            'tags_strategy': tags_strategy,
            'metadata_strategy': metadata_strategy,
            'preserve_updated_at': preserve_updated_at
        }

        return await self.call_tool('update_memory', args)

    async def delete_memory(self, id: str | List[str]) -> Dict[str, Any]:
        """Delete memory by ID (single or array)"""
        return await self.call_tool('delete_memory', {'id': id})

    async def delete_by_tag(self, tags: List[str], match_all: bool = False) -> Dict[str, Any]:
        """Delete memories by tags"""
        return await self.call_tool('delete_by_tag', {
            'tags': tags,
            'match_all': match_all
        })

    async def check_memory_health(self) -> Dict[str, Any]:
        """Get system health and statistics"""
        return await self.call_tool('check_memory_health', {})

    async def backup_memory(self) -> Dict[str, Any]:
        """Create memory backup with WAL checkpoint"""
        return await self.call_tool('backup_memory', {})

    def _parse_memories(self, result: Dict[str, Any]) -> List[Memory]:
        """
        Parse MCP tool result into Memory objects

        Args:
            result: JSON response from tool (already parsed)

        Returns:
            List of Memory objects
        """
        memories = []

        # Check success status
        if not result.get("success", False):
            logger.error(f"Tool call failed: {result.get('error')}")
            return []

        # Extract memories list from response
        if "memories" in result:
            memory_list = result["memories"]
        elif "memory" in result:
            # Single memory response (get_by_hash)
            memory_list = [result["memory"]]
        else:
            logger.warning(f"No memories found in result: {result}")
            return []

        # Parse each memory dict into Memory object
        for mem_data in memory_list:
            try:
                if isinstance(mem_data, dict):
                    # Extract relevance_score if present (from semantic search results)
                    relevance_score = mem_data.pop("relevance_score", None)

                    # Map API 'hash' field to internal 'hash' field
                    if "hash" in mem_data:
                        mem_data["hash"] = mem_data["hash"]

                    # Ensure tags is a list
                    if "tags" in mem_data and isinstance(mem_data["tags"], str):
                        mem_data["tags"] = [t.strip() for t in mem_data["tags"].split(",") if t.strip()]

                    # Create Memory object using from_dict
                    memory = Memory.from_dict(mem_data)

                    # If relevance_score exists, upgrade to MemoryWithScore
                    if relevance_score is not None:
                        memory = MemoryWithScore(
                            **memory.__dict__,
                            relevance_score=relevance_score
                        )

                    memories.append(memory)

            except Exception as e:
                logger.error(f"Failed to parse memory: {e}, data: {mem_data}")
                continue

        return memories
