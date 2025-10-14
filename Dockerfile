FROM python:3.12-slim

# Install Node.js (latest LTS) + npm
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    apt-get install -y docker.io && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only metadata files first (to leverage Docker cache)
COPY pyproject.toml uv.lock* ./

# Install uv
RUN pip install --no-cache-dir uv

# Sync dependencies into a managed virtual environment
RUN uv sync --frozen

# Copy project files
COPY . .

# Optional: ensure your local vendor package is installed in editable mode
# (only if it's not declared in pyproject.toml)
#RUN uv pip install -e ./vendor/mcp-use

# Set environment variables
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
ENV HOST=0.0.0.0
ENV LANG=C.UTF-8
ENV SHELL=/bin/bash

EXPOSE 8000

# Launch app through uv to ensure venv and deps are resolved
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
