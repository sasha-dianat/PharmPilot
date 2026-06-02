"""
Neo4j Graph Database Client
=============================
Manages the PharmPilot knowledge graph. Uses the official neo4j Python driver
with async support. Connection pool shared across the application.

Graph model overview:
  Nodes: Drug, Patient, Prescriber, Pharmacy, Condition, Ingredient, Manufacturer
  Relationships:
    (Drug)-[:INTERACTS_WITH {severity, mechanism, evidence_grade}]->(Drug)
    (Drug)-[:CONTRAINDICATED_IN]->(Condition)
    (Drug)-[:TREATS]->(Condition)
    (Drug)-[:CONTAINS]->(Ingredient)
    (Drug)-[:METABOLIZED_BY]->(CYPEnzyme)
    (Patient)-[:TAKES {dose, freq, start_date}]->(Drug)
    (Patient)-[:HAS_CONDITION]->(Condition)
    (Patient)-[:PRESCRIBED_BY]->(Prescriber)
    (Patient)-[:FILLED_AT]->(Pharmacy)
    (Prescriber)-[:WORKS_AT]->(Clinic)
    (Ingredient)-[:SUPPLIED_BY {lot, expiry}]->(Manufacturer)
    (Manufacturer)-[:SHIPS_TO]->(Distributor)-[:DELIVERS_TO]->(Pharmacy)
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)

NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "pharmpilot_graph"


class GraphClient:
    """Async Neo4j driver wrapper with connection management."""

    def __init__(self, uri: str = NEO4J_URI, user: str = NEO4J_USER,
                 password: str = NEO4J_PASSWORD):
        self.uri = uri
        self.user = user
        self.password = password
        self._driver = None

    def connect(self):
        """Initialize the driver. Call at application startup."""
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                max_connection_pool_size=50,
            )
            self._driver.verify_connectivity()
            logger.info("Neo4j connected: %s", self.uri)
        except Exception as exc:
            logger.warning("Neo4j unavailable (%s) — graph features disabled", exc)
            self._driver = None

    def close(self):
        if self._driver:
            self._driver.close()

    @property
    def is_connected(self) -> bool:
        return self._driver is not None

    @asynccontextmanager
    async def session(self):
        """Async context manager yielding a Neo4j session."""
        if not self._driver:
            raise RuntimeError("Neo4j not connected — call connect() first")
        import asyncio
        async with self._driver.session() as session:
            yield session

    def run_sync(self, query: str, **params) -> list[dict]:
        """Run a Cypher query synchronously (for background tasks)."""
        if not self._driver:
            return []
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [dict(record) for record in result]


# Application-level singleton
_graph_client: Optional[GraphClient] = None


def get_graph_client() -> GraphClient:
    global _graph_client
    if _graph_client is None:
        _graph_client = GraphClient()
        _graph_client.connect()
    return _graph_client
