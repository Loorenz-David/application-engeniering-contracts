# 30 — Multi-Modal Contract

## The three-layer separation

Multi-modal support introduces three concerns that must remain separate:

| Layer | What it covers | Who defines it |
|---|---|---|
| **Transport** | How files reach the system — upload, storage, `file_id` reference | This contract |
| **Extraction** | Converting a file into content an LLM can reason about — text, image description, page images | This contract — shared tools |
| **Domain reasoning** | What the extracted content means in this application — clauses, totals, defects, diagnoses | Each application — agent system prompt + domain tools |

The contracts provide the transport and extraction infrastructure. Each application provides the reasoning layer. An application never needs to reimplement file upload or PDF parsing — it only needs to decide what to do with the result.

---

## Core rule

**The LLM never sees a file URL or storage path. It only sees `file_id`.**

All file access is mediated through tools. Tools resolve `file_id` → content inside the tool function, after verifying workspace ownership. This keeps file access workspace-scoped, auditable, and replaceable if storage changes.

---

## `FileAttachment` model

```python
# models/tables/ai/file_attachment.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func


class FileAttachment(Base):
    __tablename__ = "file_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    # Internal storage path (S3 key, GCS path, etc.) — never exposed to the LLM
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set for PDFs after upload. Used by extract_pdf_pages_tool.
    is_temporary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # True for derived files (e.g. page images from a PDF split)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set for temporary files — cleaned up by scheduled job
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

---

## File upload flow

Files are always uploaded and stored before any agent interaction. The agent receives a `file_id`, not the file itself.

```
POST /api/v1/files
Content-Type: multipart/form-data

→ Validate: mime_type in ALLOWED_MIME_TYPES, size_bytes ≤ MAX_FILE_SIZE_BYTES
→ Store to object storage (S3, GCS, local — implementation-specific)
→ For PDFs: extract page_count, store in FileAttachment
→ Return: { file_id, file_name, mime_type, size_bytes, page_count }
```

```python
# config/default.py
ALLOWED_MIME_TYPES = [
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
    "text/plain",
]
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB per file
MAX_FILES_PER_REQUEST = 5
MAX_TOTAL_SIZE_PER_REQUEST = 50 * 1024 * 1024  # 50 MB total
```

The client stores the returned `file_id` and includes it in the next agent chat request.

---

## `Message` type — content blocks

The existing `Message` type supports `content: str`. Multi-modal extends it to support a list of typed content blocks. Plain text messages remain unchanged — `content: str` is still valid for text-only messages.

```python
# ai/providers/base.py
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ImageBlock:
    type: Literal["image"] = "image"
    file_id: str = ""        # workspace-scoped reference — resolved by tool before this point
    media_type: str = ""     # image/png, image/jpeg, image/webp, image/gif
    data: bytes = field(default_factory=bytes)
    # Raw bytes — loaded from storage by the extraction tool, then passed to the provider.
    # Never stored in the message history after the provider call completes.


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str | list[TextBlock | ImageBlock] = ""
    tool_calls: list | None = None
    tool_call_id: str | None = None
```

Provider adapters convert `ImageBlock` into the provider-specific format:
- **Anthropic**: base64-encoded data + `media_type` in the content array
- **OpenAI**: data URI (`data:<media_type>;base64,<data>`) in `image_url`
- **Google**: inline blob with `mime_type` and `data`

The agent layer never handles provider-specific image formats — that is the adapter's responsibility.

### `LLMProvider` — vision capability declaration

```python
# ai/providers/base.py

class LLMProvider(Protocol):
    def chat(self, messages, tools, config) -> LLMResponse: ...
    def stream(self, messages, tools, config) -> Iterator[LLMStreamChunk]: ...
    def count_tokens(self, text: str) -> int: ...
    def supports_vision(self) -> bool: ...
    # Returns True if the configured model can process ImageBlock content.
    # Tools check this before constructing vision messages.
```

---

## `AgentConfig` — accepted file types

```python
# ai/agents/base.py

@dataclass
class AgentConfig:
    ...
    accepted_file_types: list[str] = field(default_factory=list)
    # MIME types this agent can process. Empty = agent accepts no file input.
    # Example: ["application/pdf", "image/png", "image/jpeg"]
    # The router checks this before dispatching a file-bearing request.
