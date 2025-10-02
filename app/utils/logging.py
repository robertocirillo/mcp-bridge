"""
Logging configuration for local code
(does not modify mcp-use or uvicorn loggers)
"""

import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import settings

def setup_logging():
    """Configure logging only for your code's namespace"""

    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Logger for your namespace
    logger = logging.getLogger("app")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # If handlers already exist, do not add more
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

    logger.info("Logging system configured")

def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the specified namespace"""
    return logging.getLogger(f"app.{name}")
