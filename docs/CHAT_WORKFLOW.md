# CHAT_WORKFLOW.md

Official procedure for **opening** and **closing** a development chat for the project
**mcp-bridge – MCP + A2A integration**, without losing context and without degrading performance.

This file is part of the project documentation and must be followed every time.

---

## 🎯 Goal

* Use **always-new, fast chats**
* Avoid context loss
* Avoid implicit or undocumented decisions
* Ensure that project knowledge lives in the repository, not in chat history

---

# ▶️ Chat opening procedure

Follow these steps at the **beginning of every new chat**.

## Step 1 — Open a new chat

* Open a **completely new chat**
* Do not reuse previous conversations
* Do not reference past chats

## Step 2 — Paste the bootstrap prompt

* Open:

```
docs/NEW_CHAT_PROMPT.md
```

* Copy **the entire contents**
* Paste it as the **first message** in the chat

## Step 3 — Declare the task

Immediately after the bootstrap prompt, write the task in **1–3 lines**, for example:

```text
I want to continue development on the mcp-bridge project.

Current task:
Implement GET /a2a/agents/{agent_id}/tasks/{task_id}
using the existing HTTP shim, without introducing the A2A SDK yet.
```

Rules:

* No long explanations
* No historical context
* Context will be loaded from the project docs

## Step 4 — Wait for context requests

Do **not** paste documentation proactively.

The assistant must:

1. Ask for `docs/PROJECT_CONTEXT.md`
2. After reading it, ask **only if necessary** for one or more of:

   * `docs/ARCHITECTURE_FLOW.md`
   * `docs/DECISIONS.md`
   * `docs/SNIPPETS.md`

Paste **only** what is explicitly requested.

## Step 5 — Development phase

* Work normally on the task
* Assume the assistant only knows what is contained in the pasted documents
* If new decisions or architectural changes emerge, keep working; they will be handled during closure

---

# ⏹ Chat closing procedure

Follow these steps **before leaving the chat**.

## Step 6 — Explicitly request closure

Send this as the final message:

```text
Before we stop, perform the closure phase as defined in NEW_CHAT_PROMPT.md.
```

## Step 7 — End-of-chat analysis (assistant responsibility)

The assistant **must** evaluate whether the conversation introduced:

* a new decision
* an architectural change
* a behavior change
* a newly identified limitation

Then it must state **which docs must be updated**, choosing from:

* `PROJECT_CONTEXT.md`
* `ARCHITECTURE_FLOW.md`
* `DECISIONS.md`
* `SNIPPETS.md`

## Step 8 — Documentation update proposal

For **each** file that needs updating, the assistant must provide:

* The **reason** for the update
* **Copy-paste-ready content** (or a clear conceptual diff)
* Clear indication of **where** to place it

Example:

```text
File to update: DECISIONS.md
Reason:
- We decided to keep A2A task polling out of scope for now.

Suggested addition under "Accepted decisions":
<markdown snippet>
```

If no updates are required, the assistant must say:

```text
No documentation updates are required.
```

## Step 9 — Apply updates and close the chat

* Copy the suggested updates
* Apply them to the appropriate files under `docs/`
* Save / commit
* Close the chat permanently

---

## 🔒 Non-negotiable rules

* The chat is **never** a source of truth
* Anything important must be written into `docs/`
* Every chat is **disposable**
* Project context lives in the repository, not in ChatGPT history

---

## ✅ Expected outcome

By following this procedure:

* chats remain fast
* context is preserved
* decisions are not re-litigated
* mcp-bridge evolves in a controlled, traceable way
