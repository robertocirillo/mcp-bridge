# MCP-BRIDGE REST API

A modular and scalable REST service to interact with MCP servers using the mcp-use library: https://github.com/mcp-use/mcp-use
It is ready to work with a Docker MCP gateway in either DIND or DOD mode, which can be selected by using the appropriate Docker Compose file.

## 🚀 Features

- **Modular architecture** - Code organized into separate modules
- **Advanced session management** - Persistent sessions with automatic cleanup
- **Multi-provider LLM** - Support for OpenAI, Anthropic, Ollama
- **E2B Sandbox** - Safe execution in an isolated environment
- **RESTful API** - Well-documented endpoints with OpenAPI/Swagger
- **Structured logging** - Organized logs for debugging and monitoring
- **Docker ready** - Container ready for deployment
- **Health monitoring** - Endpoints for service health checks

## 📁 Project Structure

```
mcp_bridge/
├── main.py                 # FastAPI entry point
├── config.py               # Global configurations
├── requirements.txt        # Dependencies
├── Dockerfile              # Container setup
├── docker-compose*.yml      # Service orchestration
├── app/
│   ├── api/
│   │   ├── routes/         # REST endpoints
│   │   └── dependencies.py # Dependency injection
│   ├── core/
│   │   ├── mcp_wrapper.py  # MCP-Use wrapper
│   │   ├── session_manager.py # Session management
│   │   └── exceptions.py   # Custom exceptions
│   ├── models/
│   │   ├── config.py       # Config models
│   │   ├── requests.py     # Request models
│   │   └── responses.py    # Response models
│   └── utils/
│       ├── logging.py      # Logging setup
│       └── helpers.py      # Utility functions
├── logs/                   # Logs directory
└── tests/                  # Test suite
```

## 🛠️ Installation

### Local Installation

1. **Clone the repository**
```bash
git clone <repository-url>
cd mcp_bridge
```

2. **Create a virtual environment**
```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. **Start the service**
```bash
python main.py
```

### Docker Installation

1. **Prepare environment**
```bash
cp .env.example .env
# Edit .env with your configuration
```

2. **Start with Docker Compose**
```bash
docker-compose up -d
```

## ⚙️ Configuration

### Main Environment Variables

```env
# API Configuration
API_TITLE="MCP-BRIDGE REST API"
PORT=8000
DEBUG=false

# Session Management
MAX_ACTIVE_SESSIONS=100
SESSION_TIMEOUT=3600

# LLM Provider API Keys
OPENAI_API_KEY="your_key_here"
ANTHROPIC_API_KEY="your_key_here"

# E2B Sandbox (optional)
E2B_API_KEY="your_key_here"
```

### Supported LLM Providers

- **OpenAI**: GPT-3.5, GPT-4, etc.
- **Anthropic**: Claude models
- **Ollama**: Local models

## 📚 Usage

### 1. Create a Session

```bash
curl -X POST "http://localhost:8000/sessions"   -H "Content-Type: application/json"   -d '{
    "llm_provider": {
      "provider": "openai",
      "model": "gpt-3.5-turbo",
      "temperature": 0.7
    },
    "mcp_servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      }
    },
    "max_steps": 30,
    "verbose": false
  }'
```

### 2. Run a Query

```bash
curl -X POST "http://localhost:8000/sessions/{session_id}/query"   -H "Content-Type: application/json"   -d '{
    "query": "List the files in the directory",
    "max_steps": 10
  }'
```

### 3. Monitor Sessions

```bash
# List active sessions
curl "http://localhost:8000/sessions"

# Get specific session info
curl "http://localhost:8000/sessions/{session_id}"

# Health check
curl "http://localhost:8000/health"
```

## 🔧 API Endpoints

### Sessions
- `POST /sessions` - Create new session
- `GET /sessions` - List active sessions
- `GET /sessions/{id}` - Get session info
- `DELETE /sessions/{id}` - Delete session

### Query
- `POST /sessions/{id}/query` - Execute query
- `GET /sessions/{id}/history` - Query history

### Monitoring
- `GET /health` - Health check
- `GET /stats` - Service statistics
- `GET /version` - Version info

## 🧪 Testing

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest tests/

# Run tests with coverage
pytest --cov=app tests/
```

## 📊 Monitoring & Logging

### Logging
- **Console**: Formatted output for development
- **File**: `logs/mcp_api.log` for general logs
- **Errors**: `logs/errors.log` for specific errors

### Health Check
The service exposes the `/health` endpoint for monitoring:
- Overall service status
- Number of active sessions
- Available functionalities

## 🐳 Docker

### Build Image
```bash
docker build -t mcp-bridge .
```

### Run Container
```bash
docker run -p 8000:8000 --env-file .env mcp-bridge
```

### Docker Compose
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## 🔒 Security

- **E2B Sandbox**: Secure code execution
- **Input validation**: All incoming data validated
- **Rate limiting**: Limit simultaneous sessions
- **Timeouts**: Automatic timeout for inactive sessions

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/new-feature`)
3. Commit changes (`git commit -am 'Add new feature'`)
4. Push to branch (`git push origin feature/new-feature`)
5. Create a Pull Request

## 📄 License

[MIT License](LICENSE)

## 🆘 Support

For issues or questions:
- Open an issue on GitHub
- Contact the maintainer at (mailto: roberto.cirillo@isti.cnr.it)