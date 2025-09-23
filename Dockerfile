# usa Python >=3.12 perché il tuo pyproject chiede requires-python = ">=3.12"
FROM python:3.12-slim

# manteniamo nodejs + npm (richiesti da te)
RUN apt-get update \
    && apt-get install -y nodejs npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copia solo i file di metadata per sfruttare la cache
COPY pyproject.toml uv.lock* ./

# installa uv (usiamo la versione da pip; puoi pinarla se preferisci)
RUN pip install --no-cache-dir uv

# crea il venv dentro l'immagine (portabile)
RUN python -m venv /app/venv

# obblighiamo i processi successivi ad usare il venv
ENV VIRTUAL_ENV=/app/venv
ENV PATH="/app/venv/bin:$PATH"

# sincronizza le dipendenze dal progetto/lockfile nel venv
# --locked forza l'uso del lockfile (deterministico)
RUN uv sync --locked

# poi copia il codice dell'app (dopo aver installato le dipendenze per caching)
COPY . .

ENV PORT=8000
ENV HOST=0.0.0.0
ENV SHELL=/bin/bash
ENV LANG=C.UTF-8

EXPOSE 8001

# avvia con uv run (userà automaticamente il venv)
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

