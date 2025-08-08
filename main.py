import uvicorn

# Local development entrypoint
if __name__ == "__main__":
    uvicorn.run("app.mcp_use_api_wrapper:app", host="0.0.0.0", port=8000, reload=True)
