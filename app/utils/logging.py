"""
Configurazione del sistema di logging per il codice locale
(non modifica i logger di mcp-use o di uvicorn)
"""

import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import settings

def setup_logging():
    """Configura il logging solo per il namespace del tuo codice"""

    # Crea directory logs se non esiste
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Logger per il tuo namespace
    logger = logging.getLogger("app")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Se già ci sono handler, non aggiungerne altri
    if not logger.handlers:
        formatter = logging.Formatter(settings.LOG_FORMAT)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

        # File handler
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10*1024*1024,
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    logger.info("Sistema di logging configurato")

def get_logger(name: str) -> logging.Logger:
    """Restituisce un logger configurato per il namespace specificato"""
    return logging.getLogger(f"app.{name}")
