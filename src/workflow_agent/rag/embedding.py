from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from workflow_agent.config import config

ef = OllamaEmbeddingFunction(
    url=config.ollama_base_url.removesuffix("/v1").rstrip("/"),
    model_name=config.embedding_model
)