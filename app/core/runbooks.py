from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.config import Settings
from app.core.embeddings import get_embedding
from app.core.storage import CollectionStore
from app.models import Incident, RunbookDocument, RunbookSearchHit, utc_now


class RunbookRAGEngine:
    """Retrieve service runbook snippets before the LLM investigation stage."""

    def __init__(
        self,
        store: CollectionStore[RunbookDocument],
        settings: Settings,
    ) -> None:
        self.store = store
        self.settings = settings

    def ingest_directory(self, directory: Path | None = None) -> list[RunbookDocument]:
        root = directory or self.settings.runbook_dir
        if root is None or not root.exists():
            return []
        documents: list[RunbookDocument] = []
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".md", ".markdown", ".txt"} or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="utf-8", errors="ignore")
            documents.append(
                self.ingest_document(
                    title=self._title_from_content(path, content),
                    content=content,
                    source_path=str(path),
                    service=self._infer_service(path, content),
                    tags=self._infer_tags(path, content),
                )
            )
        return documents

    def ingest_document(
        self,
        *,
        title: str,
        content: str,
        service: str | None = None,
        environment: str | None = None,
        source_path: str | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> RunbookDocument:
        normalized_content = content.strip()
        checksum = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        existing = self._find_existing(source_path, checksum)
        document = RunbookDocument(
            id=existing.id if existing else hashlib.sha1((source_path or checksum).encode("utf-8")).hexdigest(),
            title=title.strip() or "Untitled runbook",
            content=normalized_content,
            service=service,
            environment=environment,
            source_path=source_path,
            tags=tags or [],
            checksum=checksum,
            embedding_vector=self._embedding(f"{title}\n{normalized_content}"),
            metadata=metadata or {},
            created_at=existing.created_at if existing else utc_now(),
            updated_at=utc_now(),
        )
        return self.store.save(document)

    def search(
        self,
        query: str,
        *,
        service: str | None = None,
        environment: str | None = None,
        limit: int = 5,
    ) -> list[RunbookSearchHit]:
        query_tokens = self._tokenize(query)
        query_vector = self._embedding(query)
        hits: list[RunbookSearchHit] = []
        for document in self.store.list():
            candidate_text = f"{document.title} {document.content} {' '.join(document.tags)} {document.service or ''}"
            candidate_tokens = self._tokenize(candidate_text)
            matched_terms = sorted(query_tokens & candidate_tokens)
            lexical_score = len(matched_terms)
            vector_score = self._vector_similarity(query_vector, document.embedding_vector)
            score = lexical_score + (vector_score * 2.0)
            if service and document.service and service.lower() == document.service.lower():
                score += 2.5
            if environment and document.environment and environment.lower() == document.environment.lower():
                score += 1.0
            if service and service.lower() in {tag.lower() for tag in document.tags}:
                score += 1.0
            if score <= 0:
                continue
            hits.append(
                RunbookSearchHit(
                    document_id=document.id,
                    title=document.title,
                    excerpt=self._excerpt(document.content, matched_terms),
                    score=round(score, 4),
                    service=document.service,
                    environment=document.environment,
                    source_path=document.source_path,
                    tags=list(document.tags),
                    matched_terms=matched_terms[:12],
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[: max(1, min(limit, 20))]

    def retrieve_for_incident(self, incident: Incident, limit: int = 5) -> list[RunbookSearchHit]:
        query = (
            f"{incident.title} {incident.description} {incident.service} {incident.environment} "
            f"{incident.severity.value} {' '.join(incident.labels)} {' '.join(incident.metrics.keys())}"
        )
        return self.search(query, service=incident.service, environment=incident.environment, limit=limit)

    def list_documents(self) -> list[RunbookDocument]:
        return sorted(self.store.list(), key=lambda item: item.updated_at, reverse=True)

    def _find_existing(self, source_path: str | None, checksum: str) -> RunbookDocument | None:
        for document in self.store.list():
            if source_path and document.source_path == source_path:
                return document
            if document.checksum == checksum:
                return document
        return None

    def _embedding(self, text: str) -> list[float]:
        return get_embedding(text, self.settings.openai_api_key, self.settings.openai_api_base, usage_source="runbook_embedding")

    @staticmethod
    def _vector_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}

    @staticmethod
    def _title_from_content(path: Path, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return path.stem.replace("-", " ").replace("_", " ").title()

    @staticmethod
    def _infer_service(path: Path, content: str) -> str | None:
        haystack = f"{path.name} {content[:500]}".lower()
        api_match = re.search(r"\b[a-z0-9]+(?:-[a-z0-9]+)*-api\b", haystack)
        if api_match:
            return api_match.group(0)
        for token in re.findall(r"\b[a-z0-9]+(?:-[a-z0-9]+)+(?:-api)?\b", haystack):
            if token in {"runbook-docs", "post-mortem"}:
                continue
            return token
        return None

    @staticmethod
    def _infer_tags(path: Path, content: str) -> list[str]:
        haystack = f"{path} {content}".lower()
        tags = []
        for token in ("latency", "5xx", "release", "rollback", "database", "queue", "memory", "cpu"):
            if token in haystack:
                tags.append(token)
        return tags

    @staticmethod
    def _excerpt(content: str, matched_terms: list[str], max_chars: int = 420) -> str:
        text = re.sub(r"\s+", " ", content).strip()
        if not text:
            return ""
        lowered = text.lower()
        indexes = [lowered.find(term.lower()) for term in matched_terms if lowered.find(term.lower()) >= 0]
        start = max(0, (min(indexes) - 80) if indexes else 0)
        excerpt = text[start : start + max_chars].strip()
        return ("..." if start > 0 else "") + excerpt + ("..." if start + max_chars < len(text) else "")
