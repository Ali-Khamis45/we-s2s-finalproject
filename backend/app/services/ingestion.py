"""Corpus ingestion (A9).

Walks `data/corpus`, splits documents, embeds them, and writes them into Chroma
with enough metadata that every retrieved excerpt can name its source.

Chunk identity is content-addressed: the id is a hash of the source path plus
the chunk text, so re-ingesting an unchanged corpus is idempotent and editing
one document does not duplicate the rest.

Provenance is not optional here. `data/corpus/SOURCES.md` is the committed
record of what the knowledge base contains, and the report's sources appendix
comes from it (A10). `manifest()` reports what is actually indexed so the two
can be reconciled rather than drifting.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import DependencyMissingError
from app.core.logging import get_logger, stage
from app.services.retrieval import retrieval_service

log = get_logger(__name__)

SUPPORTED = {".pdf", ".txt", ".md", ".markdown"}

#: Embedding a few hundred chunks at once is far faster than one at a time, but
#: a large batch on CPU can spike memory. This is a compromise that holds on the
#: 8 GB laptop the project targets.
BATCH = 64


@dataclass(slots=True)
class IngestReport:
    files: int = 0
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "chunks": self.chunks,
            "skipped": self.skipped,
            "sources": self.sources,
        }


def _chunk_id(source: str, text: str) -> str:
    digest = hashlib.sha256(f"{source}\x00{text}".encode()).hexdigest()
    return digest[:32]


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise DependencyMissingError("pypdf", "PDF ingestion") from exc
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def _split(text: str) -> list[str]:
    """Split into overlapping chunks at natural boundaries.

    Uses LangChain's recursive splitter when available — the brief specifies
    LangChain for RAG — and falls back to an equivalent paragraph-first split so
    ingestion still works before the full dependency set is installed.
    """
    text = text.strip()
    if not text:
        return []

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size * 4,  # splitter counts characters
            chunk_overlap=settings.chunk_overlap * 4,
            separators=["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""],
        )
        return [c.strip() for c in splitter.split_text(text) if c.strip()]
    except ImportError:
        return _fallback_split(text)


def _fallback_split(text: str) -> list[str]:
    target = settings.chunk_size * 4
    overlap = settings.chunk_overlap * 4

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
        if len(buf) + len(para) + 2 <= target:
            buf = f"{buf}\n\n{para}".strip()
            continue
        if buf:
            chunks.append(buf)
        # Carry a tail of the previous chunk so a fact split across the boundary
        # is still retrievable from either side.
        tail = buf[-overlap:] if buf and overlap else ""
        buf = f"{tail}\n\n{para}".strip() if tail else para
        while len(buf) > target:
            chunks.append(buf[:target])
            buf = buf[target - overlap :]

    if buf:
        chunks.append(buf)
    return chunks


def _title_of(path: Path, text: str) -> str:
    """Prefer a markdown H1, then a plausible first line, then the filename."""
    for line in text.splitlines()[:12]:
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()[:120]
    for line in text.splitlines()[:5]:
        line = line.strip()
        if 8 <= len(line) <= 120 and not line.endswith("."):
            return line
    return path.stem.replace("_", " ").replace("-", " ").title()


class IngestionService:
    async def ingest_directory(
        self, directory: Path | None = None, *, reset: bool = False
    ) -> IngestReport:
        root = Path(directory or settings.corpus_dir)
        report = IngestReport()

        if not root.exists():
            log.warning("corpus directory missing", extra={"path": str(root)})
            return report

        if reset:
            await retrieval_service.reset()

        files = sorted(
            p
            for p in root.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SUPPORTED
            and p.name != "SOURCES.md"
            and not p.name.startswith(".")
        )

        with stage(log, "ingest", files=len(files)) as s:
            for path in files:
                try:
                    added = await self._ingest_file(path, root)
                except Exception as exc:
                    log.warning(
                        "ingest failed", extra={"file": path.name, "reason": str(exc)}
                    )
                    report.skipped.append(f"{path.name}: {exc}")
                    continue

                if added:
                    report.files += 1
                    report.chunks += added
                    report.sources.append(path.relative_to(root).as_posix())
                else:
                    report.skipped.append(f"{path.name}: no extractable text")

            s["chunks"] = report.chunks
        return report

    async def _ingest_file(self, path: Path, root: Path) -> int:
        text = await asyncio.to_thread(_read_text, path)
        chunks = _split(text)
        if not chunks:
            return 0

        source = path.relative_to(root).as_posix()
        title = _title_of(path, text)
        collection = await retrieval_service.collection()

        total = 0
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start : start + BATCH]
            vectors = await retrieval_service.embed(batch)
            await asyncio.to_thread(
                collection.upsert,
                ids=[_chunk_id(source, c) for c in batch],
                documents=batch,
                embeddings=[v.tolist() for v in vectors],
                metadatas=[
                    {
                        "source": source,
                        "title": title,
                        "chunk_index": start + i,
                        "chars": len(c),
                    }
                    for i, c in enumerate(batch)
                ],
            )
            total += len(batch)

        log.info("ingested", extra={"file": source, "chunks": total})
        return total

    async def manifest(self) -> dict[str, Any]:
        """What is actually in the index, grouped by source.

        Reconcile against data/corpus/SOURCES.md before writing the report — a
        document indexed but unlogged has no provenance, and a document logged
        but unindexed is not backing any answer.
        """
        collection = await retrieval_service.collection()
        total = int(await asyncio.to_thread(collection.count))
        if total == 0:
            return {"chunks": 0, "sources": {}}

        raw = await asyncio.to_thread(collection.get, include=["metadatas"])
        counts: dict[str, dict[str, Any]] = {}
        for meta in raw.get("metadatas") or []:
            src = str((meta or {}).get("source", "unknown"))
            entry = counts.setdefault(src, {"chunks": 0, "title": (meta or {}).get("title")})
            entry["chunks"] += 1

        return {"chunks": total, "sources": counts}


ingestion_service = IngestionService()
