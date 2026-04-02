# Multimodal V2 Roadmap

Roadmap and design notes for the next evolution of multimodal/file support in **mcp-bridge**.

This version updates the previous roadmap with the following decisions:

- the work previously thought of as **0.2.1** has now been **incorporated into the consolidated 0.2.0**
- the original sequencing remained valid: multipart image support and lifecycle hardening landed before the first document format
- **PDF support** is now the **first non-image format** included in the consolidated `0.2.0`
- for PDF support toward LLMs, the chosen strategy is **pass-through with capability gating**, **not** unconditional bridge-side text extraction and **not** blind pass-through

---

## 1. Current baseline

The project already has a working V1/V1.5 image path with the following characteristics:

- legacy `query: string` still supported
- structured `input` payload already introduced
- image inputs already supported via:
  - `source_type="base64"`
  - `source_type="url"`
- backward compatibility preserved
- multimodal base64 path already tested end-to-end successfully
- server-side URL image fetch already present with hardening
- guardrails still applied only to text
- metadata and async operation summaries already avoid leaking raw base64/blob content

Important implementation reality:

- the codebase already contains a multimodal resolution pipeline, not just a naive request model change
- the current design already points toward a normalized internal representation of resolved images before provider invocation

This roadmap must start from this real state, not from a greenfield design.

---

## 2. Accepted direction

### D1 - Multipart comes before S3

**Accepted**

The next implementation step for V2 is **multipart upload support**.

Reason:

- it solves the immediate UX problem of huge base64 strings in client payloads
- it fits naturally with the current bridge-centric API model
- it keeps the visual builder coupled only to the bridge API, not to shared infrastructure details
- it can be implemented incrementally on top of the existing resolver pipeline

### D2 - Multipart must be designed as a generic asset ingestion layer, not as an image-only hack

**Accepted**

Even if V2 is driven by image upload support, the internal design must be extensible to future file types.

Goal:

- avoid building a one-off image-only upload path that must be rewritten later
- introduce a reusable session-scoped asset handling model that can later support PDFs, audio, documents, and other file categories

### D3 - S3/object storage is a later evolution, not the first V2 step

**Accepted**

S3-compatible object storage is considered a good future direction, especially for:

- large files
- many files
- heterogeneous file types
- more scalable retention/cleanup
- multi-instance deployments

However, it should follow the multipart phase, not replace it immediately.

### D4 - If/when S3 is introduced, use one shared bucket with session prefixes

**Accepted**

Do **not** create one bucket per session.

Preferred structure:

- one shared bucket
- per-session prefixes, for example `sessions/{session_id}/...`

### D5 - Cleanup must never rely only on explicit session deletion

**Accepted**

Regardless of backend storage choice (local temp storage first, S3 later), asset cleanup must be layered:

1. primary cleanup on explicit session deletion
2. fallback cleanup via TTL / background garbage collection
3. cleanup logic must be idempotent and resilient to crashes/restarts

### D6 - PDF is the first non-image format included in the consolidated 0.2.0

**Accepted**

The first extension beyond images is **PDF only**, added after the multipart image milestone and folded into the consolidated `0.2.0`.

Reason:

- PDFs cover many practical document-export scenarios
- limiting scope to one non-image type keeps the design controlled
- PDFs are a good fit both for future LLM use and MCP-server/tool use
- this reduces scope compared with generic file support while still validating the extensible asset model

### D7 - For PDFs toward LLMs, prefer pass-through with capability gating

**Accepted**

For PDF input sent toward LLMs, the bridge should prefer:

- **pass-through when the configured backend/runtime is explicitly known to support PDF input**
- **clear bridge-side error when it is not supported**

The bridge should **not** initially:

- always extract text from PDF as the baseline path
- blindly pass PDFs downstream and hope the runtime fails cleanly

Reason:

- avoids unnecessary bridge-side document processing in the first step
- avoids ambiguous downstream failures
- keeps API behavior explicit and predictable
- stays aligned with capability checks already planned for multimodal input

---

## 3. Why multipart first

Multipart is the recommended first step because it is the best trade-off for the current project state.

### Main benefits

- solves the current client UX issue without sending base64 blobs in JSON
- preserves a clean bridge-owned API boundary
- requires less infrastructure than shared object storage
- keeps control of validation and error handling inside mcp-bridge
- fits well with the current modular resolver-based implementation

### Main limitations

- requires dedicated route parsing for multipart bodies
- the bridge still handles uploaded binary content directly
- large file support is possible, but local/temp storage is less scalable than object storage

### Consequence

V2 should use multipart as the **ingestion mechanism**, but the internals should already be organized around a reusable **session asset** abstraction.

---

## 4. Why S3 later