```

---

## Standard extraction tools

These tools live in `ai/tools/shared/` and are available to any agent. They form the extraction layer — converting files into content the LLM can reason about.

### `extract_text_tool`

Extracts plain text from text-based documents. Works for text-based PDFs, DOCX, and TXT files. Returns empty text (`char_count == 0`) for scanned (image-only) PDFs — the agent uses this as a signal to switch to `extract_pdf_pages_tool`.

```python
# ai/tools/shared/extract_text_tool.py

SCHEMA: dict = {
    "name": "extract_text",
    "description": (
        "Extracts plain text from a file (PDF, DOCX, TXT). "
        "Returns empty text for scanned PDFs — use extract_pdf_pages in that case."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The file ID from the upload."},
            "pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional page numbers to extract (1-indexed). Omit to extract all pages.",
            },
        },
        "required": ["file_id"],
    },
}


def extract_text_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    file_ref = _resolve_file(arguments["file_id"], agent_ctx.workspace_id)
    text = _extract_text(file_ref, pages=arguments.get("pages"))
    return {
        "file_id": file_ref.file_id,
        "file_name": file_ref.file_name,
        "mime_type": file_ref.mime_type,
        "page_count": file_ref.page_count,
        "char_count": len(text),
        "text": text,   # empty string if scanned PDF
    }
```

### `describe_image_tool`

Sends an image to a vision-capable LLM and returns a structured description. Used for images and for individual pages from a scanned PDF (after `extract_pdf_pages_tool`).

```python
# ai/tools/shared/describe_image_tool.py

SCHEMA: dict = {
    "name": "describe_image",
    "description": (
        "Analyzes an image using a vision LLM. "
        "Use for PNG, JPEG, WEBP files, or for pages returned by extract_pdf_pages."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "question": {
                "type": "string",
                "description": (
                    "Specific question to answer about the image. "
                    "If omitted, returns a general structured description."
                ),
            },
        },
        "required": ["file_id"],
    },
}


def describe_image_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    provider = get_provider()
    if not provider.supports_vision():
        raise CapabilityError(
            "The current model does not support vision input. "
            "Switch to a vision-capable model or use extract_text instead."
        )

    file_ref = _resolve_file(arguments["file_id"], agent_ctx.workspace_id)
    if not file_ref.mime_type.startswith("image/"):
        raise ValidationError(f"describe_image requires an image file. Got: {file_ref.mime_type}")

    image_data = _load_file_bytes(file_ref)
    question = arguments.get("question", "Describe this image in detail.")

    response = provider.chat(
        messages=[
            Message(
                role="user",
                content=[
                    ImageBlock(
                        file_id=file_ref.file_id,
                        media_type=file_ref.mime_type,
                        data=image_data,
                    ),
                    TextBlock(text=question),
                ],
            )
        ],
        tools=[],
        config=LLMConfig(
            model=_vision_model(),
            system_prompt=(
                "You are an image analysis assistant. "
                "Provide accurate, structured descriptions. "
                "Do not infer information that is not visible in the image."
            ),
        ),
    )

    return {
        "file_id": file_ref.file_id,
        "file_name": file_ref.file_name,
        "question": question,
        "description": response.content,
    }
```

```python
# config/default.py
VISION_MODEL = "claude-sonnet-4-6"   # or gpt-4o — must support vision
```

### `extract_pdf_pages_tool`

Converts PDF pages to images. Returns a list of `file_id` references — one per page — that can then be passed to `describe_image_tool`. Used for scanned PDFs where `extract_text_tool` returns empty content.

```python
# ai/tools/shared/extract_pdf_pages_tool.py

SCHEMA: dict = {
    "name": "extract_pdf_pages",
    "description": (
        "Converts PDF pages to images. Returns a file_id for each page. "
        "Use when extract_text returns empty content (scanned PDF). "
        "Then call describe_image on individual page file_ids."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Specific page numbers to convert (1-indexed). Omit to convert all pages.",
            },
        },
        "required": ["file_id"],
    },
}


