import logging
import time
from datetime import datetime

import chromadb
import httpx
from langchain_core.documents import Document

from workflow_agent.config import Config
from workflow_agent.rag.chunking import split_text
from workflow_agent.rag.embedding import ef

logger = logging.getLogger(__name__)

COLLECTION_NAME = "manual"


class IngestionPipeline:
    """Download the Workflow manual and index it in a persistent ChromaDB collection."""

    def __init__(self, config: Config):
        self.url = config.workflow_base_url
        self.token = config.workflow_token
        self.embedding_model = config.embedding_model
        logger.debug("Opening ChromaDB at %s", config.chromadb_path)
        self.db_client = chromadb.PersistentClient(path=config.chromadb_path)

    def get_from_remote(self) -> dict:
        """Fetch the manual from Workflow; returns a dict with `version` and `content` (markdown)."""
        logger.info("Downloading the manual from %s", self.url)
        with httpx.Client(base_url=self.url, headers={"Authorization": f"Token {self.token}"}, timeout=10) as client:
            response = client.get("/api/manuale/")
            response.raise_for_status()
        remote = response.json()
        logger.info(
            "Manual downloaded: version=%s, %d characters",
            remote.get("version"), len(remote.get("content", "")),
        )
        return remote

    def add(self, chunks: list[Document], version: str):
        """Create the collection tagged with `version` and upsert the chunks into it."""
        # Collection metadata (hnsw:space included) can only be set at creation
        # time, so the version is passed here instead of via collection.modify().
        collection = self.db_client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=ef,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.embedding_model,
                "ingested_at": datetime.now().isoformat(),
                "version": version,
            },
        )
        logger.info(
            "Upserting %d chunks into collection '%s' (embedding with %s)",
            len(chunks), COLLECTION_NAME, self.embedding_model,
        )
        start = time.perf_counter()
        collection.upsert(
            ids=[f"chunk-{i}" for i in range(len(chunks))],
            documents=[c.page_content for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )
        logger.info("Upsert done in %.1fs, documents in collection: %d", time.perf_counter() - start, collection.count())
        return collection

    def _is_up_to_date(self, remote_version: str) -> bool:
        """True if the local collection already holds `remote_version`; drops a stale one."""
        if not any(c.name == COLLECTION_NAME for c in self.db_client.list_collections()):
            logger.info("Collection '%s' not found: ingestion needed.", COLLECTION_NAME)
            return False
        collection = self.db_client.get_collection(COLLECTION_NAME)
        local_version = collection.metadata.get("version")
        if local_version == remote_version:
            logger.info("Collection '%s' already at version %s (%d documents).", COLLECTION_NAME, local_version, collection.count())
            return True
        logger.info(
            "Collection '%s' at version %s, remote is %s: deleting it for re-ingestion.",
            COLLECTION_NAME, local_version, remote_version,
        )
        self.db_client.delete_collection(COLLECTION_NAME)
        return False

    def run(self):
        """Ingest the manual unless the local collection is already at the remote version."""
        start = time.perf_counter()
        remote = self.get_from_remote()
        version = remote["version"]
        if self._is_up_to_date(version):
            logger.info("Ingestion skipped.")
            return
        chunks = split_text(remote["content"])
        logger.info("Text split into %d chunks", len(chunks))
        self.add(chunks, version)
        logger.info("Ingestion of version %s completed in %.1fs", version, time.perf_counter() - start)
