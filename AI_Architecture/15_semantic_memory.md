# 15 — Semantic Memory Contract

## Definition

Semantic memory is the ability to find relevant information by meaning rather than by exact key lookup. It powers use cases like:
- "Find records similar to this description."
- "What did we discuss about the Smith project in previous sessions?"
- "Show me documentation that answers this question."

Semantic memory uses embeddings (vector representations of text) and a vector store to perform similarity search.

---

## When to use semantic memory

Use semantic memory when:
- The agent needs to find relevant content from a large, unstructured corpus (documents, notes, past conversations).
- Exact key-value lookup (persistent memory) is insufficient because the retrieval query is in natural language.
- The application implements a RAG (Retrieval-Augmented Generation) pattern to ground the LLM in application-specific knowledge.

Do not use semantic memory when:
- The data is structured and can be queried precisely (use queries/tools).
- The dataset is small enough to fit in the context window directly.
- The user needs an exact record (use `get_record` tool, not similarity search).

---

## Folder structure

```
ai/
└── memory/
    └── semantic.py            # Embedding adapter + vector store adapter + search
```

---

## Provider-agnostic embedding interface

```python
# ai/memory/semantic.py
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimensions(self) -> int: ...
```

Concrete adapters (OpenAI, Cohere, local sentence-transformers, etc.) implement this protocol. The rest of the semantic memory layer only imports `EmbeddingProvider`, never the provider SDK directly.

---

## Provider-agnostic vector store interface

```python
# ai/memory/semantic.py

class VectorStore(Protocol):
    def upsert(
        self,
        id: str,
        vector: list[float],
        metadata: dict,
        namespace: str,
    ) -> None: ...

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        namespace: str,
        filter: dict | None = None,
    ) -> list[SearchResult]: ...

    def delete(self, id: str, namespace: str) -> None: ...


class SearchResult:
    id: str
    score: float        # cosine similarity — 0 to 1
    metadata: dict
```

---

## Semantic memory model (DB-backed)

If you use a relational database with vector extension (e.g., pgvector with PostgreSQL), the vector store is the database itself:

```python
# models/tables/ai/semantic_memory.py
from pgvector.sqlalchemy import Vector

class SemanticMemory(Base):
    __tablename__ = "semantic_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Namespaces: "documents", "past_sessions", "records", "notes"
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)       # original text chunk
    embedding: Mapped[list[float]] = mapped_column(Vector(1536))     # dimension matches provider
    metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

If using an external vector store (Pinecone, Qdrant, Weaviate, Chroma), this table is not needed — the vector store is the source of truth and `source_id` in the metadata references the backend entity.

---

## Chunking strategy

Long documents must be split into chunks before embedding. Each chunk is embedded and stored independently.

Rules:
- Chunk size: 400–600 tokens. Smaller chunks improve precision; larger improve recall. Start at 512.
- Overlap: 50–100 tokens between adjacent chunks. Prevents losing context at chunk boundaries.
- Each chunk stores `{"source_id": "...", "chunk_index": 0, "total_chunks": 5}` in metadata.
- Chunk at paragraph or sentence boundaries where possible — never mid-sentence.

```python
# ai/memory/semantic.py

def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        start += chunk_size - overlap
    return chunks
```

---

## Indexing flow

```python
# ai/memory/semantic.py

def index_document(
    workspace_id: int,
    namespace: str,
    source_type: str,
    source_id: str,
    content: str,
    metadata: dict,
    embedder: EmbeddingProvider,
    store: VectorStore,
) -> None:
    chunks = chunk_text(content)
    vectors = embedder.embed_batch(chunks)

    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        chunk_id = f"{source_id}__chunk_{i}"
        store.upsert(
            id=chunk_id,
            vector=vector,
            metadata={
                **metadata,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "source_id": source_id,
                "chunk_index": i,
                "content": chunk,
            },
            namespace=namespace,
        )
```

---

## RAG query pattern

```python
# ai/memory/semantic.py

def semantic_search(
    query: str,
    workspace_id: int,
    namespace: str,
    top_k: int = 5,
    embedder: EmbeddingProvider,
    store: VectorStore,
) -> list[str]:
    query_vector = embedder.embed(query)
    results = store.search(
        query_vector=query_vector,
        top_k=top_k,
        namespace=namespace,
        filter={"workspace_id": workspace_id},
    )
    # Filter low-confidence results
    return [
        r.metadata["content"]
        for r in results
        if r.score >= 0.75
    ]
```

Results are injected into the agent's context as a retrieved-knowledge block:

```python
# In the agent runner, before the LLM call:
retrieved = semantic_search(query=user_message, workspace_id=..., namespace="documents", ...)
if retrieved:
    knowledge_block = "\n\n".join(retrieved)
    messages.insert(0, Message(
        role="user",
        content=f"[Retrieved knowledge — use this to answer the question]\n{knowledge_block}",
    ))
```

---

## Namespace conventions

| Namespace | Contains |
|---|---|
| `documents` | Uploaded files, knowledge base articles |
| `past_sessions` | Summarized prior agent sessions |
| `records` | Embedded record content for semantic record search |
| `notes` | Free-text user notes |

Each namespace is isolated. A search in `documents` does not return results from `past_sessions`.

---

## Score threshold

Discard results with cosine similarity below `0.75`. Below this threshold, results are unlikely to be relevant and may mislead the LLM. Make the threshold configurable per namespace — some domains need stricter thresholds.

---

## What semantic memory must NOT do

- Replace structured queries for known-key lookups (`get_record` by ID is always faster and more accurate than semantic search).
- Index data without consent (do not embed full user messages without the user opting in).
- Embed and store PII without an explicit erasure strategy (see `Backend_architecture/35_gdpr_erasure.md`).
- Return results from outside the current workspace (always filter by `workspace_id`).
- Inject an unlimited number of retrieved chunks into context — cap at `top_k` and apply the score threshold.
