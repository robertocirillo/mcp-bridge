"""
Modelli Pydantic per le richieste HTTP
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any

from .config import LLMProvider, MCPServerConfig, SandboxOptions

class SessionCreateRequest(BaseModel):
    """Richiesta per creare una nuova sessione"""
    llm_provider: LLMProvider
    mcp_servers: Dict[str, MCPServerConfig] = Field(..., min_items=1)
    max_steps: int = Field(30, gt=0, le=100, description="Numero massimo di passi dell'agent")
    use_server_manager: bool = Field(False, description="Usa il server manager per selezione automatica")
    disallowed_tools: Optional[List[str]] = Field(None, description="Strumenti non consentiti")
    sandbox: bool = Field(False, description="Usa l'ambiente sandbox E2B")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Opzioni per il sandbox")
    verbose: bool = Field(False, description="Modalità verbose per debug")

class QueryRequest(BaseModel):
    """Richiesta per eseguire una query"""
    query: str = Field(..., min_length=1, description="Query da eseguire")
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Override del numero massimo di passi")
    server_name: Optional[str] = Field(None, description="Nome specifico del server da usare")

class SessionUpdateRequest(BaseModel):
    """Richiesta per aggiornare una sessione"""
    max_steps: Optional[int] = Field(None, gt=0, le=100, description="Nuovo numero massimo di passi")
    verbose: Optional[bool] = Field(None, description="Nuova modalità verbose")