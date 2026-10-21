# Lettura della configurazione dell'ambiente (.env)
from dotenv import load_dotenv

from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    workflow_base_url: str
    ollama_base_url: str
    llm_model: str
    embedding_model: str
    workflow_token: str
    store_path: str = "conversations.db"
    chromadb_path: str = "storage"

config = Config()