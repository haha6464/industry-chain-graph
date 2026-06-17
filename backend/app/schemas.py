from typing import Literal

from pydantic import BaseModel, Field


ChainPosition = Literal["root", "upstream", "midstream", "downstream", "support"]
RelationType = Literal["contains", "upstream_downstream"]


class Industry(BaseModel):
    id: str
    name: str
    status: str = "demo"
    node_count: int = 0
    edge_count: int = 0


class GraphNode(BaseModel):
    id: str
    industry_id: str = "food_beverage"
    name: str
    level: int
    chain_position: ChainPosition
    parent_id: str | None = None
    description: str


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation_type: RelationType
    description: str


class GraphResponse(BaseModel):
    industry_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class GraphFilters(BaseModel):
    q: str | None = None
    chain_positions: list[ChainPosition] = Field(default_factory=list)
    relation_types: list[RelationType] = Field(default_factory=list)
    levels: list[int] = Field(default_factory=list)


class AskRequest(BaseModel):
    industry_id: str = "food_beverage"
    question: str
    filters: GraphFilters = Field(default_factory=GraphFilters)


class AskResponse(BaseModel):
    answer: str
    context_nodes: list[GraphNode]
    context_edges: list[GraphEdge]
    cypher_summary: str


class HealthResponse(BaseModel):
    api: str
    neo4j: str

