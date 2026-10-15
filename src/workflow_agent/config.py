from pydantic_settings  import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_psw: str

settings = Settings()