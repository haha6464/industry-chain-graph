import httpx

from app.config import Settings
from app.schemas import AskResponse, GraphEdge, GraphNode


class AIConfigurationError(RuntimeError):
    pass


def build_context(nodes: list[GraphNode], edges: list[GraphEdge], max_nodes: int = 30, max_edges: int = 40) -> str:
    node_lines = [
        f"- [{node.id}] {node.name} | {node.chain_position} | level={node.level} | {node.description}"
        for node in nodes[:max_nodes]
    ]
    edge_lines = [
        f"- {edge.source} -[{edge.relation_type}]-> {edge.target}: {edge.description}"
        for edge in edges[:max_edges]
    ]
    return "相关节点:\n" + "\n".join(node_lines) + "\n\n相关关系:\n" + "\n".join(edge_lines)


async def answer_with_graph_context(
    settings: Settings,
    question: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> AskResponse:
    if not settings.openai_api_key:
        raise AIConfigurationError("OPENAI_API_KEY 未配置，无法调用 OpenAI-compatible 模型。")

    context = build_context(nodes, edges)
    system_prompt = (
        "你是证券公司投研场景下的产业链图谱助手。"
        "你只能基于给定图谱上下文回答问题；如果上下文不足，请明确说明不足。"
        "回答要简洁、结构化，并在回答末尾列出引用的节点 id。"
    )
    user_prompt = f"问题：{question}\n\n图谱上下文：\n{context}"

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload = response.json()

    answer = payload["choices"][0]["message"]["content"]
    return AskResponse(
        answer=answer,
        context_nodes=nodes,
        context_edges=edges,
        cypher_summary=f"GraphRAG-lite context: {len(nodes)} nodes, {len(edges)} edges.",
    )

