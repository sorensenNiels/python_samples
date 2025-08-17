from beanie import Document, init_beanie
from fastapi import Request
from pymongo import AsyncMongoClient

from .logger import logger
from .settings import settings


async def initialize_database(beanie_models: list[type[Document]]) -> AsyncMongoClient:
    """Initializes the database connection and Beanie models."""

    client: AsyncMongoClient = AsyncMongoClient(settings.mongo_dsn)
    db: str = settings.mongo_db

    # This is the line that caused the type-hinting conflict.
    # We will simply let the client object be passed directly to init_beanie.
    await init_beanie(database=client[db], document_models=beanie_models)

    logger.debug("Beanie initialized")

    # Return the client object instead of the database object.
    # This is a more consistent and robust type to work with.
    return client


async def close_database(client: AsyncMongoClient) -> None:
    """Closes the database connection."""

    logger.debug("Closing Beanie connection")

    await client.close()

    logger.debug("Beanie connection closed")


def get_database(request: Request) -> AsyncMongoClient:
    """Dependency to get the database client from app state."""

    return request.app.state
