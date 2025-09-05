"""
Modelli Pydantic per le risposte HTTP
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class SessionResponse(BaseModel):
    """Risposta per la creazione di una sessione"""
    session_id: str
    status: str = Field(..., description="Stato della sessione")
    message: str = Field(..., description="Messaggio informativo")
    servers: List[str] = Field(..., description="Lista dei server MCP configurati")

class QueryResponse(BaseModel):
    """Risposta per l'esecuzione di una query"""
    session_id: str
    result: str = Field(..., description="Risultato dell'esecuzione")
    execution_time: float = Field(..., description="Tempo di esecuzione in secondi")
    steps_used: int = Field(..., description="Numero di passi utilizzati")
    timestamp: datetime = Field(..., description="Timestamp dell'esecuzione")
    server_used: Optional[str] = Field(None, description="Server utilizzato per l'esecuzione")

class SessionInfo(BaseModel):
    """Informazioni dettagliate su una sessione"""
    session_id: str
    status: str = Field(..., description="Stato della sessione")
    created_at: datetime = Field(..., description="Data/ora di creazione")
    last_used: datetime = Field(..., description="Data/ora ultimo utilizzo")
    query_count: int = Field(..., description="Numero di query eseguite")
    servers: List[str] = Field(..., description="Server MCP configurati")
    llm_provider: str = Field(..., description="Provider LLM utilizzato")
    llm_model: str = Field(..., description="Modello LLM utilizzato")

class HealthResponse(BaseModel):
    """Risposta per il health check"""
    status: str = Field(..., description="Stato del servizio")
    timestamp: datetime = Field(..., description="Timestamp del controllo")
    active_sessions: int = Field(..., description="Numero di sessioni attive")
    supported_providers: List[str] = Field(..., description="Provider LLM supportati")
    features: Dict[str, Any] = Field(..., description="Funzionalità disponibili")

class ErrorResponse(BaseModel):
    """Risposta per gli errori"""
    error: str = Field(..., description="Tipo di errore")
    message: str = Field(..., description="Messaggio di errore")
    details: Optional[Dict[str, Any]] = Field(None, description="Dettagli aggiuntivi")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp dell'errore")

class SessionStatsResponse(BaseModel):
    """Statistiche delle sessioni"""
    total_sessions: int = Field(..., description="Totale sessioni create")
    active_sessions: int = Field(..., description="Sessioni attive")
    total_queries: int = Field(..., description="Totale query eseguite")
    avg_execution_time: float = Field(..., description="Tempo medio di esecuzione")
    providers_usage: Dict[str, int] = Field(..., description="Utilizzo per provider")