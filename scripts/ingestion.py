import logging

from workflow_agent.config import config
from workflow_agent.rag.ingestion import IngestionPipeline


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ingestion_pipeline = IngestionPipeline(config)
    ingestion_pipeline.run()


if __name__ == "__main__":
    main()