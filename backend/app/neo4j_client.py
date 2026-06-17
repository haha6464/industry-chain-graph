from collections.abc import Generator

from neo4j import Driver, GraphDatabase

from app.config import Settings, get_settings


class Neo4jClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self) -> None:
        self.driver.close()

    def verify(self) -> None:
        self.driver.verify_connectivity()


def get_neo4j_client() -> Generator[Neo4jClient, None, None]:
    client = Neo4jClient(get_settings())
    try:
        yield client
    finally:
        client.close()