def extract_pdf_pages_tool(arguments: dict, agent_ctx: AgentContext) -> dict:
    file_ref = _resolve_file(arguments["file_id"], agent_ctx.workspace_id)
    if file_ref.mime_type != "application/pdf":
        raise ValidationError("extract_pdf_pages requires a PDF file.")

    page_numbers = arguments.get("pages") or list(range(1, (file_ref.page_count or 1) + 1))
    page_file_ids = []

    for page_num in page_numbers:
        image_bytes = _render_pdf_page(file_ref, page_num)  # PyMuPDF render
        page_file_id = _store_temporary_file(
            data=image_bytes,
            mime_type="image/png",
            file_name=f"{file_ref.file_name}_page_{page_num}.png",
            workspace_id=agent_ctx.workspace_id,
            expires_in_hours=1,
        )
        page_file_ids.append({"page": page_num, "file_id": page_file_id})

    return {
        "source_file_id": file_ref.file_id,
        "total_pages": file_ref.page_count,
        "extracted_pages": page_file_ids,
    }
```

Temporary page files are stored as `FileAttachment` records with `is_temporary=True` and `expires_at` set. A scheduled job deletes them after expiry.

---

## Extraction method by file type

| MIME type | `extract_text` | `describe_image` | `extract_pdf_pages` |
|---|---|---|---|
| `application/pdf` (text-based) | ✓ | — | ✓ for diagrams/figures |
| `application/pdf` (scanned) | ✗ empty | — | ✓ → then describe_image per page |
| `image/png`, `image/jpeg`, `image/webp` | — | ✓ | — |
| `application/vnd.openxmlformats...` (DOCX) | ✓ | — | — |
| `text/plain` | ✓ | — | — |

**Detecting a scanned PDF:** `extract_text` returns `char_count == 0`. The agent then calls `extract_pdf_pages` and processes pages with `describe_image`. This multi-step detection is what makes an extraction agent worthwhile for complex documents.

---

## Extraction agent pattern

For simple cases, including extraction tools in the domain agent is sufficient. For complex documents — scanned PDFs, mixed text/image content, multi-page reasoning — a dedicated extraction agent is cleaner. It handles the detection logic in its own tool loop and returns structured content to the calling orchestrator.

```
ai/agents/
└── contract_analysis_agent/
    ├── agent.py               # Domain reasoning — uses extracted content
    ├── system_prompt.md
    └── extractors/
        └── pdf_extractor/
            ├── agent.py       # Extraction only — text, pages, images
            └── system_prompt.md
```

The extraction agent's system prompt is focused on extraction, not domain reasoning:

```markdown
# PDF Extractor

Your job is to extract all meaningful content from a PDF file.

## Steps
1. Call extract_text. If char_count > 0, the PDF is text-based — return the text.
2. If char_count == 0, the PDF is scanned. Call extract_pdf_pages.
3. For each page returned, call describe_image with a question specific to the content type.
4. Combine all results and call extraction_complete with the structured output.

## Output
Always call extraction_complete to return structured output. Never return prose directly.
```

The extraction agent uses a dedicated output tool (`extraction_complete`) to return structured data, not prose. The calling orchestrator works with this structured output.

---

## Router extension — handling file attachments

When a chat request includes `file_ids`, the router validates them and checks `accepted_file_types` before routing:

```python
# ai/router/router.py — in IntentRouter.route()

def route(
    self,
    user_message: str,
    agent_ctx: AgentContext,
    session: ConversationSession | None = None,
    file_ids: list[str] | None = None,
) -> AgentResult:
    file_attachments = _resolve_file_attachments(file_ids, agent_ctx.workspace_id) if file_ids else []

    decision = self._classify(user_message, session, file_attachments)

    # After intent is matched — validate file types
    if file_attachments:
        intent_def = self.registry.get(decision.intent)
        unsupported = [
            f.mime_type for f in file_attachments
            if f.mime_type not in (intent_def.agent_config.accepted_file_types or [])
        ]
        if unsupported:
            return AgentResult(
                status="clarification_needed",
                clarification=ClarificationRequest(
                    question=f"This agent does not support the following file types: {unsupported}.",
                    clarification_type="intent",
                    context_gathered="File type mismatch before routing.",
                    referenced_data={"unsupported_types": unsupported},
                    suggested_answers=[],
                ),
                session_id=agent_ctx.session_id,
            )

    enriched_message = self._enrich(user_message, decision.entities, session, file_attachments)
    runner = AgentRunner(self.provider, intent_def.agent_config)
    return runner.run(enriched_message, agent_ctx)


