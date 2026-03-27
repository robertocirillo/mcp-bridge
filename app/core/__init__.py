"""
Core Package - Logica di business principale
"""

from .runtime.mcp_wrapper import MCPWrapper
from .sessions.manager import SessionManager
from .exceptions import *

__all__ = ["MCPWrapper", "SessionManager"]
