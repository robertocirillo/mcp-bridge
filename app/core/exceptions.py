"""
Custom exceptions for MCP-Use REST API
"""

class MCPAPIException(Exception):
    """Base exception for MCP API"""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class SessionNotFoundError(MCPAPIException):
    """Exception for session not found"""

    def __init__(self, message: str = "Session not found"):
        super().__init__(message, status_code=404)

class MaxSessionsExceededError(MCPAPIException):
    """Exception for maximum sessions limit reached"""

    def __init__(self, message: str = "Maximum sessions limit reached"):
        super().__init__(message, status_code=429)

class MCPWrapperError(MCPAPIException):
    """Exception for MCP wrapper errors"""

    def __init__(self, message: str = "Error in MCP wrapper"):
        super().__init__(message, status_code=500)


class MCPCapabilityError(MCPWrapperError):
    """Base exception for prompt/resource capability operations."""

    def __init__(
        self,
        capability: str,
        message: str,
        *,
        server_name: str | None = None,
        status_code: int = 500,
    ):
        self.capability = capability
        self.server_name = server_name
        super().__init__(message)
        self.status_code = status_code


class MCPCapabilityNotSupportedError(MCPCapabilityError):
    """Raised when the runtime/server does not expose a requested capability."""

    def __init__(
        self,
        capability: str,
        message: str,
        *,
        server_name: str | None = None,
    ):
        super().__init__(
            capability,
            message,
            server_name=server_name,
            status_code=501,
        )


class MCPCapabilityUpstreamError(MCPCapabilityError):
    """Raised when a capability exists but fails in the runtime/server."""

    def __init__(
        self,
        capability: str,
        message: str,
        *,
        server_name: str | None = None,
    ):
        super().__init__(
            capability,
            message,
            server_name=server_name,
            status_code=502,
        )

class QueryExecutionError(MCPAPIException):
    """Exception for query execution errors"""

    def __init__(self, message: str = "Error executing query"):
        super().__init__(message, status_code=500)

class ConfigurationError(MCPAPIException):
    """Exception for configuration errors"""

    def __init__(self, message: str = "Configuration error"):
        super().__init__(message, status_code=400)

class DependencyError(MCPAPIException):
    """Exception for missing dependencies"""

    def __init__(self, message: str = "Missing dependency"):
        super().__init__(message, status_code=500)
