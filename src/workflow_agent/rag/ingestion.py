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
    def __init__(self, config: Config):
        self.url = config.workflow_base_url
        self.token = config.workflow_token
        self.embedding_model = config.embedding_model
        logger.debug("Apertura ChromaDB in %s", config.chromadb_path)
        self.db_client = chromadb.PersistentClient(path=config.chromadb_path)

    def get_from_remote(self) -> dict:
        logger.info("Scarico il manuale da %s", self.url)
        with httpx.Client(base_url=self.url, headers={"Authorization": f"Token {self.token}"}, timeout=10) as client:
            response = client.get("/api/manuale/")
            response.raise_for_status()
        remote = response.json()
        logger.info(
            "Manuale scaricato: versione=%s, %d caratteri",
            remote.get("version"), len(remote.get("content", "")),
        )
        return remote

    def add(self, chunks: list[Document], version: str):
        # La metadata (compreso hnsw:space) si può impostare solo alla creazione:
        # per questo la versione viene passata qui e non con collection.modify().
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
            "Upsert di %d chunk nella collection '%s' (embedding con %s)",
            len(chunks), COLLECTION_NAME, self.embedding_model,
        )
        start = time.perf_counter()
        collection.upsert(
            ids=[f"chunk-{i}" for i in range(len(chunks))],
            documents=[c.page_content for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )
        logger.info("Upsert completato in %.1fs, totale documenti: %d", time.perf_counter() - start, collection.count())
        return collection

    def _is_up_to_date(self, remote_version: str) -> bool:
        """True se esiste già una collection con la versione remota; cancella quella obsoleta."""
        if not any(c.name == COLLECTION_NAME for c in self.db_client.list_collections()):
            logger.info("Collection '%s' non trovata: serve l'ingestion.", COLLECTION_NAME)
            return False
        collection = self.db_client.get_collection(COLLECTION_NAME)
        local_version = collection.metadata.get("version")
        if local_version == remote_version:
            logger.info("Collection '%s' già alla versione %s (%d documenti).", COLLECTION_NAME, local_version, collection.count())
            return True
        logger.info(
            "Collection '%s' alla versione %s, remota %s: la cancello per re-ingestion.",
            COLLECTION_NAME, local_version, remote_version,
        )
        self.db_client.delete_collection(COLLECTION_NAME)
        return False

    def run(self):
        start = time.perf_counter()
        remote = self.get_from_remote()
        version = remote["version"]
        if self._is_up_to_date(version):
            logger.info("Ingestion saltata.")
            return
        chunks = split_text(remote["content"])
        logger.info("Testo diviso in %d chunk", len(chunks))
        self.add(chunks, version)
        logger.info("Ingestion della versione %s completata in %.1fs", version, time.perf_counter() - start)
