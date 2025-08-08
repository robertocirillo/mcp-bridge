FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv pip install --no-cache-dir

COPY app /app

CMD ["uvicorn", "app.mcp_use_api_wrapper:app", "--host", "0.0.0.0", "--port", "8000"]
