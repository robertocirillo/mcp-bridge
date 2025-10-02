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
