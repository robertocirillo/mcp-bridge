"""
Eccezioni personalizzate per MCP-Use REST API
"""

class MCPAPIException(Exception):
    """Eccezione base per l'API MCP"""
    
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class SessionNotFoundError(MCPAPIException):
    """Eccezione per sessione non trovata"""
    
    def __init__(self, message: str = "Sessione non trovata"):
        super().__init__(message, status_code=404)

class MaxSessionsExceededError(MCPAPIException):
    """Eccezione per limite massimo sessioni raggiunto"""
    
    def __init__(self, message: str = "Limite massimo di sessioni raggiunto"):
        super().__init__(message, status_code=429)

class MCPWrapperError(MCPAPIException):
    """Eccezione per errori del wrapper MCP"""
    
    def __init__(self, message: str = "Errore nel wrapper MCP"):
        super().__init__(message, status_code=500)

class QueryExecutionError(MCPAPIException):
    """Eccezione per errori nell'esecuzione di query"""
    
    def __init__(self, message: str = "Errore nell'esecuzione della query"):
        super().__init__(message, status_code=500)

class ConfigurationError(MCPAPIException):
    """Eccezione per errori di configurazione"""
    
    def __init__(self, message: str = "Errore di configurazione"):
        super().__init__(message, status_code=400)

class DependencyError(MCPAPIException):
    """Eccezione per dipendenze mancanti"""
    
    def __init__(self, message: str = "Dipendenza mancante"):
        super().__init__(message, status_code=500)