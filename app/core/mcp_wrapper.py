"""
Refined wrapper for mcp-use with enhanced error handling
"""

import os
import logging
from typing import Optional, Dict, Any, List

from app.core.exceptions import MCPWrapperError, DependencyError, ConfigurationError
from app.utils.logging import get_logger
from app.utils.helpers import retry_async

logger = get_logger(__name__)

class MCPWrapper:
    """Enhanced wrapper for mcp-use that fully encapsulates the library"""

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
        Initializes the MCP wrapper

        Args:
            llm_provider: Model provider (openai, anthropic, ollama)
            model: Model name
            api_key: API key (optional if set in environment)
            base_url: Base URL for custom providers
            temperature: Model temperature
            max_tokens: Maximum number of tokens
            mcp_servers: MCP servers configuration
            max_steps: Maximum steps for the agent
            verbose: Verbose mode for debugging
            use_sandbox: Use the E2B sandbox environment
            sandbox_options: Options for the sandbox
            disallowed_tools: Tools not allowed
            use_server_manager: Use server manager for automatic selection
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

        # Internal state
        self._agent = None
        self._client = None
        self._initialized = False
        self._steps_used = 0
        self._last_server_used = None

        # Initial validation
        self._validate_config()

        # Import dependencies
        self._import_dependencies()

    def _validate_config(self):
        """Validates the initial configuration"""
        if not self.llm_provider:
            raise ConfigurationError("LLM provider not specified")

        if not self.model:
            raise ConfigurationError("Model not specified")

        if not self.mcp_servers:
            raise ConfigurationError("No MCP servers configured")

        # Validate MCP servers
        for name, config in self.mcp_servers.items():
            if not config.get("command") and not config.get("url"):
                raise ConfigurationError(f"Server {name}: must have 'command' or 'url'")

    def _import_dependencies(self):
        """Imports required dependencies with enhanced error handling"""
        # Import mcp-use
        try:
            from mcp_use import MCPAgent, MCPClient
            from mcp_use.types.sandbox import SandboxOptions
            self.MCPAgent = MCPAgent
            self.MCPClient = MCPClient
            self.SandboxOptions = SandboxOptions
            logger.debug("mcp-use successfully imported")
        except ImportError as e:
            raise DependencyError(f"mcp-use not installed: {e}")

        # Import LangChain providers
        if self.llm_provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
                self.ChatLLM = ChatOpenAI
                logger.debug("langchain-openai successfully imported")
            except ImportError as e:
                raise DependencyError(f"langchain-openai not installed: {e}")

        elif self.llm_provider == "anthropic":
            try:
                from langchain_anthropic import ChatAnthropic
                self.ChatLLM = ChatAnthropic
                logger.debug("langchain-anthropic successfully imported")
            except ImportError as e:
                raise DependencyError(f"langchain-anthropic not installed: {e}")

        elif self.llm_provider == "ollama":
            try:
                from langchain_ollama import ChatOllama
                self.ChatLLM = ChatOllama
                logger.debug("langchain-ollama successfully imported")
            except ImportError as e:
                raise DependencyError(f"langchain-ollama not installed: {e}")

        else:
            raise ConfigurationError(f"Unsupported provider: {self.llm_provider}")

    def _create_llm(self):
        """Creates the LLM model instance with error handling"""
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
                    raise ConfigurationError("OpenAI API key not found")

                if self.base_url:
                    kwargs["base_url"] = self.base_url

            elif self.llm_provider == "anthropic":
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                elif not os.getenv("ANTHROPIC_API_KEY"):
                    raise ConfigurationError("Anthropic API key not found")

            elif self.llm_provider == "ollama":
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                else:
                    kwargs["base_url"] = "http://localhost:11434"

            llm = self.ChatLLM(**kwargs)
            logger.debug(f"LLM {self.llm_provider}/{self.model} successfully created")
            return llm

        except Exception as e:
            raise MCPWrapperError(f"Error creating LLM model: {e}")

    def _create_mcp_config(self) -> Dict[str, Any]:
        """Creates the configuration for MCP servers"""
        return {"mcpServers": self.mcp_servers}

    async def initialize(self):
        """Initializes the MCP agent and clients with automatic retry"""
        if self._initialized:
            logger.debug("MCPWrapper already initialized")
            return

        try:
            # Use retry for network operations
            await retry_async(self._initialize_internal, max_retries=3, delay=1.0)

            self._initialized = True
            logger.info("MCPWrapper successfully initialized")

        except Exception as e:
            logger.error(f"Initialization error after all attempts: {e}")
            raise MCPWrapperError(f"Initialization failed: {e}")

    async def _initialize_internal(self):
        """Internal initialization logic"""
        # Create LLM model
        llm = self._create_llm()

        # Create MCP configuration
        mcp_config = self._create_mcp_config()

        # Configure MCP client
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

        # Create the agent
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

    async def run_query(self,
                        query: str,
                        max_steps: Optional[int] = None,
                        server_name: Optional[str] = None) -> str:
        """
        Executes a query using the MCP agent

        Args:
            query: The query to process
            max_steps: Override for maximum steps (optional)
            server_name: Specific server name to use (optional)

        Returns:
            The agent's response as a string
        """
        if not self._initialized:
            await self.initialize()

        if not query.strip():
            raise ValueError("Empty query not allowed")

        try:
            logger.debug(f"Executing query: {query[:100]}...")

            # Prepare parameters
            run_kwargs = {"query": query}

            if max_steps:
                run_kwargs["max_steps"] = max_steps

            if server_name:
                if server_name not in self.mcp_servers:
                    raise ValueError(f"Server '{server_name}' not configured")
                run_kwargs["server_name"] = server_name
                self._last_server_used = server_name

            # Define a separate async function for retry
            async def execute_agent_run():
                return await self._agent.run(**run_kwargs)

            # Execute the query with retry
            result = await retry_async(
                execute_agent_run,
                max_retries=2,
                delay=0.5
            )

            # Update stats
            self._steps_used = getattr(self._agent, 'steps_used', 0)

            if not self._last_server_used and hasattr(self._agent, 'last_server_used'):
                self._last_server_used = self._agent.last_server_used

            logger.debug(f"Query completed in {self._steps_used} steps")
            return str(result)

        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise MCPWrapperError(f"Query execution failed: {e}")


    async def close(self):
        """Closes connections and releases resources"""
        if self._client:
            try:
                await self._client.close_all_sessions()
                logger.debug("MCP client closed successfully")
            except Exception as e:
                logger.warning(f"Error closing MCP client: {e}")

        self._agent = None
        self._client = None
        self._initialized = False
        logger.debug("MCPWrapper closed")

    @property
    def steps_used(self) -> int:
        """Returns the number of steps used in the last run"""
        return self._steps_used

    @property
    def last_server_used(self) -> Optional[str]:
        """Returns the last server used"""
        return self._last_server_used

    @property
    def is_initialized(self) -> bool:
        """Indicates if the wrapper has been initialized"""
        return self._initialized

    def get_config_summary(self) -> Dict[str, Any]:
        """Returns a summary of the configuration"""
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
        """Tests the connection to configured MCP servers"""
        if not self._initialized:
            await self.initialize()

        results = {}
        for server_name in self.mcp_servers.keys():
            try:
                # Simple test with a minimal query
                await self.run_query("ping", max_steps=1, server_name=server_name)
                results[server_name] = True
            except Exception as e:
                logger.warning(f"Connection test failed for {server_name}: {e}")
                results[server_name] = False

        return results

    def __repr__(self) -> str:
        return (f"MCPWrapper(provider={self.llm_provider}, model={self.model}, "
                f"servers={list(self.mcp_servers.keys())}, initialized={self._initialized})")
