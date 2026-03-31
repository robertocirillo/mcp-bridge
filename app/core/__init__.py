"""
Core Package - Logica di business principale
"""

__all__ = ["MCPWrapper", "SessionManager"]


def __getattr__(name: str):
    if name == "MCPWrapper":
        from .runtime.mcp_wrapper import MCPWrapper

        return MCPWrapper
    if name == "SessionManager":
        from .sessions.manager import SessionManager

        return SessionManager
    raise AttributeError(f"module 'app.core' has no attribute {name!r}")
