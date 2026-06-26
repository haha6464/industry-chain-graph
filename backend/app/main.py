import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.agent_service import apply_candidate_graph, cancel_run, export_csv, get_run, list_agent_artifacts, list_exports, read_agent_artifact, read_report, run_build, run_search_plan, run_update, run_validate
from app.ai_service import AIConfigurationError, answer_with_graph_context
from app.config import Settings, get_settings
from app.graph_loader import load_industry_graph, load_manifest
from app.neo4j_client import Neo4jClient, get_neo4j_client
from app.repository import GraphRepository
from app.schemas import (
    AgentArtifactContent,
    AgentArtifactListResponse,
    AgentRunRequest,
    AgentRunResponse,
    ApplyCandidateRequest,
    AgentUpdateRequest,
    AskRequest,
    AskResponse,
    ChainPosition,
    ExportResponse,
    GraphFilters,
    GraphResponse,
    HealthResponse,
    Industry,
    RelationType,
)


app = FastAPI(title="Industry Chain Graph API", version="0.1.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _manifest_industries() -> list[Industry]:
    items: list[Industry] = []
    for item in load_manifest():
        node_count = int(item.get("node_count", 0))
        edge_count = int(item.get("edge_count", 0))
        status = str(item.get("status", "pending"))
        try:
            _, nodes, edges = load_industry_graph(str(item["id"]))
            node_count = len(nodes)
            edge_count = len(edges)
            if status == "pending":
                status = "ready"
        except (FileNotFoundError, ValueError):
            pass
        items.append(
            Industry(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                status=status,
                node_count=node_count,
                edge_count=edge_count,
            )
        )
    return items


def _filter_file_graph(industry_id: str, filters: GraphFilters) -> tuple[list, list]:
    try:
        _, nodes, edges = load_industry_graph(industry_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"该行业尚未生成正式 graph.json，请先到 Agent 工作流构建：{industry_id}") from exc

    selected_nodes = nodes
    if filters.q:
        q = filters.q.strip()
        selected_nodes = [
            node for node in selected_nodes
            if q in node.name or q in (node.description or "") or q in (node.business_description or "")
        ]
    if filters.chain_positions:
        selected_nodes = [node for node in selected_nodes if node.chain_position in filters.chain_positions]
    if filters.levels:
        selected_nodes = [node for node in selected_nodes if node.level in filters.levels]

    selected_ids = {node.id for node in selected_nodes}
    selected_edges = [
        edge for edge in edges
        if edge.source in selected_ids
        and edge.target in selected_ids
        and (not filters.relation_types or edge.relation_type in filters.relation_types)
    ]
    return selected_nodes, selected_edges


def _file_neighbors(industry_id: str, node_id: str) -> tuple[list, list]:
    try:
        _, nodes, edges = load_industry_graph(industry_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"该行业尚未生成正式 graph.json，请先到 Agent 工作流构建：{industry_id}") from exc

    related_edges = [edge for edge in edges if edge.source == node_id or edge.target == node_id]
    related_ids = {node_id}
    for edge in related_edges:
        related_ids.add(edge.source)
        related_ids.add(edge.target)
    related_nodes = [node for node in nodes if node.id in related_ids]
    return related_nodes, related_edges


def get_repository(client: Neo4jClient = Depends(get_neo4j_client)) -> GraphRepository:
    return GraphRepository(client.driver, get_settings())


@app.get("/api/health", response_model=HealthResponse)
def health(client: Neo4jClient = Depends(get_neo4j_client)) -> HealthResponse:
    try:
        client.verify()
        neo4j_status = "ok"
    except Exception:
        neo4j_status = "unavailable"
    return HealthResponse(api="ok", neo4j=neo4j_status)


@app.post("/api/import/food-beverage")
def import_food_beverage(repository: GraphRepository = Depends(get_repository)) -> dict[str, int | str]:
    industry_name, nodes, edges = load_industry_graph("food_beverage")
    result = repository.import_graph("food_beverage", industry_name, nodes, edges)
    return {"industry_id": "food_beverage", **result}


@app.get("/api/industries", response_model=list[Industry])
def list_industries() -> list[Industry]:
    return _manifest_industries()


@app.get("/api/graph", response_model=GraphResponse)
def get_graph(
    industry_id: str = Query(default="food_beverage"),
    q: str | None = Query(default=None),
    chain_positions: list[ChainPosition] | None = Query(default=None),
    relation_types: list[RelationType] | None = Query(default=None),
    levels: list[int] | None = Query(default=None),
) -> GraphResponse:
    filters = GraphFilters(
        q=q,
        chain_positions=chain_positions or [],
        relation_types=relation_types or [],
        levels=levels or [],
    )
    nodes, edges = _filter_file_graph(industry_id, filters)
    return GraphResponse(industry_id=industry_id, nodes=nodes, edges=edges)


@app.get("/api/nodes/{node_id}/neighbors", response_model=GraphResponse)
def get_neighbors(
    node_id: str,
    industry_id: str = Query(default="food_beverage"),
) -> GraphResponse:
    nodes, edges = _file_neighbors(industry_id, node_id)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return GraphResponse(industry_id=industry_id, nodes=nodes, edges=edges)


@app.post("/api/agent/search-plan", response_model=AgentRunResponse)
def create_agent_search_plan(request: AgentRunRequest) -> AgentRunResponse:
    result = run_search_plan(request.industry_id, request.industry_name)
    return AgentRunResponse(**result)


@app.post("/api/agent/validate", response_model=AgentRunResponse)
def validate_agent_graph(request: AgentUpdateRequest) -> AgentRunResponse:
    try:
        result = run_validate(request.industry_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentRunResponse(**result)


@app.post("/api/agent/build", response_model=AgentRunResponse)
def build_agent_graph(request: AgentRunRequest) -> AgentRunResponse:
    try:
        result = run_build(request.industry_id, request.industry_name, request.target_depth)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentRunResponse(**result)


@app.post("/api/agent/update", response_model=AgentRunResponse)
def update_agent_graph(request: AgentUpdateRequest) -> AgentRunResponse:
    try:
        result = run_update(request.industry_id, request.mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentRunResponse(**result)


@app.get("/api/agent/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(run_id: str) -> AgentRunResponse:
    try:
        return AgentRunResponse(**get_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/agent/runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel_agent_run(run_id: str) -> AgentRunResponse:
    try:
        return AgentRunResponse(**cancel_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/agent/runs/{run_id}/report")
def get_agent_report(run_id: str) -> dict[str, str]:
    try:
        run = get_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    report_path = run.get("report_path") or ""
    return {"run_id": run_id, "report_path": report_path, "content": read_report(report_path) if report_path else ""}




@app.post("/api/industries/{industry_id}/apply-candidate", response_model=AgentRunResponse)
def apply_industry_candidate(industry_id: str, request: ApplyCandidateRequest) -> AgentRunResponse:
    try:
        return AgentRunResponse(**apply_candidate_graph(industry_id, request.candidate_type))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/industries/{industry_id}/export-csv", response_model=ExportResponse)
def export_industry_csv(industry_id: str) -> ExportResponse:
    try:
        return ExportResponse(**export_csv(industry_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/industries/{industry_id}/exports")
def get_industry_exports(industry_id: str) -> dict[str, list[str]]:
    return {"industry_id": industry_id, "exports": list_exports(industry_id)}

@app.get("/api/industries/{industry_id}/agent-artifacts", response_model=AgentArtifactListResponse)
def get_agent_artifacts(industry_id: str) -> AgentArtifactListResponse:
    return AgentArtifactListResponse(industry_id=industry_id, artifacts=list_agent_artifacts(industry_id))


@app.get("/api/industries/{industry_id}/agent-artifacts/{artifact_name}", response_model=AgentArtifactContent)
def get_agent_artifact(industry_id: str, artifact_name: str) -> AgentArtifactContent:
    try:
        return AgentArtifactContent(**read_agent_artifact(industry_id, artifact_name))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@app.post("/api/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    settings: Settings = Depends(get_settings),
) -> AskResponse:
    nodes, edges = _filter_file_graph(request.industry_id, request.filters)

    if request.question.strip():
        keyword_filters = GraphFilters(q=request.question.strip())
        keyword_nodes, keyword_edges = _filter_file_graph(request.industry_id, keyword_filters)
        node_map = {node.id: node for node in nodes + keyword_nodes}
        edge_map = {edge.id: edge for edge in edges + keyword_edges}
        nodes = list(node_map.values())
        edges = list(edge_map.values())

    if not nodes:
        raise HTTPException(status_code=404, detail="未检索到可用于问答的图谱上下文。")

    try:
        return await answer_with_graph_context(settings, request.question, nodes, edges)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # type: ignore[name-defined]
        raise HTTPException(status_code=502, detail=f"AI 服务调用失败：{exc}") from exc
