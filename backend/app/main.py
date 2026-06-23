import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.agent_service import export_csv, list_exports, read_report, run_build, run_update
from app.ai_service import AIConfigurationError, answer_with_graph_context
from app.config import Settings, get_settings
from app.graph_loader import load_industry_graph
from app.neo4j_client import Neo4jClient, get_neo4j_client
from app.repository import GraphRepository
from app.schemas import (
    AgentRunRequest,
    AgentRunResponse,
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
RUN_REPORTS: dict[str, str] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
def list_industries(repository: GraphRepository = Depends(get_repository)) -> list[Industry]:
    return repository.list_industries()


@app.get("/api/graph", response_model=GraphResponse)
def get_graph(
    industry_id: str = Query(default="food_beverage"),
    q: str | None = Query(default=None),
    chain_positions: list[ChainPosition] | None = Query(default=None),
    relation_types: list[RelationType] | None = Query(default=None),
    levels: list[int] | None = Query(default=None),
    repository: GraphRepository = Depends(get_repository),
) -> GraphResponse:
    filters = GraphFilters(
        q=q,
        chain_positions=chain_positions or [],
        relation_types=relation_types or [],
        levels=levels or [],
    )
    nodes, edges = repository.get_graph(industry_id, filters)
    return GraphResponse(industry_id=industry_id, nodes=nodes, edges=edges)


@app.get("/api/nodes/{node_id}/neighbors", response_model=GraphResponse)
def get_neighbors(
    node_id: str,
    industry_id: str = Query(default="food_beverage"),
    repository: GraphRepository = Depends(get_repository),
) -> GraphResponse:
    nodes, edges = repository.get_neighbors(node_id)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return GraphResponse(industry_id=industry_id, nodes=nodes, edges=edges)


@app.post("/api/agent/build", response_model=AgentRunResponse)
def build_agent_graph(request: AgentRunRequest) -> AgentRunResponse:
    try:
        result = run_build(request.industry_id, request.industry_name, request.target_depth)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    RUN_REPORTS[result["run_id"]] = result.get("report_path") or ""
    return AgentRunResponse(**result)


@app.post("/api/agent/update", response_model=AgentRunResponse)
def update_agent_graph(request: AgentUpdateRequest) -> AgentRunResponse:
    try:
        result = run_update(request.industry_id, request.mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    RUN_REPORTS[result["run_id"]] = result.get("report_path") or ""
    return AgentRunResponse(**result)


@app.get("/api/agent/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(run_id: str) -> AgentRunResponse:
    report_path = RUN_REPORTS.get(run_id)
    if report_path is None:
        raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}")
    return AgentRunResponse(run_id=run_id, industry_id="", status="completed", report_path=report_path)


@app.get("/api/agent/runs/{run_id}/report")
def get_agent_report(run_id: str) -> dict[str, str]:
    report_path = RUN_REPORTS.get(run_id)
    if report_path is None:
        raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}")
    return {"run_id": run_id, "report_path": report_path, "content": read_report(report_path)}


@app.post("/api/industries/{industry_id}/export-csv", response_model=ExportResponse)
def export_industry_csv(industry_id: str) -> ExportResponse:
    try:
        return ExportResponse(**export_csv(industry_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/industries/{industry_id}/exports")
def get_industry_exports(industry_id: str) -> dict[str, list[str]]:
    return {"industry_id": industry_id, "exports": list_exports(industry_id)}


@app.post("/api/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    repository: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> AskResponse:
    nodes, edges = repository.get_graph(request.industry_id, request.filters)
    if request.question.strip():
        keyword_filters = GraphFilters(q=request.question.strip())
        keyword_nodes, keyword_edges = repository.get_graph(request.industry_id, keyword_filters)
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
