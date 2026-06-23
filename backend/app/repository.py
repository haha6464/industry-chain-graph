from neo4j import Driver

from app.config import Settings
from app.graph_loader import load_manifest
from app.schemas import GraphEdge, GraphFilters, GraphNode, Industry


def _edge_id(source: str, relation_type: str, target: str) -> str:
    return f"{source}__{relation_type}__{target}"


class GraphRepository:
    def __init__(self, driver: Driver, settings: Settings):
        self.driver = driver
        self.database = settings.neo4j_database

    def import_graph(
        self,
        industry_id: str,
        industry_name: str,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> dict[str, int]:
        node_payload = [node.model_dump() for node in nodes]
        edge_payload = [edge.model_dump() for edge in edges]
        records, _, _ = self.driver.execute_query(
            """
            MERGE (industry:Industry {id: $industry_id})
            SET industry.name = $industry_name,
                industry.node_count = size($nodes),
                industry.edge_count = size($edges),
                industry.status = 'demo'
            WITH industry
            UNWIND $nodes AS node
            MERGE (n:IndustryNode {id: node.id})
            SET n += node
            MERGE (industry)-[:CONTAINS]->(n)
            WITH count(n) AS imported_nodes
            UNWIND $edges AS edge
            MATCH (source:IndustryNode {id: edge.source})
            MATCH (target:IndustryNode {id: edge.target})
            CALL {
              WITH source, target, edge
              WITH source, target, edge WHERE edge.relation_type = 'contains'
              MERGE (source)-[rel:CONTAINS {industry_id: $industry_id}]->(target)
              SET rel += edge
              RETURN count(rel) AS rel_count
              UNION
              WITH source, target, edge
              WITH source, target, edge WHERE edge.relation_type = 'upstream_downstream'
              MERGE (source)-[rel:UPSTREAM_DOWNSTREAM {industry_id: $industry_id}]->(target)
              SET rel += edge
              RETURN count(rel) AS rel_count
            }
            RETURN imported_nodes AS node_count, sum(rel_count) AS edge_count
            """,
            industry_id=industry_id,
            industry_name=industry_name,
            nodes=node_payload,
            edges=edge_payload,
            database_=self.database,
        )
        if not records:
            return {"node_count": 0, "edge_count": 0}
        return {
            "node_count": records[0]["node_count"],
            "edge_count": records[0]["edge_count"],
        }

    def list_industries(self) -> list[Industry]:
        manifest_items = {item["id"]: item for item in load_manifest()}
        neo4j_items: dict[str, dict[str, object]] = {}
        try:
            records, _, _ = self.driver.execute_query(
                """
                MATCH (industry:Industry)
                RETURN industry.id AS id,
                       industry.name AS name,
                       coalesce(industry.status, 'demo') AS status,
                       coalesce(industry.node_count, 0) AS node_count,
                       coalesce(industry.edge_count, 0) AS edge_count
                """,
                database_=self.database,
            )
            neo4j_items = {record["id"]: record.data() for record in records}
        except Exception:
            neo4j_items = {}

        items: list[Industry] = []
        for manifest_item in manifest_items.values():
            merged = {
                "id": manifest_item["id"],
                "name": manifest_item.get("name", manifest_item["id"]),
                "status": manifest_item.get("status", "pending"),
                "node_count": manifest_item.get("node_count", 0),
                "edge_count": manifest_item.get("edge_count", 0),
            }
            merged.update(neo4j_items.get(manifest_item["id"], {}))
            items.append(Industry(**merged))

        for industry_id, neo4j_item in neo4j_items.items():
            if industry_id not in manifest_items:
                items.append(Industry(**neo4j_item))

        return items

    def get_graph(self, industry_id: str, filters: GraphFilters) -> tuple[list[GraphNode], list[GraphEdge]]:
        params = {
            "industry_id": industry_id,
            "q": filters.q,
            "chain_positions": filters.chain_positions,
            "relation_types": filters.relation_types,
            "levels": filters.levels,
        }
        records, _, _ = self.driver.execute_query(
            """
            MATCH (:Industry {id: $industry_id})-[:CONTAINS]->(n:IndustryNode)
            WHERE ($q IS NULL OR $q = '' OR n.name CONTAINS $q OR coalesce(n.description, '') CONTAINS $q OR coalesce(n.business_description, '') CONTAINS $q)
              AND (size($chain_positions) = 0 OR n.chain_position IN $chain_positions)
              AND (size($levels) = 0 OR n.level IN $levels)
            WITH collect(DISTINCT n) AS nodes
            UNWIND nodes AS n
            OPTIONAL MATCH (n)-[r:CONTAINS|UPSTREAM_DOWNSTREAM]->(m:IndustryNode)
            WHERE m IN nodes
              AND (size($relation_types) = 0 OR r.relation_type IN $relation_types)
            RETURN nodes AS nodes, collect(DISTINCT {
              id: r.id,
              source: startNode(r).id,
              target: endNode(r).id,
              relation_type: r.relation_type,
              description: r.description,
              relation_weight: r.relation_weight,
              source_urls: coalesce(r.source_urls, []),
              evidence_ids: coalesce(r.evidence_ids, []),
              confidence: coalesce(r.confidence, 0.0),
              updated_at: r.updated_at
            }) AS edges
            """,
            parameters_=params,
            database_=self.database,
        )
        if not records:
            return [], []

        node_values = records[0]["nodes"]
        edge_values = [
            edge for edge in records[0]["edges"]
            if edge["id"] is not None
        ]
        return self._map_nodes(node_values), self._map_edges(edge_values)

    def get_neighbors(self, node_id: str) -> tuple[list[GraphNode], list[GraphEdge]]:
        records, _, _ = self.driver.execute_query(
            """
            MATCH (center:IndustryNode {id: $node_id})
            OPTIONAL MATCH (center)-[out_rel:CONTAINS|UPSTREAM_DOWNSTREAM]->(out_node:IndustryNode)
            OPTIONAL MATCH (in_node:IndustryNode)-[in_rel:CONTAINS|UPSTREAM_DOWNSTREAM]->(center)
            WITH [center] + collect(DISTINCT out_node) + collect(DISTINCT in_node) AS raw_nodes,
                 collect(DISTINCT out_rel) + collect(DISTINCT in_rel) AS raw_edges
            RETURN [node IN raw_nodes WHERE node IS NOT NULL] AS nodes,
                   [rel IN raw_edges WHERE rel IS NOT NULL | {
                     id: rel.id,
                     source: startNode(rel).id,
                     target: endNode(rel).id,
                     relation_type: rel.relation_type,
                     description: rel.description,
                     relation_weight: rel.relation_weight,
                     source_urls: coalesce(rel.source_urls, []),
                     evidence_ids: coalesce(rel.evidence_ids, []),
                     confidence: coalesce(rel.confidence, 0.0),
                     updated_at: rel.updated_at
                   }] AS edges
            """,
            node_id=node_id,
            database_=self.database,
        )
        if not records:
            return [], []
        return self._map_nodes(records[0]["nodes"]), self._map_edges(records[0]["edges"])

    def _map_nodes(self, raw_nodes: list) -> list[GraphNode]:
        nodes = []
        seen = set()
        for raw in raw_nodes:
            data = dict(raw)
            if data["id"] in seen:
                continue
            seen.add(data["id"])
            nodes.append(
                GraphNode(
                    id=data["id"],
                    industry_id=data.get("industry_id", "food_beverage"),
                    name=data["name"],
                    node_type=data.get("node_type", "产业链环节"),
                    tags=data.get("tags", []),
                    industry=data.get("industry"),
                    level=data["level"],
                    chain_position=data["chain_position"],
                    chain_segment=data.get("chain_segment"),
                    parent_id=data.get("parent_id"),
                    description=data.get("description") or data.get("business_description", ""),
                    business_description=data.get("business_description") or data.get("description", ""),
                    is_key_node=bool(data.get("is_key_node", False)),
                    source_urls=data.get("source_urls", []),
                    evidence_ids=data.get("evidence_ids", []),
                    confidence=float(data.get("confidence", 0.0)),
                    updated_at=data.get("updated_at"),
                )
            )
        return nodes

    def _map_edges(self, raw_edges: list[dict]) -> list[GraphEdge]:
        edges = []
        seen = set()
        for data in raw_edges:
            edge_id = data.get("id") or _edge_id(data["source"], data["relation_type"], data["target"])
            if edge_id in seen:
                continue
            seen.add(edge_id)
            edges.append(
                GraphEdge(
                    id=edge_id,
                    source=data["source"],
                    target=data["target"],
                    relation_type=data["relation_type"],
                    description=data.get("description", ""),
                    relation_weight=float(data.get("relation_weight", 1.0) or 1.0),
                    source_urls=data.get("source_urls", []),
                    evidence_ids=data.get("evidence_ids", []),
                    confidence=float(data.get("confidence", 0.0) or 0.0),
                    updated_at=data.get("updated_at"),
                )
            )
        return edges

