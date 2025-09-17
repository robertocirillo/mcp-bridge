"""
MCP-Use REST API Service
Un servizio REST basato su FastAPI per interagire con la libreria mcp-agent
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
from contextlib import asynccontextmanager
import asyncio
import uuid
import logging
from datetime import datetime
from dotenv import load_dotenv


from app.old_mcp_wrapper import MCPWrapper

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carica variabili d'ambiente
load_dotenv()

# Store per le sessioni attive
active_sessions: Dict[str, Dict[str, Any]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestisce il ciclo di vita dell'applicazione"""
    logger.info("Avvio del servizio MCP-Use REST API")
    yield
    logger.info("Chiusura del servizio MCP-Use REST API")
    # Cleanup delle sessioni attive
    for session_id in list(active_sessions.keys()):
        await cleanup_session(session_id)

app = FastAPI(
    title="MCP-Use REST API",
    description="Servizio REST per interagire con MCP servers tramite mcp-use",
    version="1.0.0",
    lifespan=lifespan
)

# Configurazione CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === MODELLI PYDANTIC ===

class LLMProvider(BaseModel):
    """Configurazione del provider LLM"""
    provider: str = Field(..., description="Provider del modello (openai, anthropic, ollama)")
    model: str = Field(..., description="Nome del modello")
    api_key: Optional[str] = Field(None, description="API key (opzionale se in env)")
    base_url: Optional[str] = Field(None, description="Base URL per provider custom (es. Ollama)")
    temperature: Optional[float] = Field(0.7, description="Temperatura del modello")
    max_tokens: Optional[int] = Field(None, description="Massimo numero di token")

class MCPServerConfig(BaseModel):
    """Configurazione di un MCP Server"""
    command: Optional[str] = Field(None, description="Comando per avviare il server")
    args: Optional[List[str]] = Field(None, description="Argomenti del comando")
    env: Optional[Dict[str, str]] = Field(None, description="Variabili d'ambiente")
    url: Optional[str] = Field(None, description="URL per connessioni HTTP")

class SessionConfig(BaseModel):
    """Configurazione per creare una nuova sessione"""
    llm_provider: LLMProvider
    mcp_servers: Dict[str, MCPServerConfig]
    max_steps: int = Field(30, description="Numero massimo di passi dell'agent")
    use_server_manager: bool = Field(False, description="Usa il server manager per selezione automatica")
    disallowed_tools: Optional[List[str]] = Field(None, description="Strumenti non consentiti")
    sandbox: bool = Field(False, description="Usa l'ambiente sandbox E2B")
    sandbox_options: Optional[Dict[str, Any]] = Field(None, description="Opzioni per il sandbox")
    verbose: bool = Field(False, description="Modalità verbose per debug")

class QueryRequest(BaseModel):
    """Richiesta per eseguire una query"""
    query: str = Field(..., description="Query da eseguire")
    max_steps: Optional[int] = Field(None, description="Override del numero massimo di passi")
    server_name: Optional[str] = Field(None, description="Nome specifico del server da usare")

class SessionResponse(BaseModel):
    """Risposta per la creazione di una sessione"""
    session_id: str
    status: str
    message: str
    servers: List[str]

class QueryResponse(BaseModel):
    """Risposta per l'esecuzione di una query"""
    session_id: str
    result: str
    execution_time: float
    steps_used: int
    timestamp: datetime

class SessionInfo(BaseModel):
    """Informazioni su una sessione"""
    session_id: str
    status: str
    created_at: datetime
    servers: List[str]
    llm_provider: str
    llm_model: str

# === FUNZIONI HELPER ===

def convert_mcp_servers(servers: Dict[str, MCPServerConfig]) -> Dict[str, Dict[str, Any]]:
    """Converte la configurazione server dal formato API al formato wrapper"""
    mcp_servers = {}
    
    for name, config in servers.items():
        server_config = {}
        
        if config.url:
            # Configurazione HTTP
            server_config["url"] = config.url
        else:
            # Configurazione comando
            if config.command:
                server_config["command"] = config.command
            if config.args:
                server_config["args"] = config.args
            if config.env:
                server_config["env"] = config.env
        
        mcp_servers[name] = server_config
    
    return mcp_servers

async def cleanup_session(session_id: str):
    """Pulisce una sessione e rilascia le risorse"""
    if session_id in active_sessions:
        session_data = active_sessions[session_id]
        if "wrapper" in session_data and session_data["wrapper"]:
            try:
                await session_data["wrapper"].close()
            except Exception as e:
                logger.warning(f"Errore nella chiusura del wrapper per sessione {session_id}: {e}")
        
        del active_sessions[session_id]
        logger.info(f"Sessione {session_id} pulita")

