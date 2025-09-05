# MCP-Use REST API

Un servizio REST modulare e scalabile per interagire con MCP servers tramite la libreria mcp-use.

## 🚀 Caratteristiche

- **Architettura modulare** - Codice organizzato in moduli separati
- **Gestione sessioni avanzata** - Sessioni persistenti con cleanup automatico
- **Multi-provider LLM** - Supporto per OpenAI, Anthropic, Ollama
- **Sandbox E2B** - Esecuzione sicura in ambiente isolato
- **API RESTful** - Endpoints ben documentati con OpenAPI/Swagger
- **Logging strutturato** - Logs organizzati per debugging e monitoraggio
- **Docker ready** - Container pronto per il deployment
- **Health monitoring** - Endpoints per monitoraggio stato servizio

## 📁 Struttura del Progetto

```
mcp_use_api/
├── main.py                 # Entry point FastAPI
├── config.py               # Configurazioni globali
├── requirements.txt        # Dipendenze
├── Dockerfile             # Container setup
├── docker-compose.yml     # Orchestrazione servizi
├── app/
│   ├── api/
│   │   ├── routes/         # Endpoints REST
│   │   └── dependencies.py # Dependency injection
│   ├── core/
│   │   ├── mcp_wrapper.py  # Wrapper MCP-Use
│   │   ├── session_manager.py # Gestione sessioni
│   │   └── exceptions.py   # Eccezioni custom
│   ├── models/
│   │   ├── config.py       # Modelli configurazione
│   │   ├── requests.py     # Modelli richieste
│   │   └── responses.py    # Modelli risposte
│   └── utils/
│       ├── logging.py      # Setup logging
│       └── helpers.py      # Funzioni utility
├── logs/                   # Directory log
└── tests/                  # Test suite
```

## 🛠️ Installazione

### Installazione Locale

1. **Clona il repository**
```bash
git clone <repository-url>
cd mcp_use_api
```

2. **Crea ambiente virtuale**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate     # Windows
```

3. **Installa dipendenze**
```bash
pip install -r requirements.txt
```

4. **Configura variabili d'ambiente**
```bash
cp .env.example .env
# Modifica .env con le tue configurazioni
```

5. **Avvia il servizio**
```bash
python main.py
```

### Installazione con Docker

1. **Configura l'ambiente**
```bash
cp .env.example .env
# Modifica .env con le tue configurazioni
```

2. **Avvia con Docker Compose**
```bash
docker-compose up -d
```

## ⚙️ Configurazione

### Variabili d'Ambiente Principali

```env
# API Configuration
API_TITLE="MCP-Use REST API"
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

### Provider LLM Supportati

- **OpenAI**: GPT-3.5, GPT-4, etc.
- **Anthropic**: Claude models
- **Ollama**: Modelli locali

## 📚 Utilizzo

### 1. Creare una Sessione

```bash
curl -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -d '{
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

### 2. Eseguire una Query

```bash
curl -X POST "http://localhost:8000/sessions/{session_id}/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "List the files in the directory",
    "max_steps": 10
  }'
```

### 3. Monitorare le Sessioni

```bash
# Lista sessioni attive
curl "http://localhost:8000/sessions"

# Informazioni sessione specifica
curl "http://localhost:8000/sessions/{session_id}"

# Health check
curl "http://localhost:8000/health"
```

## 🔧 API Endpoints

### Sessioni
- `POST /sessions` - Crea nuova sessione
- `GET /sessions` - Lista sessioni attive
- `GET /sessions/{id}` - Info sessione specifica
- `DELETE /sessions/{id}` - Elimina sessione

### Query
- `POST /sessions/{id}/query` - Esegue query
- `GET /sessions/{id}/history` - Cronologia query

### Monitoraggio
- `GET /health` - Health check
- `GET /stats` - Statistiche servizio
- `GET /version` - Informazioni versione

## 🧪 Testing

```bash
# Installa dipendenze di test
pip install pytest pytest-asyncio httpx

# Esegui test
pytest tests/

# Test con coverage
pytest --cov=app tests/
```

## 📊 Monitoring & Logging

### Logging
- **Console**: Output formattato per sviluppo
- **File**: `logs/mcp_api.log` per logs generali
- **Errori**: `logs/errors.log` per errori specifici

### Health Check
Il servizio espone endpoint `/health` per monitoraggio:
- Stato generale del servizio
- Numero sessioni attive
- Funzionalità disponibili

## 🐳 Docker

### Build Immagine
```bash
docker build -t mcp-use-api .
```

### Run Container
```bash
docker run -p 8000:8000 --env-file .env mcp-use-api
```

### Docker Compose
```bash
# Avvia tutti i servizi
docker-compose up -d

# Visualizza logs
docker-compose logs -f

# Ferma servizi
docker-compose down
```

## 🔒 Sicurezza

- **Sandbox E2B**: Esecuzione sicura di codice
- **Validazione input**: Tutti i dati in ingresso validati
- **Rate limiting**: Limite sessioni simultanee
- **Timeout**: Timeout automatico per sessioni inattive

## 🤝 Contribuire

1. Fork del repository
2. Crea feature branch (`git checkout -b feature/nuova-funzione`)
3. Commit modifiche (`git commit -am 'Aggiunge nuova funzione'`)
4. Push al branch (`git push origin feature/nuova-funzione`)
5. Crea Pull Request

## 📄 Licenza

[MIT License](LICENSE)

## 🆘 Supporto

Per problemi o domande:
- Apri un issue su GitHub
- Consulta la documentazione API su `/docs`
- Controlla i logs in `logs/`