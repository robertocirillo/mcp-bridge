"""
Wrapper raffinato per mcp-use con gestione errori migliorata
"""

import os
import logging
from typing import Optional, Dict, Any, List

from app.core.exceptions import MCPWrapperError, DependencyError, ConfigurationError
from app.utils.logging import get_logger
from app.utils.helpers import retry_async

logger = get_logger(__name__)

class MCPWrapper:
    """Wrapper migliorato per mcp-use che incapsula completamente la libreria"""

    def __init__(self,
                 llm_provider: str,
                 model: str,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 temperature: float = 0.7,
                 max_tokens: Optional[int] = None,
                 mcp_servers: Dict[str, Dict[str, Any]] = None,
                 max_steps: int = 30,
                 verbose: bool = False,
                 use_sandbox: bool = False,
                 sandbox_options: Optional[Dict[str, Any]] = None,
                 disallowed_tools: Optional[List[str]] = None,
                 use_server_manager: bool = False):
        """
        Inizializza il wrapper MCP

        Args:
            llm_provider: Provider del modello (openai, anthropic, ollama)
            model: Nome del modello
            api_key: API key (opzionale se in env)
            base_url: Base URL per provider custom
            temperature: Temperatura del modello
            max_tokens: Massimo numero di token
            mcp_servers: Configurazione dei server MCP
            max_steps: Numero massimo di passi per l'agent
            verbose: Modalità verbose per debug
            use_sandbox: Usa l'ambiente sandbox E2B
            sandbox_options: Opzioni per il sandbox
            disallowed_tools: Strumenti non consentiti
            use_server_manager: Usa il server manager per selezione automatica
        """
        self.llm_provider = llm_provider.lower()
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.mcp_servers = mcp_servers or {}
        self.max_steps = max_steps
        self.verbose = verbose
        self.use_sandbox = use_sandbox
        self.sandbox_options = sandbox_options or {}
        self.disallowed_tools = disallowed_tools
        self.use_server_manager = use_server_manager

        # Stato interno
        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None

        # Validazione iniziale
        self._validate_config()
        
        # Import delle dipendenze
        self._import_dependencies()

    def _validate_config(self):
        """Valida la configurazione iniziale"""
        if not self.llm_provider:
            raise ConfigurationError("Provider LLM non specificato")
        
        if not self.model:
            raise ConfigurationError("Modello non specificato")
        
        if not self.mcp_servers:
            raise ConfigurationError("Nessun server MCP configurato")
        
        # Valida i server MCP
        for name, config in self.mcp_servers.items():
            if not config.get("command") and not config.get("url"):
                raise ConfigurationError(f"Server {name}: deve avere 'command' o 'url'")

    def _import_dependencies(self):
        """Importa le dipendenze necessarie con gestione errori migliorata"""
        # Import mcp-use
        try:
            from mcp_use import MCPAgent, MCPClient
            from mcp_use.types.sandbox import SandboxOptions
            self.MCPAgent = MCPAgent
            self.MCPClient = MCPClient
            self.SandboxOptions = SandboxOptions
            logger.debug("mcp-use importato con successo")
        except ImportError as e:
            raise DependencyError(f"mcp-use non installato: {e}")

        # Import LangChain providers
        if self.llm_provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
                self.ChatLLM = ChatOpenAI
                logger.debug("langchain-openai importato con successo")
            except ImportError as e:
                raise DependencyError(f"langchain-openai non installato: {e}")

        elif self.llm_provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
                self.ChatLLM = ChatAnthropic
                logger.debug("langchain-anthropic importato con successo")
            except ImportError as e:
                raise DependencyError(f"langchain-anthropic non installato: {e}")

        elif self.llm_provider == "ollama":
            try:
                from langchain_ollama import ChatOllama
                self.ChatLLM = ChatOllama
                logger.debug("langchain-ollama importato con successo")
            except ImportError as e:
                raise DependencyError(f"langchain-ollama non installato: {e}")

        else:
            raise ConfigurationError(f"Provider non supportato: {self.llm_provider}")

    def _create_llm(self):
        """Crea l'istanza del modello LLM con gestione errori"""
        try:
            kwargs = {
                "model": self.model,
                "temperature": self.temperature,
            }

            if self.max_tokens:
                kwargs["max_tokens"] = self.max_tokens

            if self.llm_provider == "openai":
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                elif not os.getenv("OPENAI_API_KEY"):
                    raise ConfigurationError("API key OpenAI non trovata")
                
                if self.base_url:
                    kwargs["base_url"] = self.base_url

            elif self.llm_provider == "anthropic":
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                elif not os.getenv("ANTHROPIC_API_KEY"):
                    raise ConfigurationError("API key Anthropic non trovata")

            elif self.llm_provider == "ollama":
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                else:
                    kwargs["base_url"] = "http://localhost:11434"

            llm = self.ChatLLM(**kwargs)
            logger.debug(f"LLM {self.llm_provider}/{self.model} creato con successo")
            return llm
            
        except Exception as e:
            raise MCPWrapperError(f"Errore nella creazione del modello LLM: {e}")

    def _create_mcp_config(self) -> Dict[str, Any]:
        """Crea la configurazione per i server MCP"""
        return {"mcpServers": self.mcp_servers}

    async def initialize(self):
        """Inizializza l'agent e i client MCP con retry automatico"""
        if self._initialized:
            logger.debug("MCPWrapper già inizializzato")
            return

        try:
            # Usa retry per operazioni di rete
            await retry_async(self._initialize_internal, max_retries=3, delay=1.0)
            
            self._initialized = True
            logger.info("MCPWrapper inizializzato con successo")

        except Exception as e:
            logger.error(f"Errore nell'inizializzazione dopo tutti i tentativi: {e}")
            raise MCPWrapperError(f"Inizializzazione fallita: {e}")

    async def _initialize_internal(self):
        """Logica interna di inizializzazione"""
        # Crea il modello LLM
        llm = self._create_llm()

        # Crea la configurazione MCP
        mcp_config = self._create_mcp_config()

        # Configura il client MCP
        client_kwargs = {"config": mcp_config}

        if self.use_sandbox:
            client_kwargs["sandbox"] = True
            if self.sandbox_options:
                sandbox_options = {
                    "api_key": self.sandbox_options.get("api_key", os.getenv("E2B_API_KEY")),
                    "sandbox_template_id": self.sandbox_options.get("sandbox_template_id", "base"),
                    "supergateway_command": self.sandbox_options.get("supergateway_command", "npx -y supergateway")
                }
                client_kwargs["sandbox_options"] = sandbox_options

        self._client = self.MCPClient(**client_kwargs)

        # Crea l'agent
        agent_kwargs = {
            "llm": llm,
            "client": self._client,
            "max_steps": self.max_steps,
            "use_server_manager": self.use_server_manager,
            "verbose": self.verbose
        }

        if self.disallowed_tools:
            agent_kwargs["disallowed_tools"] = self.disallowed_tools

        self._agent = self.MCPAgent(**agent_kwargs)

    # async def run_query(self,
    #                     query: str,
    #                     max_steps: Optional[int] = None,
    #                     server_name: Optional[str] = None) -> str:
    #     """
    #     Esegue una query utilizzando l'agent MCP
    #
    #     Args:
    #         query: La query da elaborare
    #         max_steps: Override del numero massimo di passi (opzionale)
    #         server_name: Nome specifico del server da usare (opzionale)
    #
    #     Returns:
    #         La risposta dell'agent come stringa
    #     """
    #     if not self._initialized:
    #         await self.initialize()
    #
    #     if not query.strip():
    #         raise ValueError("Query vuota non consentita")
    #
    #     try:
    #         logger.debug(f"Esecuzione query: {query[:100]}...")
    #
    #         # Prepara i parametri
    #         run_kwargs = {"query": query}
    #
    #         if max_steps:
    #             run_kwargs["max_steps"] = max_steps
    #
    #         if server_name:
    #             if server_name not in self.mcp_servers:
    #                 raise ValueError(f"Server '{server_name}' non configurato")
    #             run_kwargs["server_name"] = server_name
    #             self._last_server_used = server_name
    #
    #         # Esegue la query con retry
    #         result = await retry_async(
    #             lambda: self._agent.run(**run_kwargs),
    #             max_retries=2,
    #             delay=0.5
    #         )
    #
    #         # Aggiorna le statistiche
    #         self._steps_used = getattr(self._agent, 'steps_used', 0)
    #
    #         if not self._last_server_used and hasattr(self._agent, 'last_server_used'):
    #             self._last_server_used = self._agent.last_server_used
    #
    #         logger.debug(f"Query completata in {self._steps_used} passi")
    #         return str(result)
    #
    #     except Exception as e:
    #         logger.error(f"Errore nell'esecuzione della query: {e}")
    #         raise MCPWrapperError(f"Esecuzione query fallita: {e}")

    async def run_query(
            self,
            query: str,
            max_steps: Optional[int] = None,
            server_name: Optional[str] = None
    ) -> str:
        """
        Esegue una query utilizzando l'agent MCP

        Args:
            query: La query da elaborare
            max_steps: Override del numero massimo di passi (opzionale)
            server_name: Nome specifico del server da usare (opzionale)

        Returns:
            La risposta dell'agent come stringa
        """
        if not self._initialized:
            await self.initialize()

        if not query.strip():
            raise ValueError("Query vuota non consentita")

        try:
            logger.debug(f"Esecuzione query: {query[:100]}...")

            # Prepara i parametri
            run_kwargs = {"query": query}

            if max_steps:
                run_kwargs["max_steps"] = max_steps

            if server_name:
                if server_name not in self.mcp_servers:
                    raise ValueError(f"Server '{server_name}' non configurato")
                run_kwargs["server_name"] = server_name
                self._last_server_used = server_name

            # Funzione async per retry
            async def run_agent():
                return await self._agent.run(**run_kwargs)

            # Esegue la query con retry
            result = await retry_async(
                run_agent,
                max_retries=2,
                delay=0.5
            )

            # Aggiorna le statistiche
            self._steps_used = getattr(self._agent, 'steps_used', 0)

            if not self._last_server_used and hasattr(self._agent, 'last_server_used'):
                self._last_server_used = self._agent.last_server_used

            logger.debug(f"Query completata in {self._steps_used} passi")
            return str(result)

        except Exception as e:
            logger.error(f"Errore nell'esecuzione della query: {e}")
            raise MCPWrapperError(f"Esecuzione query fallita: {e}")

    async def close(self):
        """Chiude le connessioni e rilascia le risorse"""
        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.debug("Client MCP chiuso correttamente")
            except Exception as e:
                logger.warning(f"Errore nella chiusura del client: {e}")

        self._agent = None
        self._client = None
        self._initialized = False
        logger.debug("MCPWrapper chiuso")

    @property
    def steps_used(self) -> int:
        """Restituisce il numero di passi utilizzati nell'ultima esecuzione"""
        return self._steps_used

    @property
    def last_server_used(self) -> Optional[str]:
        """Restituisce l'ultimo server utilizzato"""
        return self._last_server_used

    @property
    def is_initialized(self) -> bool:
        """Indica se il wrapper è stato inizializzato"""
        return self._initialized

    def get_config_summary(self) -> Dict[str, Any]:
        """Restituisce un riassunto della configurazione"""
        return {
            "llm_provider": self.llm_provider,
            "model": self.model,
            "max_steps": self.max_steps,
            "use_sandbox": self.use_sandbox,
            "servers": list(self.mcp_servers.keys()),
            "use_server_manager": self.use_server_manager,
            "initialized": self._initialized
        }

    async def test_connection(self) -> Dict[str, bool]:
        """Testa la connessione ai server MCP configurati"""
        if not self._initialized:
            await self.initialize()
        
        results = {}
        for server_name in self.mcp_servers.keys():
            try:
                # Test semplice con una query minima
                await self.run_query("ping", max_steps=1, server_name=server_name)
                results[server_name] = True
            except Exception as e:
                logger.warning(f"Test connessione fallito per {server_name}: {e}")
                results[server_name] = False
        
        return results

    def __repr__(self) -> str:
        return (f"MCPWrapper(provider={self.llm_provider}, model={self.model}, "
                f"servers={list(self.mcp_servers.keys())}, initialized={self._initialized})")