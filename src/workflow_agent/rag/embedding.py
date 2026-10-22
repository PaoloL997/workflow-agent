from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from workflow_agent.config import config

# The default 60s is not enough when Ollama has to load the model before
# embedding the first batch.
TIMEOUT = 180

ef = OllamaEmbeddingFunction(
    url=config.ollama_base_url.removesuffix("/v1").rstrip("/"),
    model_name=config.embedding_model,
    timeout=TIMEOUT,
)