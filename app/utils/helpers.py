"""
Funzioni helper e utilità varie
"""

import json
import asyncio
from typing import Any, Dict, Optional, Callable
from datetime import datetime, timezone
import hashlib

def generate_session_id(config: Dict[str, Any]) -> str:
    """
    Genera un ID sessione basato sulla configurazione
    
    Args:
        config: Configurazione della sessione
        
    Returns:
        ID sessione generato
    """
    # Crea un hash della configurazione per ID deterministico (opzionale)
    config_str = json.dumps(config, sort_keys=True, default=str)
    config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"session_{timestamp}_{config_hash}"

def safe_json_serialize(obj: Any) -> str:
    """
    Serializza un oggetto in JSON gestendo tipi non serializzabili
    
    Args:
        obj: Oggetto da serializzare
        
    Returns:
        Stringa JSON
    """
    def json_serializer(o):
        if isinstance(o, datetime):
            return o.isoformat()
        elif hasattr(o, '__dict__'):
            return o.__dict__
        else:
            return str(o)
    
    return json.dumps(obj, default=json_serializer, indent=2)

def format_execution_time(seconds: float) -> str:
    """
    Formatta il tempo di esecuzione in modo leggibile
    
    Args:
        seconds: Tempo in secondi
        
    Returns:
        Tempo formattato
    """
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.2f}s"

def validate_provider_config(provider: str, config: Dict[str, Any]) -> bool:
    """
    Valida la configurazione di un provider LLM
    
    Args:
        provider: Nome del provider
        config: Configurazione del provider
        
    Returns:
        True se valida, False altrimenti
    """
    required_fields = {
        "openai": ["model"],
        "anthropic": ["model"], 
        "ollama": ["model"]
    }
    
    if provider not in required_fields:
        return False
    
    for field in required_fields[provider]:
        if field not in config or not config[field]:
            return False
    
    return True

async def retry_async(
    func: Callable,
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
) -> Any:
    """
    Riprova una funzione asincrona con backoff esponenziale
    
    Args:
        func: Funzione da riprovare
        max_retries: Numero massimo di tentativi
        delay: Delay iniziale
        backoff: Fattore di backoff
        exceptions: Eccezioni da catturare
        
    Returns:
        Risultato della funzione
        
    Raises:
        L'ultima eccezione se tutti i tentativi falliscono
    """
    last_exception = None
    current_delay = delay
    
    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func()
            else:
                return func()
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                await asyncio.sleep(current_delay)
                current_delay *= backoff
            else:
                raise last_exception

def sanitize_filename(filename: str) -> str:
    """
    Sanitizza un nome file rimuovendo caratteri non validi
    
    Args:
        filename: Nome file da sanitizzare
        
    Returns:
        Nome file sanitizzato
    """
    import re
    # Rimuovi caratteri non validi
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Rimuovi spazi multipli e trim
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized

def get_memory_usage() -> Dict[str, float]:
    """
    Ottiene informazioni sull'utilizzo della memoria
    
    Returns:
        Dizionario con informazioni sulla memoria
    """
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            "rss_mb": memory_info.rss / 1024 / 1024,  # Resident Set Size
            "vms_mb": memory_info.vms / 1024 / 1024,  # Virtual Memory Size
            "percent": process.memory_percent()
        }
    except ImportError:
        return {"error": "psutil not available"}
    except Exception as e:
        return {"error": str(e)}

def parse_duration(duration_str: str) -> Optional[int]:
    """
    Parsa una stringa di durata in secondi
    
    Args:
        duration_str: Stringa durata (es: "30s", "5m", "1h")
        
    Returns:
        Durata in secondi o None se non valida
    """
    import re
    
    pattern = r'^(\d+)([smh])$'
    match = re.match(pattern, duration_str.lower())
    
    if not match:
        return None
    
    value, unit = match.groups()
    value = int(value)
    
    multipliers = {
        's': 1,
        'm': 60,
        'h': 3600
    }
    
    return value * multipliers.get(unit, 1)