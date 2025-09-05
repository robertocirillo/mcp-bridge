"""
Modelli Pydantic per le configurazioni
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any

class LLMProvider(BaseModel):
    """Configurazione del provider LLM"""
    provider: str = Field(..., description="Provider del modello (openai, anthropic, ollama)")
    model: str = Field(..., description="Nome del modello")
    api_key: Optional[str] = Field(None, description="API key (opzionale se in env)")
    base_url: Optional[str] = Field(None, description="Base URL per provider custom (es. Ollama)")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0, description="Temperatura del modello")
    max_tokens: Optional[int] = Field(None, gt=0, description="Massimo numero di token")

class MCPServerConfig(BaseModel):
    """Configurazione di un MCP Server"""
    command: Optional[str] = Field(None, description="Comando per avviare il server")
    args: Optional[List[str]] = Field(None, description="Argomenti del comando")
    env: Optional[Dict[str, str]] = Field(None, description="Variabili d'ambiente")
    url: Optional[str] = Field(None, description="URL per connessioni HTTP")

    def model_post_init(self, __context):
        """Validazione post-inizializzazione"""
        if not self.command and not self.url:
            raise ValueError("Deve essere specificato almeno uno tra 'command' o 'url'")
        if self.command and self.url:
            raise ValueError("Non è possibile specificare sia 'command' che 'url'")

class SandboxOptions(BaseModel):
    """Opzioni per il sandbox E2B"""
    api_key: Optional[str] = Field(None, description="API key E2B")
    sandbox_template_id: str = Field("base", description="ID template del sandbox")
    supergateway_command: str = Field("npx -y supergateway", description="Comando supergateway")
    timeout: int = Field(300, gt=0, description="Timeout in secondi")

class SessionConfig(BaseModel):
    """Configurazione per creare una nuova sessione"""
    llm_provider: LLMProvider
    mcp_servers: Dict[str, MCPServerConfig] = Field(..., min_items=1)
    max_steps: int = Field(30, gt=0, le=100, description="Numero massimo di passi dell'agent")
    use_server_manager: bool = Field(False, description="Usa il server manager per selezione automatica")
    disallowed_tools: Optional[List[str]] = Field(None, description="Strumenti non consentiti")
    sandbox: bool = Field(False, description="Usa l'ambiente sandbox E2B")
    sandbox_options: Optional[SandboxOptions] = Field(None, description="Opzioni per il sandbox")
    verbose: bool = Field(False, description="Modalità verbose per debug")

    def model_post_init(self, __context):
        """Validazione post-inizializzazione"""
        # Se sandbox è abilitato ma non ci sono opzioni, usa quelle di default
        if self.sandbox and not self.sandbox_options:
            self.sandbox_options = SandboxOptions()