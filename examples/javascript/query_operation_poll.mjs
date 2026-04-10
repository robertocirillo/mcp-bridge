const baseUrl = (process.env.MCP_BRIDGE_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
const llmProvider = process.env.MCP_BRIDGE_LLM_PROVIDER ?? "openai";
const llmModel = process.env.MCP_BRIDGE_LLM_MODEL ?? "gpt-4o-mini";
const serverRoot = process.env.MCP_SERVER_ROOT ?? "/tmp";
const tenantId = process.env.MCP_BRIDGE_TENANT_ID;
const runId = process.env.MCP_BRIDGE_RUN_ID;

function buildHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (tenantId) headers["X-Tenant-Id"] = tenantId;
  if (runId) headers["X-Run-Id"] = runId;
  return headers;
}

async function apiRequest(method, path, payload) {
  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers: buildHeaders(),
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new Error(`${method} ${path} failed with HTTP ${response.status}: ${text}`);
  }

  return data;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function deleteSession(sessionId) {
  try {
    await apiRequest("DELETE", `/sessions/${sessionId}`);
    console.log(`\nDeleted session: ${sessionId}`);
  } catch (error) {
    console.error(`\nWarning: failed to delete session ${sessionId}: ${error.message}`);
  }
}

async function main() {
  let sessionId;

  try {
    const sessionResponse = await apiRequest("POST", "/sessions", {
      llm_provider: {
        provider: llmProvider,
        model: llmModel,
        temperature: 0,
      },
      mcp_servers: {
        filesystem: {
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-filesystem", serverRoot],
        },
      },
      max_steps: 20,
    });

    sessionId = sessionResponse.session_id;
    console.log(`Created session: ${sessionId}`);

    const operation = await apiRequest("POST", `/sessions/${sessionId}/query-operations`, {
      query: "Use the filesystem MCP server to summarize what is available in the configured root directory.",
      max_steps: 10,
    });

    console.log(`Started operation: ${operation.operation_id}`);

    while (true) {
      const current = await apiRequest(
        "GET",
        `/sessions/${sessionId}/query-operations/${operation.operation_id}`
      );

      console.log(`Operation status: ${current.status}`);

      if (current.status === "completed") {
        console.log("\nQuery result:\n");
        console.log(current.result?.result ?? "(empty result)");
        return;
      }

      if (current.status === "failed") {
        throw new Error(`Operation failed: ${current.error?.message ?? "unknown error"}`);
      }

      if (current.status === "cancelled") {
        throw new Error("Operation was cancelled");
      }

      if (current.status === "input-required") {
        throw new Error("Operation is waiting for user input; resume is outside this example.");
      }

      await sleep(1000);
    }
  } finally {
    if (sessionId) {
      await deleteSession(sessionId);
    }
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
