import os
import logging
from typing import Optional, Dict, Any, List

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPWrapper:
    """Wrapper minimale per mcp-use che incapsula completamente la libreria"""



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

        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0

        # Import dinamici per evitare dipendenze esterne
        self._import_dependencies()

    def _import_dependencies(self):
        """Importa le dipendenze necessarie"""
        # Import mcp-use
        try:
            from mcp_use import MCPAgent, MCPClient
            from mcp_use.types.sandbox import SandboxOptions
            self.MCPAgent = MCPAgent
            self.MCPClient = MCPClient
            self.SandboxOptions = SandboxOptions
        except ImportError:
            raise ImportError("mcp-use non installato. Installare con: pip install mcp-use")

        # Import LangChain providers
        if self.llm_provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
                self.ChatLLM = ChatOpenAI
            except ImportError:
                raise ImportError("langchain-openai non installato. Installare con: pip install langchain-openai")

        elif self.llm_provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
                self.ChatLLM = ChatAnthropic
            except ImportError:
                raise ImportError("langchain-anthropic non installato. Installare con: pip install langchain-anthropic")

        elif self.llm_provider == "ollama":
            try:
                from langchain_ollama import ChatOllama
                self.ChatLLM = ChatOllama
            except ImportError:
                raise ImportError("langchain-ollama non installato. Installare con: pip install langchain-ollama")

        else:
            raise ValueError(f"Provider non supportato: {self.llm_provider}. Supportati: openai, anthropic, ollama")

    def _create_llm(self):
        """Crea l'istanza del modello LLM"""
        kwargs = {
            "model": self.model,
            "temperature": self.temperature,
        }

        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        if self.llm_provider == "openai":
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url

        elif self.llm_provider == "anthropic":
            if self.api_key:
                kwargs["api_key"] = self.api_key

        elif self.llm_provider == "ollama":
            if self.base_url:
                kwargs["base_url"] = self.base_url
            else:
                kwargs["base_url"] = "http://localhost:11434"

        return self.ChatLLM(**kwargs)

    def _create_mcp_config(self) -> Dict[str, Any]:
        """Crea la configurazione per i server MCP"""
        return {"mcpServers": self.mcp_servers}

    async def initialize(self):
        """Inizializza l'agent e i client MCP"""
        if self._initialized:
            return

        try:
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

            self._initialized = True
            logger.info("MCPWrapper inizializzato con successo")

        except Exception as e:
            logger.error(f"Errore nell'inizializzazione: {e}")
            raise

    async def run_query(self,
                        query: str,
                        max_steps: Optional[int] = None,
                        server_name: Optional[str] = None) -> str:
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

        try:
            # Prepara i parametri
            run_kwargs = {"query": query}

            if max_steps:
                run_kwargs["max_steps"] = max_steps

            if server_name:
                run_kwargs["server_name"] = server_name

            # Esegue la query
            result = await self._agent.run(**run_kwargs)

            # Aggiorna i passi utilizzati
            self._steps_used = getattr(self._agent, 'steps_used', 0)

            return str(result)

        except Exception as e:
            logger.error(f"Errore nell'esecuzione della query: {e}")
            raise

    async def close(self):
        """Chiude le connessioni e rilascia le risorse"""
        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.info("Client MCP chiuso correttamente")
            except Exception as e:
                logger.warning(f"Errore nella chiusura del client: {e}")

        self._agent = None
        self._client = None
        self._initialized = False

    @property
    def steps_used(self) -> int:
        """Restituisce il numero di passi utilizzati nell'ultima esecuzione"""
        return self._steps_used
