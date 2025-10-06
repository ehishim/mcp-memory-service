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

import hashlib
import json
from typing import Any, Dict, List, Optional

def generate_content_hash(
    content: str,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """
    Generate deterministic hash including content, tags, and metadata.

    Ensures consistent hashing regardless of input order:
    1. Normalizes content (strip whitespace, lowercase)
    2. Sorts tags alphabetically (deduplicated)
    3. Sorts metadata keys alphabetically
    4. Filters out dynamic fields (timestamps)
    5. Uses consistent JSON serialization

    Args:
        content: The memory content
        tags: Optional list of tags
        metadata: Optional metadata dictionary

    Returns:
        SHA-256 hash as hexadecimal string
    """
    # Normalize content
    normalized_content = content.strip().lower()

    # Sort and deduplicate tags
    sorted_tags = sorted(list(set(tags))) if tags else []

    # Filter and sort metadata
    static_metadata = {}
    if metadata:
        static_metadata = {
            k: v for k, v in sorted(metadata.items())
            if k not in ['created_at', 'updated_at', 'created_at_iso', 'updated_at_iso',
                        'timestamp', 'content_hash', 'embedding']
        }

    # Build hash input with all components (order-independent)
    hash_input = {
        "content": normalized_content,
        "tags": sorted_tags,
        "metadata": static_metadata
    }

    # Generate hash with consistent serialization
    hash_str = json.dumps(hash_input, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(hash_str.encode('utf-8')).hexdigest()