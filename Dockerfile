FROM python:3.12-slim

# Keep Node.js + npm
RUN apt-get update \
    && apt-get install -y nodejs npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only metadata files to leverage cache
COPY pyproject.toml uv.lock* ./

# uv
RUN pip install --no-cache-dir uv

# Create the virtual environment inside the image (portable)
RUN python -m venv /app/venv

# Force subsequent processes to use the venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/venv/bin:$PATH"

# Sync dependencies from project/lockfile into the venv
# --locked forces using the lockfile (deterministic)
RUN uv sync --locked

# Then copy the app code (after installing dependencies for caching)
COPY . .

ENV PORT=8000
ENV HOST=0.0.0.0
ENV SHELL=/bin/bash
ENV LANG=C.UTF-8

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