def _enrich(self, original_message, entities, session, file_attachments) -> str:
    lines = [original_message]
    if entities:
        lines.append("\n[Pre-extracted entities]\n" + "\n".join(f"  {k}: {v}" for k, v in entities.items()))
    if file_attachments:
        lines.append("\n[Attached files]")
        for f in file_attachments:
            lines.append(f"  file_id: {f.file_id}  name: {f.file_name}  type: {f.mime_type}")
    if session:
        ctx = build_agent_context(session)
        if ctx:
            lines.append(f"\n{ctx}")
    return "\n".join(lines)
```

```python
# routers/api_v1/agent.py

@bp.route("/agents/chat", methods=["POST"])
def chat():
    user_message = request.json["message"]
    file_ids = request.json.get("file_ids", [])
    ...
    result = IntentRouter(INTENT_REGISTRY, get_provider()).route(
        user_message=user_message,
        agent_ctx=agent_ctx,
        session=session,
        file_ids=file_ids,
    )
```

---

## Conversation session — file entity tracking

When a file is processed, its reference is added to `active_entities` in the conversation session (see [28_conversation_session.md](28_conversation_session.md)):

```json
{
  "file": {
    "id": "file_abc123",
    "name": "smith_contract.pdf",
    "mime_type": "application/pdf",
    "action": "uploaded"
  }
}
```

A follow-up message like "now extract the payment clauses" resolves "the file" to `file_abc123` from the session — no re-upload required.

---

## Security

**Workspace isolation**: `_resolve_file(file_id, workspace_id)` always filters by `workspace_id`. A tool can never access a file from another workspace even if the `file_id` is guessed.

**Injection defense**: Extracted text is treated as user-supplied data. Before appending it to the message history, wrap it in structural delimiters (contract 17):

```python
# In extract_text_tool — before returning
return {
    ...
    "text": f"<extracted_content source=\"{file_ref.file_name}\">\n{text}\n</extracted_content>",
}
```

This signals to the LLM that the extracted content is data, not instructions — the same defense used for all tool results.

**Storage URL isolation**: `storage_key` is never returned by any tool. The LLM cannot construct or infer a storage URL. File access is always through `file_id` → tool → storage resolution.

**Temporary file cleanup**: A scheduled job deletes expired temporary files (page images) from both storage and the `FileAttachment` table:

```python
# services/commands/ai/cleanup_temporary_files.py
def cleanup_temporary_files(ctx: ServiceContext) -> dict:
    expired = (
        db.session.query(FileAttachment)
        .filter(
            FileAttachment.is_temporary == True,
            FileAttachment.expires_at < datetime.utcnow(),
        )
        .all()
    )
    for f in expired:
        _delete_from_storage(f.storage_key)
        db.session.delete(f)
    db.session.commit()
    return {"deleted_files": len(expired)}
```

---

## Application-specific reasoning — what each application defines

| Application | File type | Extraction tool(s) | Domain tool(s) |
|---|---|---|---|
| Contract management | PDF | `extract_text` or `extract_pdf_pages` + `describe_image` | `identify_clauses`, `flag_risks` |
| Invoice processing | PDF / image | `extract_text`, `describe_image` | `parse_line_items`, `match_vendor` |
| Property inspection | Image | `describe_image` | `classify_defect`, `estimate_severity` |
| Medical records | PDF | `extract_text` | `extract_diagnoses`, `check_drug_interactions` |

Each application:
1. Adds the relevant extraction tool(s) to the agent's `TOOLS` list.
2. Sets `accepted_file_types` in `AgentConfig`.
3. Writes the domain reasoning in the agent's `system_prompt.md`.
4. Implements domain tools that operate on extracted content.

The extraction layer is identical across all four applications. Only the domain layer differs.

---

## What multi-modal must NOT do

- Pass raw file bytes or storage URLs into the agent message or tool arguments — always use `file_id`.
- Allow one workspace's `file_id` to resolve in another workspace's tool call.
- Cache image bytes in the message history after the provider call — `ImageBlock.data` is transient.
- Send a non-vision-capable model an `ImageBlock` — check `provider.supports_vision()` first.
- Store extracted text in `AgentSessionLog` without structural wrapping — injection defense always applies.
- Keep temporary page files indefinitely — enforce `expires_at` and run the cleanup job.
- Accept file types not in `ALLOWED_MIME_TYPES` — reject at upload time, not at agent dispatch time.
- Perform domain reasoning inside extraction tools — extraction returns raw content, domain tools interpret it.
