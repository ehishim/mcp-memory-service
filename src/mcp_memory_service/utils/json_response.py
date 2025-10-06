"""
JSON Response Formatting Utilities for MCP Memory Service

Provides standardized JSON response formatting for MCP tool handlers,
ensuring consistent structure across all API responses.
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime
import mcp.types as types

from ..models.memory import Memory, MemoryQueryResult


def create_json_response(data: Dict[str, Any]) -> List[types.TextContent]:
    """
    Convert dict to JSON string wrapped in TextContent.

    Args:
        data: Dictionary to serialize (must be JSON-serializable)

    Returns:
        List containing single TextContent with JSON string
    """
    return [types.TextContent(
        type="text",
        text=json.dumps(data, indent=2, ensure_ascii=False)
    )]


def create_success_response(data: Dict[str, Any]) -> List[types.TextContent]:
    """
    Create standard success response with consistent format.

    Args:
        data: Response data to include

    Returns:
        JSON response with success=True
    """
    return create_json_response({
        "success": True,
        **data
    })


def create_error_response(error: str) -> List[types.TextContent]:
    """
    Create standard error response with consistent format.

    Args:
        error: Error message description

    Returns:
        JSON response with success=False
    """
    return create_json_response({
        "success": False,
        "data": None,
        "error": error
    })


def memory_to_dict(memory: Memory, include_relevance: Optional[float] = None) -> Dict[str, Any]:
    """
    Convert Memory object to JSON-serializable dictionary.

    Args:
        memory: Memory object to convert
        include_relevance: Optional relevance score to include

    Returns:
        Dictionary representation of memory
    """
    mem_dict = {
        "hash": memory.content_hash,
        "content": memory.content,
        "tags": memory.tags or [],
        "metadata": memory.metadata or {},
        "created_at": memory.created_at_iso if hasattr(memory, 'created_at_iso') else None,
        "updated_at": memory.updated_at_iso if hasattr(memory, 'updated_at_iso') else None
    }

    if include_relevance is not None:
        mem_dict["relevance_score"] = include_relevance

    return mem_dict


def query_result_to_dict(result: MemoryQueryResult) -> Dict[str, Any]:
    """
    Convert MemoryQueryResult to JSON-serializable dictionary.

    Args:
        result: MemoryQueryResult object

    Returns:
        Dictionary representation with memory and relevance score
    """
    return memory_to_dict(
        result.memory,
        include_relevance=result.relevance_score if hasattr(result, 'relevance_score') else None
    )


def create_paginated_response(
    memories: List[Any],
    total_count: int,
    limit: int,
    offset: int,
    is_query_result: bool = False
) -> List[types.TextContent]:
    """
    Create paginated response with memories and pagination metadata.

    Args:
        memories: List of Memory or MemoryQueryResult objects
        total_count: Total number of results available
        limit: Maximum results returned
        offset: Number of results skipped
        is_query_result: True if memories are MemoryQueryResult objects

    Returns:
        JSON response with memories and pagination metadata
    """
    # Convert memories to dicts
    if is_query_result:
        memories_data = [query_result_to_dict(m) for m in memories]
    else:
        memories_data = [memory_to_dict(m) for m in memories]

    # Calculate pagination metadata
    has_more = (offset + len(memories)) < total_count
    next_offset = offset + len(memories) if has_more else None

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


def create_single_memory_response(memory: Memory) -> List[types.TextContent]:
    """
    Create response for single memory retrieval.

    Args:
        memory: Memory object to return

    Returns:
        JSON response with single memory
    """
    return create_success_response({
        "memory": memory_to_dict(memory)
    })


def create_operation_response(
    affected_count: int,
    message: str
) -> List[types.TextContent]:
    """
    Create response for operations (update, delete).

    Args:
        affected_count: Number of items affected
        message: Human-readable message

    Returns:
        JSON response with operation results
    """
    return create_success_response({
        "affected_count": affected_count,
        "message": message
    })


def create_health_response(health_data: Dict[str, Any]) -> List[types.TextContent]:
    """
    Create response for health check.

    Args:
        health_data: Dictionary containing health metrics

    Returns:
        JSON response with health data
    """
    return create_success_response({
        "health": health_data
    })


def create_backup_response(backup_info: Dict[str, Any]) -> List[types.TextContent]:
    """
    Create response for backup operation.

    Args:
        backup_info: Dictionary containing backup details

    Returns:
        JSON response with backup information
    """
    return create_success_response({
        "backup": backup_info
    })
