"""Query the manual collection indexed by `rag.ingestion`."""

import chromadb

from workflow_agent.config import config
from workflow_agent.rag.embedding import ef
from workflow_agent.rag.ingestion import COLLECTION_NAME


class ManualRetriever:
    """Semantic search over the chunks of the Workflow manual stored in ChromaDB."""

    def __init__(self) -> None:
        client = chromadb.PersistentClient(config.chromadb_path)
        self.collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)

    def retrieve(self, query: str, k: int):
        return self.collection.query(query_texts=[query], n_results=k)


_retriever: ManualRetriever | None = None


def retrieve(query: str, k: int = 3):
    """Return the `k` chunks closest to `query`, opening the collection on first use."""
    global _retriever
    if _retriever is None:
        _retriever = ManualRetriever()
    return _retriever.retrieve(query, k)
