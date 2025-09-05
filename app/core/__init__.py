"""
Core Package - Logica di business principale
"""

from .mcp_wrapper import MCPWrapper
from .session_manager import SessionManager
from .exceptions import *

__all__ = ["MCPWrapper", "SessionManager"]