# === ENDPOINTS ===

@app.get("/")
async def root():
    """Endpoint di health check"""
    return {
        "service": "MCP-Use REST API",
        "version": "1.0.0",
        "status": "online",
        "active_sessions": len(active_sessions)
    }

@app.post("/sessions", response_model=SessionResponse)
async def create_session(config: SessionConfig):
    """Crea una nuova sessione MCP-Use"""
    session_id = str(uuid.uuid4())
    
    try:
        # Converte i server MCP
        mcp_servers = convert_mcp_servers(config.mcp_servers)
        
        # Crea il wrapper
        wrapper = MCPWrapper(
            llm_provider=config.llm_provider.provider,
            model=config.llm_provider.model,
            api_key=config.llm_provider.api_key,
            base_url=config.llm_provider.base_url,
            temperature=config.llm_provider.temperature or 0.7,
            max_tokens=config.llm_provider.max_tokens,
            mcp_servers=mcp_servers,
            max_steps=config.max_steps,
            verbose=config.verbose,
            use_sandbox=config.sandbox,
            sandbox_options=config.sandbox_options,
            disallowed_tools=config.disallowed_tools,
            use_server_manager=config.use_server_manager
        )
        
        # Inizializza il wrapper
        await wrapper.initialize()
        
        # Salva la sessione
        active_sessions[session_id] = {
            "wrapper": wrapper,
            "config": config,
            "created_at": datetime.now(),
            "status": "active"
        }
        
        server_names = list(config.mcp_servers.keys())
        
        logger.info(f"Sessione {session_id} creata con successo")
        
        return SessionResponse(
            session_id=session_id,
            status="created",
            message="Sessione creata con successo",
            servers=server_names
        )
        
    except Exception as e:
        logger.error(f"Errore nella creazione della sessione: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sessions/{session_id}/query", response_model=QueryResponse)
async def execute_query(session_id: str, request: QueryRequest):
    """Esegue una query su una sessione esistente"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Sessione non trovata")
    
    session_data = active_sessions[session_id]
    wrapper = session_data["wrapper"]
    
    try:
        start_time = asyncio.get_event_loop().time()
        
        # Esegue la query usando il wrapper
        result = await wrapper.run_query(
            query=request.query,
            max_steps=request.max_steps,
            server_name=request.server_name
        )
        
        end_time = asyncio.get_event_loop().time()
        execution_time = end_time - start_time
        
        # Ottiene i passi utilizzati
        steps_used = wrapper.steps_used
        
        return QueryResponse(
            session_id=session_id,
            result=result,
            execution_time=execution_time,
            steps_used=steps_used,
            timestamp=datetime.now()
        )
        
    except Exception as e:
        logger.error(f"Errore nell'esecuzione della query per sessione {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions", response_model=List[SessionInfo])
async def list_sessions():
    """Lista tutte le sessioni attive"""
    sessions = []
    for session_id, data in active_sessions.items():
        config = data["config"]
        
        sessions.append(SessionInfo(
            session_id=session_id,
            status=data["status"],
            created_at=data["created_at"],
            servers=list(config.mcp_servers.keys()),
            llm_provider=config.llm_provider.provider,
            llm_model=config.llm_provider.model
        ))
    return sessions

@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session_info(session_id: str):
    """Ottiene informazioni su una sessione specifica"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Sessione non trovata")
    
    data = active_sessions[session_id]
    config = data["config"]
    
    return SessionInfo(
        session_id=session_id,
        status=data["status"],
        created_at=data["created_at"],
        servers=list(config.mcp_servers.keys()),
        llm_provider=config.llm_provider.provider,
        llm_model=config.llm_provider.model
    )

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, background_tasks: BackgroundTasks):
    """Elimina una sessione"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Sessione non trovata")
    
    # Aggiunge il cleanup alle task di background
    background_tasks.add_task(cleanup_session, session_id)
    
    return {"message": f"Sessione {session_id} eliminata"}

@app.get("/health")
async def health_check():
    """Health check dettagliato"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "active_sessions": len(active_sessions),
        "supported_providers": ["openai", "anthropic", "ollama"],
        "features": {
            "sandbox_support": True,
            "multi_server_support": True,
            "streaming": False,  # Implementazione futura
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)