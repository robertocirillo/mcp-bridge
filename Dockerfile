FROM python:3.11-slim

# Install required dependencies
RUN apt-get update && apt-get install -y nodejs npm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    ca-certificates \
    git \
    build-essential \
    bash \
    procps \
    docker.io \
    file \
    strace \
    binutils \
    python3-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Verify installations and install Python tools
RUN python --version \
    && node --version \
    && npm --version \
    && npx --version \
    && pip install --no-cache-dir pyelftools jsonschema

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install Python dependencies
RUN pip install uv

# Install Python dependencies
RUN uv sync

# Copy application code
COPY . .

# Set environment variables
ENV PORT=8000
ENV HOST=0.0.0.0
ENV SHELL=/bin/bash
ENV LANG=C.UTF-8

# Expose port
EXPOSE 8000

# Run the application
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]