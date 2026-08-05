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
    relation_layer: Literal["main", "l2_flow", "l1_l2_flow_projection"] = "main"
    description: str
    relation_weight: float = 1.0
    source_urls: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence_basis: str | None = None
    projection_roles: list[str] = Field(default_factory=list)
    projected_from_count: int | None = None
    projected_from_edge_ids: list[str] = Field(default_factory=list)
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
    target_depth: str = "L0-L4（5 层），节点通常在 120 个以上，不设硬上限，避免低价值概念堆节点"
    use_shenwan_reference: bool = False


class AgentUpdateRequest(BaseModel):
    industry_id: str
    mode: Literal["check_only", "propose", "apply"] = "check_only"


class CompanyAttachmentRequest(BaseModel):
    industry_id: str


class L2FlowRelationRequest(BaseModel):
    industry_id: str


class CompanyAttachmentItem(BaseModel):
    company_id: str
    comcode: str
    name: str
    short_name: str = ""
    is_listed: bool | None = None
    sw_industry: dict[str, str] = Field(default_factory=dict)
    direct_node_ids: list[str] = Field(default_factory=list)
    direct_node_names: list[str] = Field(default_factory=list)
    direct_attachments: list[dict[str, Any]] = Field(default_factory=list)


class NodeCompaniesResponse(BaseModel):
    industry_id: str
    node_id: str
    status: Literal["ready", "missing", "stale", "invalid"]
    message: str = ""
    total: int = 0
    limit: int = 50
    offset: int = 0
    items: list[CompanyAttachmentItem] = Field(default_factory=list)


class NodeL2FlowRelationsResponse(BaseModel):
    industry_id: str
    node_id: str
    status: Literal["ready", "missing", "stale", "invalid"]
    message: str = ""
    total: int = 0
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class ApplyCandidateRequest(BaseModel):
    candidate_type: Literal["candidate_graph", "update_candidate_graph"]


class AgentRunResponse(BaseModel):
    run_id: str
    industry_id: str
    status: str
    report_path: str | None = None
    kind: str | None = None
    current_step: str | None = None
    command: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    returncode: int | None = None


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
    industry_node_csv: str
    industrynode_edge_csv: str
    industrynode_industry_edge_csv: str
    industrynode_node_csv: str
    company_node_csv: str | None = None
    company_edge_csv: str | None = None
    company_edge_csv_unaggregated: str | None = None
