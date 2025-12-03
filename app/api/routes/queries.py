"""
Endpoints per l'esecuzione delle query
"""

from fastapi import APIRouter, HTTPException, Depends
import asyncio
import logging
from datetime import datetime

from app.models.requests import QueryRequest
from app.models.responses import QueryResponse
from app.core.session_manager import SessionManager
from app.core.exceptions import SessionNotFoundError, ConfigurationError, MCPWrapperError
from app.api.dependencies import get_session_manager


logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/{session_id}/query", response_model=QueryResponse)
async def execute_query(
    session_id: str,
    request: QueryRequest,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Esegue una query su una sessione esistente"""
    try:
        # Recupera la sessione
        session_data = await session_manager.get_session(session_id)
        wrapper = session_data.wrapper
        
        # Misura il tempo di esecuzione
        start_time = asyncio.get_event_loop().time()
        
        # Esegue la query usando il wrapper
        result = await wrapper.run_query(
            query=request.query,
            max_steps=request.max_steps,
            server_name=request.server_name
        )
        
        end_time = asyncio.get_event_loop().time()
        execution_time = end_time - start_time
        
        # Aggiorna le statistiche della sessione
        session_data.register_query()
        
        # Ottiene i passi utilizzati e il server usato
        steps_used = wrapper.steps_used
        server_used = getattr(wrapper, 'last_server_used', None)
        
        return QueryResponse(
            session_id=session_id,
            result=result,
            execution_time=execution_time,
            steps_used=steps_used,
            timestamp=datetime.now(),
            server_used=server_used
        )
        
    except SessionNotFoundError as e:
        logger.warning(f"Session not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Error")

@router.get("/{session_id}/history")
async def get_query_history(
    session_id: str,
    limit: int = 10,
    session_manager: SessionManager = Depends(get_session_manager)
):
    """Ottiene la cronologia delle query per una sessione"""
    try:
        # Recupera la sessione per verificare che esista
        session_data = await session_manager.get_session(session_id)
        
        # Per ora restituiamo solo le statistiche base
        # In futuro si potrebbe implementare una vera cronologia
        return {
            "session_id": session_id,
            "total_queries": session_data.query_count,
            "last_used": session_data.last_used,
            "message": "Cronologia dettagliata non ancora implementata"
        }
        
    except SessionNotFoundError as e:
        logger.warning(f"Attempt failed: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MCPWrapperError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Error")