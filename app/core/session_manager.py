"""
Session Manager per gestire le sessioni MCP attive
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid

from app.core.mcp_wrapper import MCPWrapper
from app.core.exceptions import SessionNotFoundError, MaxSessionsExceededError
from app.models.config import SessionConfig
from config import settings

logger = logging.getLogger(__name__)

class SessionData:
    """Dati di una sessione attiva"""
    
    def __init__(self, session_id: str, config: SessionConfig, wrapper: MCPWrapper):
        self.session_id = session_id
        self.config = config
        self.wrapper = wrapper
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.status = "active"
        self.query_count = 0
    
    def update_last_used(self):
        """Aggiorna il timestamp dell'ultimo utilizzo"""
        self.last_used = datetime.now()
    
    def is_expired(self) -> bool:
        """Controlla se la sessione è scaduta"""
        return (datetime.now() - self.last_used).total_seconds() > settings.SESSION_TIMEOUT

class SessionManager:
    """Gestore centrale delle sessioni MCP"""
    
    def __init__(self):
        self._sessions: Dict[str, SessionData] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """Inizializza il session manager"""
        logger.info("Inizializzazione Session Manager")
        # Avvia il task di cleanup automatico
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())
    
    async def create_session(self, config: SessionConfig) -> str:
        """
        Crea una nuova sessione
        
        Args:
            config: Configurazione della sessione
            
        Returns:
            ID della sessione creata
            
        Raises:
            MaxSessionsExceededError: Se raggiunto il limite massimo di sessioni
        """
        async with self._lock:
            # Controlla il limite di sessioni
            if len(self._sessions) >= settings.MAX_ACTIVE_SESSIONS:
                raise MaxSessionsExceededError(f"Raggiunto il limite massimo di {settings.MAX_ACTIVE_SESSIONS} sessioni")
            
            session_id = str(uuid.uuid4())
            
            try:
                # Crea il wrapper MCP
                wrapper = MCPWrapper(
                    llm_provider=config.llm_provider.provider,
                    model=config.llm_provider.model,
                    api_key=config.llm_provider.api_key,
                    base_url=config.llm_provider.base_url,
                    temperature=config.llm_provider.temperature or 0.7,
                    max_tokens=config.llm_provider.max_tokens,
                    mcp_servers=self._convert_mcp_servers(config.mcp_servers),
                    max_steps=config.max_steps,
                    verbose=config.verbose,
                    use_sandbox=config.sandbox,
                    sandbox_options=config.sandbox_options,
                    disallowed_tools=config.disallowed_tools,
                    use_server_manager=config.use_server_manager
                )
                
                # Inizializza il wrapper
                await wrapper.initialize()
                
                # Crea i dati della sessione
                session_data = SessionData(session_id, config, wrapper)
                
                # Salva la sessione
                self._sessions[session_id] = session_data
                
                logger.info(f"Sessione {session_id} creata con successo")
                return session_id
                
            except Exception as e:
                logger.error(f"Errore nella creazione della sessione: {e}")
                raise
    
    async def get_session(self, session_id: str) -> SessionData:
        """
        Recupera una sessione
        
        Args:
            session_id: ID della sessione
            
        Returns:
            Dati della sessione
            
        Raises:
            SessionNotFoundError: Se la sessione non esiste
        """
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Sessione {session_id} non trovata")
        
        session_data = self._sessions[session_id]
        session_data.update_last_used()
        
        return session_data
    
    async def delete_session(self, session_id: str):
        """
        Elimina una sessione
        
        Args:
            session_id: ID della sessione da eliminare
            
        Raises:
            SessionNotFoundError: Se la sessione non esiste
        """
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Sessione {session_id} non trovata")
        
        async with self._lock:
            session_data = self._sessions[session_id]
            
            # Chiudi il wrapper
            try:
                await session_data.wrapper.close()
            except Exception as e:
                logger.warning(f"Errore nella chiusura del wrapper per sessione {session_id}: {e}")
            
            # Rimuovi la sessione
            del self._sessions[session_id]
            logger.info(f"Sessione {session_id} eliminata")
    
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """
        Lista tutte le sessioni attive
        
        Returns:
            Lista delle informazioni delle sessioni
        """
        sessions = []
        for session_data in self._sessions.values():
            sessions.append({
                "session_id": session_data.session_id,
                "status": session_data.status,
                "created_at": session_data.created_at,
                "last_used": session_data.last_used,
                "query_count": session_data.query_count,
                "servers": list(session_data.config.mcp_servers.keys()),
                "llm_provider": session_data.config.llm_provider.provider,
                "llm_model": session_data.config.llm_provider.model
            })
        return sessions
    
    async def get_session_count(self) -> int:
        """Restituisce il numero di sessioni attive"""
        return len(self._sessions)
    
    async def cleanup_all(self):
        """Cleanup di tutte le sessioni"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                await self.delete_session(session_id)
            except Exception as e:
                logger.error(f"Errore nel cleanup della sessione {session_id}: {e}")
        
        logger.info("Cleanup completato di tutte le sessioni")
    
    async def _cleanup_expired_sessions(self):
        """Task di cleanup automatico delle sessioni scadute"""
        while True:
            try:
                expired_sessions = []
                
                # Identifica sessioni scadute
                for session_id, session_data in self._sessions.items():
                    if session_data.is_expired():
                        expired_sessions.append(session_id)
                
                # Elimina sessioni scadute
                for session_id in expired_sessions:
                    try:
                        await self.delete_session(session_id)
                        logger.info(f"Sessione scaduta {session_id} eliminata automaticamente")
                    except Exception as e:
                        logger.error(f"Errore nel cleanup automatico della sessione {session_id}: {e}")
                
                # Attendi prima del prossimo controllo
                await asyncio.sleep(300)  # 5 minuti
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Errore nel task di cleanup: {e}")
                await asyncio.sleep(60)  # Riprova tra 1 minuto
    
    @staticmethod
    def _convert_mcp_servers(servers) -> Dict[str, Dict[str, Any]]:
        """Converte la configurazione server dal formato API al formato wrapper"""
        mcp_servers = {}
        
        for name, config in servers.items():
            server_config = {}
            
            if config.url:
                server_config["url"] = config.url
            else:
                if config.command:
                    server_config["command"] = config.command
                if config.args:
                    server_config["args"] = config.args
                if config.env:
                    server_config["env"] = config.env
            
            mcp_servers[name] = server_config
        
        return mcp_servers