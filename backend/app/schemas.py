from typing import Any, Literal

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
    node_type: str = "产业链环节"
    tags: list[str] = Field(default_factory=list)
    industry: str | None = None
    level: int
    chain_position: ChainPosition
    chain_segment: str | None = None
    parent_id: str | None = None
    description: str
    business_description: str | None = None
    is_key_node: bool = False
    source_urls: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    updated_at: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation_type: RelationType
    description: str
    relation_weight: float = 1.0
    source_urls: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    updated_at: str | None = None


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


class AgentRunRequest(BaseModel):
    industry_id: str
    industry_name: str | None = None
    target_depth: str = "5-6 层"


class AgentUpdateRequest(BaseModel):
    industry_id: str
    mode: Literal["check_only", "propose", "apply"] = "check_only"


class AgentRunResponse(BaseModel):
    run_id: str
    industry_id: str
    status: str
    report_path: str | None = None


class AgentArtifact(BaseModel):
    name: str
    label: str
    kind: Literal["json", "jsonl", "markdown", "text", "csv"]
    path: str
    exists: bool
    size_bytes: int = 0
    updated_at: str | None = None


class AgentArtifactListResponse(BaseModel):
    industry_id: str
    artifacts: list[AgentArtifact]


class AgentArtifactContent(BaseModel):
    industry_id: str
    name: str
    label: str
    kind: str
    path: str
    content: Any


class ExportResponse(BaseModel):
    industry_id: str
    node_csv: str
    edge_csv: str
