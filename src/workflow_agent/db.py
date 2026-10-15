import psycopg2
from workflow_agent.config import settings

def connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_psw
    )
    return conn