S3-compatible object storage remains a strong follow-up evolution.

### What it solves well

- retention and cleanup of large files
- lower pressure on container local disk
- better scaling for heterogeneous assets
- easier support for future non-image file categories
- better fit for distributed deployments

### Why it should not be the first step

- it adds infrastructure and security complexity
- it increases coupling between the visual builder and storage conventions if introduced too early
- it does not remove the need for bridge-side validation and normalization
- the immediate problem can be solved sooner and more safely with multipart

---

## 5. Target architecture direction

The implementation should evolve toward a generic session asset model.

### 5.1 Core concepts

#### SessionAsset

A session-scoped file-like resource stored temporarily and referenced internally.

Suggested fields:

- `asset_id`
- `session_id`
- `kind` (e.g. `image`, `document`, `audio`, `generic`)
- `purpose` (e.g. `input_image`, `input_document`, `attachment`, future categories)
- `original_filename`
- `declared_content_type`
- `detected_content_type`
- `size_bytes`
- `storage_backend`
- `storage_location`
- `created_at`
- `last_accessed_at`
- `expires_at`
- optional checksum / digest

#### Asset ingestion

A dedicated path responsible for:

- receiving multipart files
- validating size/count/type
- spooling/storing them safely
- registering them as session assets

#### Asset resolution

A dedicated path responsible for converting a stored asset into the format needed by the runtime/provider.

For images, this should continue to end in the current normalized resolved-image model.

For PDFs, the initial design target is:

- **MCP-server/tool path**: pass asset handle/reference/bytes in a controlled way
- **LLM path**: allow pass-through only after capability gating confirms support

### 5.2 Storage backend evolution

The internal abstraction should make room for more than one backend:

- initial backend: local temp/session storage
- later backend: S3-compatible object storage

This means multipart V2 should not hardcode file handling too deeply into route code.

---

## 6. Recommended implementation shape

### 6.1 Keep multimodal and asset logic modular

Do not add too much logic to already large files.

Prefer new modules such as:

- `app/core/session_assets.py`
- `app/core/session_asset_store.py`
- `app/core/session_asset_cleanup.py`
- `app/core/image_validation.py`
- `app/core/document_validation.py`
- `app/core/multipart_parser.py`
- `app/core/model_capabilities.py`

Direction:

- separate file ingestion/storage concerns from request routing
- separate provider capability checks from request parsing
- separate cleanup logic from session deletion orchestration
- avoid mixing image-specific and future document-specific logic in one large route/service file

### 6.2 Preserve normalized internal resolution where needed

The existing multimodal flow already resolves input images before provider invocation.

This normalized internal contract should remain the central design point for images.

New input forms should converge into the same internal representation **when that representation is actually needed**.

For PDFs, the design does **not** need to force image-style normalization if the actual runtime contract is a gated pass-through.

---

## 7. Updated roadmap

Implementation status for the consolidated `0.2.0` target:

- capability preflight now happens before sync execution and before async query-operation creation
- multipart sync and async flows both use session-scoped temporary assets plus the same normalized upload resolution path
- local temporary assets now persist minimal metadata on disk so TTL cleanup remains idempotent and restart-safe
- multipart PDF uploads are now supported for LLM queries and for direct MCP tool invocation within the scoped bridge-owned asset flow
- the consolidated `0.2.0` now includes multipart images, cleanup/lifecycle hardening, and the first PDF milestone; broader file support and S3 remain future work

## Phase 1 - foundation hardening (to be incorporated into 0.2.0)

This phase is effectively the work that had been discussed as a potential **0.2.1**, but should now be completed and folded into the release that will remain **0.2.0**.

### 1. Capability checks for image input

Before or during query preparation, detect whether the configured provider/model supports image input.

Expected behavior:

- if images are provided to a text-only model, return a clear client error
- avoid ambiguous downstream runtime failures
- keep the error message explicit and API-friendly

### 2. Centralized multimodal limits

Introduce stronger aggregate policies, not only per-image checks.

Recommended controls:

- max image count per request
- max total bytes across all images
- MIME allowlist
- optional per-source-type limits
- safer validation messages

### 3. Improved metadata / observability

Extend safe summaries with fields such as:

- source kind
- detected MIME
- size in bytes
- resolution method (`base64`, `url`, future `upload`, future `storage_ref`)

Never expose raw base64 or raw file bytes in public metadata.

## Phase 2 - multipart V2 for images (still part of the 0.2.0 consolidation)

### 4. Introduce multipart ingestion endpoints or multipart-capable route variants

Requirements:

- preserve backward compatibility with existing JSON endpoints
- avoid breaking legacy clients
- keep contract explicit for new clients

Possible direction:

