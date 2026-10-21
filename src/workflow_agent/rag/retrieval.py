import chromadb

from workflow_agent.config import config
from workflow_agent.rag.embedding import ef



class ChromaDB:
    def __init__(self):
        client = chromadb.PersistentClient(config.chromadb_path)
        self.col = client.get_collection("manual", embedding_function=ef)
    
    def retrieve(self, query: str, k: int):
        return self.col.query(query_texts=[query], n_results=k)
