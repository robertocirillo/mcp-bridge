# MULTIMODAL_V2_ROADMAP.md

Roadmap and design notes for the next evolution of multimodal/file support in **mcp-bridge**.

This document is intended to preserve the current architectural direction across future chats and implementation phases.

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

Why:

- simpler lifecycle management
- lower infrastructure overhead
- easier policy handling
- more realistic cleanup strategy
- avoids over-coupling storage topology to application session creation/deletion

### D5 - Cleanup must never rely only on explicit session deletion

**Accepted**

Regardless of backend storage choice (local temp storage first, S3 later), asset cleanup must be layered:

1. primary cleanup on explicit session deletion
2. fallback cleanup via TTL / background garbage collection
3. cleanup logic must be idempotent and resilient to crashes/restarts

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

### Important note

The valuable future idea is **object-backed asset storage**, not specifically "bucket per session".

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
- `purpose` (e.g. `input_image`, `attachment`, future categories)
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

### 5.2 Storage backend evolution

The internal abstraction should make room for more than one backend:

- initial backend: local temp/session storage
- later backend: S3-compatible object storage

This means multipart V2 should not hardcode file handling too deeply into route code.

---

## 6. Recommended implementation shape

### 6.1 Keep existing multimodal/image modules modular

Do not add too much logic to already large files.

Prefer new modules such as:

- `app/core/session_assets.py`
- `app/core/session_asset_store.py`
- `app/core/session_asset_cleanup.py`
- `app/core/image_validation.py`
- `app/core/multipart_parser.py`
- `app/core/model_capabilities.py`

Names may vary, but the direction is:

- separate file ingestion/storage concerns from request routing
- separate provider capability checks from request parsing
- separate cleanup logic from session deletion orchestration

### 6.2 Preserve normalized internal resolution

The existing multimodal flow already resolves input images before provider invocation.

This normalized internal contract should remain the central design point.

New input forms should converge into the same internal representation.

---

## 7. V2 roadmap

## Phase 1 - foundation hardening

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

---

## Phase 2 - multipart V2

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

---

## Phase 3 - cleanup and lifecycle

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

---

## Phase 4 - extension toward generic file support

### 9. Generalize from image upload to session asset upload

After multipart image support is stable, gradually evolve the internal model to support additional file classes.

Examples:

- PDF
- text documents
- audio
- structured data files

Important distinction:

- not every uploaded file type must be accepted as direct model multimodal input
- some file types may be allowed only as session assets for future tool/runtime use

### 10. Introduce policy by file class

Different file types should eventually have distinct policies.

Examples:

- images: strict MIME allowlist, model-bound resolution path
- documents: different size and validation rules
- audio: separate future pipeline
- generic files: asset-only, not directly sent to the model

---

## Phase 5 - S3/object storage evolution

### 11. Introduce pluggable asset storage backend

After multipart and session asset semantics are stable, add an object-storage-backed implementation.

Preferred model:

- one shared bucket
- session-scoped prefixes
- bridge-owned storage abstraction
- cleanup by prefix on session delete
- fallback lifecycle/TTL policy in storage layer

### 12. Future optional storage references

Only after storage support is mature should the bridge consider accepting storage-backed references from clients.

This should be designed carefully to avoid over-coupling the visual builder to storage internals too early.

---

## 8. Non-goals for the first multipart V2

The first multipart V2 should **not** try to solve everything at once.

Not required in the first step:

- full generic file platform support
- S3 integration immediately
- bucket-per-session provisioning
- image moderation / OCR / deep visual policy analysis
- rich cross-service storage contracts with the visual builder

Those may come later, but should not block the first useful V2 milestone.

---

## 9. Practical decision summary

For future chats and implementation planning, the current agreed direction is:

1. keep the current V1/V1.5 multimodal base
2. implement multipart next
3. design multipart internally as extensible session asset ingestion
4. support cleanup via session deletion plus TTL/GC fallback
5. later introduce S3-compatible storage for larger and more diverse files
6. when S3 is introduced, use a shared bucket with session prefixes, not a bucket per session

This is the current project direction unless explicitly revised.

---

## 10. Suggested branch names

Possible branch names for the multipart-first phase:

- `feature/multimodal-images-v2`
- `feature/multimodal-upload-v2`
- `feature/multimodal-image-sources-v2`
- `feature/session-assets-v1`

Preferred if the design is intentionally extensible beyond images:

- `feature/session-assets-v1`
- `feature/multimodal-image-sources-v2`

---

## 11. Notes for future chats

When continuing this topic in a new chat, the assistant should be told that:

- V1/V1.5 already exists and works
- the next step is multipart, not a redesign from zero
- multipart should be implemented in an extensible way for future file classes
- S3 is considered a later storage evolution
- S3 should use a shared bucket plus session prefixes
- cleanup must include both explicit session deletion and fallback TTL/GC

