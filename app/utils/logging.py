"""
Configurazione del sistema di logging
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import settings

class ColorFormatter(logging.Formatter):
    """Formatter colorato per console, distingue i log di app.*"""
    COLORS = {
        "DEBUG": "\033[94m",    # blu
        "INFO": "\033[92m",     # verde
        "WARNING": "\033[93m",  # giallo
        "ERROR": "\033[91m",    # rosso
        "CRITICAL": "\033[41m", # sfondo rosso
    }
    RESET = "\033[0m"

    def format(self, record):
        msg = super().format(record)
        color = self.COLORS.get(record.levelname, "")
        # aggiungi un prefisso per distinguere il tuo codice
        if record.name.startswith("app."):
            msg = f"{color}[APP]{msg}{self.RESET}"
        return msg

def setup_logging():
    """Configura il sistema di logging"""

    # Crea directory logs se non esiste
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Configurazione root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Rimuovi handler esistenti
    root_logger.handlers.clear()

    # Formatter generico
    file_formatter = logging.Formatter(settings.LOG_FORMAT)

    # Console handler colorato
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter(settings.LOG_FORMAT))
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # File handler per logs generali
    file_handler = RotatingFileHandler(
        log_dir / "mcp_api.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    # File handler per errori
    error_handler = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    error_handler.setFormatter(file_formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

    # Configurazione logging per librerie esterne
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    logger = logging.getLogger(__name__)
    logger.info("Sistema di logging configurato")

def get_logger(name: str) -> logging.Logger:
    """Ottiene un logger configurato per il tuo codice"""
    return logging.getLogger(name)
