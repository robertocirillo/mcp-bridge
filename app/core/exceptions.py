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


class QueryOperationNotFoundError(MCPAPIException):
    """Exception for query operation not found."""

    def __init__(self, message: str = "Query operation not found"):
        super().__init__(message, status_code=404)


class QueryOperationElicitationUnavailableError(MCPAPIException):
    """Raised when a query operation has no pending elicitation to resume."""

    def __init__(self, message: str = "No pending elicitation for this query operation"):
        super().__init__(message, status_code=409)


class QueryOperationResumeInvalidError(MCPAPIException):
    """Raised when a resume request is structurally invalid."""

    def __init__(self, message: str = "Invalid query operation resume request"):
        super().__init__(message, status_code=400)


class QueryOperationElicitationExpiredError(MCPAPIException):
    """Raised when the referenced elicitation is stale, cancelled, or already resolved."""

    def __init__(self, message: str = "Pending elicitation has expired or is no longer available"):
        super().__init__(message, status_code=409)


class QueryOperationElicitationDeclinedError(MCPAPIException):
    """Raised when the user explicitly declines a required elicitation."""

    def __init__(self, message: str = "Elicitation declined by user"):
        super().__init__(message, status_code=409)

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
