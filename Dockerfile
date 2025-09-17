FROM python:3.12-slim

# Install dependencies: Node.js, curl, tar, Docker client, and other tools
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    gnupg2 \
    dirmngr \
    tar \
    docker.io \
    # Setup Node.js 18.x
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install docker-mcp plugin
RUN mkdir -p ~/.docker/cli-plugins && \
    curl -fsSL https://github.com/docker/mcp-gateway/releases/download/v0.18.0/docker-mcp-linux-amd64.tar.gz -o /tmp/docker-mcp.tar.gz && \
    tar -xzf /tmp/docker-mcp.tar.gz -C ~/.docker/cli-plugins && \
    chmod +x ~/.docker/cli-plugins/docker-mcp && \
    rm /tmp/docker-mcp.tar.gz

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen --no-cache

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Set environment variables
ENV PORT=8000
ENV HOST=0.0.0.0

# Run the application
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