- dedicated multipart query endpoints
- or dedicated multipart-capable variants for sync/async query execution

### 5. Introduce session-scoped temporary asset storage

Requirements:

- small files may be buffered briefly if appropriate
- larger files should be spooled/stored outside RAM
- assets must be associated with session id
- assets must have metadata recorded for cleanup and observability

### 6. Extend image resolution to uploaded assets

The image resolver path should support uploaded files as another source kind.

Target principle:

- `base64`
- `url`
- `upload`
- later `storage_ref`

should all converge toward the same resolved image contract.

## Phase 3 - cleanup and lifecycle (still part of the 0.2.0 consolidation)

### 7. Cleanup on session deletion

When a session is deleted, associated temporary assets must be deleted as well.

This includes:

- local temp files/directories
- registry entries / metadata records
- any related async leftovers if applicable

### 8. Fallback TTL and garbage collection

Add a safety mechanism for cases where session deletion never happens.

Requirements:

- periodic cleanup process
- TTL for stale session assets
- resilience to crash/restart scenarios
- idempotent delete operations

## Phase 4 - PDF support (included in the consolidated 0.2.0)

This phase is now part of the final consolidated `0.2.0` release.

### 9. Add PDF as the first non-image session asset type

Scope:

- accept PDF upload in multipart flows
- register PDF assets in the same session-scoped asset model
- apply PDF-specific validation policy
- preserve cleanup/lifecycle guarantees already established for images

### 10. Support PDF for MCP-server/tool use

Target behavior:

- PDFs can be passed to MCP-server/tool flows in a controlled way
- the bridge remains responsible for lifecycle and access mediation
- the API/runtime contract should avoid exposing storage internals more than necessary

### 11. Support PDF for LLMs via capability gating + pass-through

Target behavior:

- if the configured backend/runtime is known to support PDF input, allow pass-through
- if not supported, fail early with a clear client-facing error
- do not rely on downstream runtime failure to define product behavior

### 12. Defer bridge-side PDF text extraction to a later optional phase

Text extraction from PDF is **not** the baseline strategy for the first PDF milestone.

It may be evaluated later as an explicit fallback or optional mode if needed.

## Phase 5 - broader generic file support

Only after images + lifecycle + PDF support are stable should the bridge expand toward broader non-image coverage.

Examples:

- text documents beyond PDF
- audio
- structured data files
- other asset-only file categories

Important distinction:

- not every uploaded file type must be accepted as direct model multimodal input
- some file types may remain asset-only for tool/runtime use

## Phase 6 - S3/object storage evolution

### 13. Introduce pluggable asset storage backend

After multipart, lifecycle, and PDF semantics are stable, add an object-storage-backed implementation.

Preferred model:

- one shared bucket
- session-scoped prefixes
- bridge-owned storage abstraction
- cleanup by prefix on session delete
- fallback lifecycle/TTL policy in storage layer

### 14. Future optional storage references

Only after storage support is mature should the bridge consider accepting storage-backed references from clients.

This should be designed carefully to avoid over-coupling the visual builder to storage internals too early.

---

## 8. Non-goals for the consolidated 0.2.0

The current delivery should **not** try to solve everything at once.

Not required in the consolidated 0.2.0:

- full generic file platform support
- S3 integration immediately
- bucket-per-session provisioning
- bridge-side PDF text extraction as the default path
- rich cross-service storage contracts with the visual builder

---

## 9. Practical decision summary

Current agreed direction:

1. keep the current V1/V1.5 multimodal base
2. complete foundation hardening + multipart image support + cleanup/lifecycle + scoped PDF support
3. fold all of that into the final **0.2.0** release
4. for PDF toward MCP servers, support controlled pass-through/runtime use
5. for PDF toward LLMs, use **capability gating + pass-through**
6. defer PDF text extraction to a later optional phase if needed
7. only later expand to broader file classes
8. only after that, introduce S3-compatible storage if still justified

---

## 10. Suggested branch names

For the work being folded into the final 0.2.0:

- `feature/session-assets-v1`
- `feature/multimodal-image-sources-v2`
- `feature/multipart-images-hardening`

For the PDF tranche that was folded into the consolidated 0.2.0:

- `feature/pdf-session-assets`
- `feature/pdf-pass-through-v1`
- `feature/pdf-multimodal-v1`

---

## 11. Notes for future chats

When continuing this topic in a new chat, the assistant should be told that:

- V1/V1.5 already exists and works
- the consolidated `0.2.0` already includes multipart images, cleanup/lifecycle hardening, and scoped PDF support
- PDF toward LLMs should use capability gating + pass-through
- PDF text extraction is intentionally deferred
- broader generic file support comes after PDF, not before
- S3 is still a later storage evolution
