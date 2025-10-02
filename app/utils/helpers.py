"""
Helper functions and various utilities
"""

import json
import asyncio
from typing import Any, Dict, Optional, Callable
from datetime import datetime, timezone
import hashlib

def generate_session_id(config: Dict[str, Any]) -> str:
    """
    Generate a session ID based on the configuration

    Args:
        config: Session configuration

    Returns:
        Generated session ID
    """
    # Create a hash of the configuration for a deterministic ID (optional)
    config_str = json.dumps(config, sort_keys=True, default=str)
    config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"session_{timestamp}_{config_hash}"

def safe_json_serialize(obj: Any) -> str:
    """
    Serialize an object to JSON handling non-serializable types

    Args:
        obj: Object to serialize

    Returns:
        JSON string
    """
    def json_serializer(o):
        if isinstance(o, datetime):
            return o.isoformat()
        elif hasattr(o, '__dict__'):
            return o.__dict__
        else:
            return str(o)

    return json.dumps(obj, default=json_serializer, indent=2)

def format_execution_time(seconds: float) -> str:
    """
    Format execution time in a human-readable way

    Args:
        seconds: Time in seconds

    Returns:
        Formatted time
    """
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.2f}s"

def validate_provider_config(provider: str, config: Dict[str, Any]) -> bool:
    """
    Validate an LLM provider configuration

    Args:
        provider: Provider name
        config: Provider configuration

    Returns:
        True if valid, False otherwise
    """
    required_fields = {
        "openai": ["model"],
        "anthropic": ["model"],
        "ollama": ["model"]
    }

    if provider not in required_fields:
        return False

    for field in required_fields[provider]:
        if field not in config or not config[field]:
            return False

    return True

async def retry_async(
    func: Callable,
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
) -> Any:
    """
    Retry an asynchronous function with exponential backoff

    Args:
        func: Function to retry
        max_retries: Maximum number of attempts
        delay: Initial delay
        backoff: Backoff factor
        exceptions: Exceptions to catch

    Returns:
        Function result

    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    current_delay = delay

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func()
            else:
                return func()
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                await asyncio.sleep(current_delay)
                current_delay *= backoff
            else:
                raise last_exception

def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename by removing invalid characters

    Args:
        filename: Filename to sanitize

    Returns:
        Sanitized filename
    """
    import re
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove multiple spaces and trim
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized

def get_memory_usage() -> Dict[str, float]:
    """
    Get memory usage information

    Returns:
        Dictionary with memory info
    """
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()

        return {
            "rss_mb": memory_info.rss / 1024 / 1024,  # Resident Set Size
            "vms_mb": memory_info.vms / 1024 / 1024,  # Virtual Memory Size
            "percent": process.memory_percent()
        }
    except ImportError:
        return {"error": "psutil not available"}
    except Exception as e:
        return {"error": str(e)}

def parse_duration(duration_str: str) -> Optional[int]:
    """
    Parse a duration string into seconds

    Args:
        duration_str: Duration string (e.g., "30s", "5m", "1h")

    Returns:
        Duration in seconds or None if invalid
    """
    import re

    pattern = r'^(\d+)([smh])$'
    match = re.match(pattern, duration_str.lower())

    if not match:
        return None

    value, unit = match.groups()
    value = int(value)

    multipliers = {
        's': 1,
        'm': 60,
        'h': 3600
    }

    return value * multipliers.get(unit, 1)
