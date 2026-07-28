from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.agent.bailian_client import call_bailian_responses, load_bailian_env
from tools.agent.common import content_hash, edge_id, read_jsonl


L2_FLOW_SCHEMA_VERSION = "industry_l2_flow_relations_v0.2_pairwise"
L2_FLOW_RELATION_LAYER = "l2_flow"
PAIR_PROMPT_VERSION = "l2_pair_tri_state_v0.2"
PAIR_VERDICTS = {"A_TO_B", "B_TO_A", "NO"}
DEFAULT_L2_FLOW_MODEL = "qwen3.7-plus"
DEFAULT_PAIR_BATCH_SIZE = 20
DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_CANDIDATES_PER_NODE = 8
DEFAULT_TEMPERATURE = 0.1
DEFAULT_NEGATIVE_AUDIT_RATE = 0.03
ACCEPTED_RELATION_CONFIDENCE = 0.8


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def graph_fingerprint(graph: dict[str, Any]) -> str:
    payload = {
        "schema_version": graph.get("schema_version"),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _configured_int(name: str, default: int, minimum: int = 1) -> int:
    load_bailian_env()
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _configured_float(name: str, default: float, minimum: float, maximum: float) -> float:
    load_bailian_env()
    try:
        return min(maximum, max(minimum, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


def configured_model() -> str:
    load_bailian_env()
    return os.getenv("BAILIAN_L2_FLOW_MODEL", DEFAULT_L2_FLOW_MODEL).strip() or DEFAULT_L2_FLOW_MODEL


def configured_pair_batch_size() -> int:
    return _configured_int("BAILIAN_L2_FLOW_PAIR_BATCH_SIZE", DEFAULT_PAIR_BATCH_SIZE)


def configured_max_concurrency() -> int:
    return _configured_int("BAILIAN_L2_FLOW_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY)


def configured_candidates_per_node() -> int:
    return _configured_int("BAILIAN_L2_FLOW_CANDIDATES_PER_NODE", DEFAULT_CANDIDATES_PER_NODE)


def configured_temperature() -> float:
    return _configured_float("BAILIAN_L2_FLOW_TEMPERATURE", DEFAULT_TEMPERATURE, 0.0, 2.0)


def configured_negative_audit_rate() -> float:
    return _configured_float("BAILIAN_L2_FLOW_NEGATIVE_AUDIT_RATE", DEFAULT_NEGATIVE_AUDIT_RATE, 0.0, 0.25)


def chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    safe_size = max(1, size)
    for start in range(0, len(items), safe_size):
        yield items[start : start + safe_size]


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    chunks_: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for part in content or []:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                chunks_.append(str(text))
    return "\n".join(chunks_) if chunks_ else str(response)


def _node_lineage(node: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    current = node
    seen: set[str] = set()
    while current and str(current.get("id", "")) not in seen:
        identifier = str(current.get("id", ""))
        seen.add(identifier)
        lineage.append(current)
        current = node_by_id.get(str(current.get("parent_id") or ""))
    return list(reversed(lineage))


def compact_l2_catalog(graph: dict[str, Any]) -> list[dict[str, Any]]:
    node_by_id = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    children: dict[str, list[str]] = {}
    for node in node_by_id.values():
        parent_id = str(node.get("parent_id") or "")
        if parent_id:
            children.setdefault(parent_id, []).append(str(node["id"]))
    catalog: list[dict[str, Any]] = []
    for node in node_by_id.values():
        if int(node.get("level", -1)) != 2:
            continue
        lineage = _node_lineage(node, node_by_id)
        branch = next((item for item in lineage if int(item.get("level", -1)) == 1), {})
        descendant_names = [
            str(node_by_id[child_id].get("name") or child_id)
            for child_id in children.get(str(node["id"]), [])
            if child_id in node_by_id
        ]
        item = {
            "id": str(node["id"]),
            "name": str(node.get("name", "")),
            "path": " / ".join(str(item.get("name") or item.get("id")) for item in lineage),
            "branch_id": str(branch.get("id", "")),
            "branch_name": str(branch.get("name", "")),
            "chain_position": str(node.get("chain_position", "support")),
            "description": str(node.get("business_description") or node.get("description") or ""),
            "direct_child_names": descendant_names[:12],
        }
        item["node_content_hash"] = content_hash(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        catalog.append(item)
    return sorted(catalog, key=lambda item: (item["path"], item["id"]))


def _normalized_text(node: dict[str, Any]) -> str:
    raw = " ".join(
        [
            str(node.get("name", "")),
            str(node.get("path", "")),
            str(node.get("description", "")),
            " ".join(str(item) for item in node.get("direct_child_names", []) or []),
        ]
    ).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)


def _text_features(node: dict[str, Any]) -> set[str]:
    text = _normalized_text(node)
    features = {text[index : index + 2] for index in range(max(0, len(text) - 1))}
    features.update(text[index : index + 3] for index in range(max(0, len(text) - 2)))
    return features


def _pair_score(left: dict[str, Any], right: dict[str, Any], features: dict[str, set[str]]) -> float:
    left_features, right_features = features[left["id"]], features[right["id"]]
    overlap = len(left_features & right_features) / max(1.0, math.sqrt(len(left_features) * len(right_features)))
    left_name, right_name = str(left.get("name", "")), str(right.get("name", ""))
    left_text, right_text = _normalized_text(left), _normalized_text(right)
    direct_reference = float(bool(left_name and left_name in right_text)) + float(bool(right_name and right_name in left_text))
    position_bonus = 0.05 if left.get("chain_position") != right.get("chain_position") else 0.0
    return round(overlap + direct_reference * 2.0 + position_bonus, 6)


def pair_id(node_a_id: str, node_b_id: str) -> str:
    node_a_id, node_b_id = sorted((str(node_a_id), str(node_b_id)))
    digest = hashlib.sha256(f"{node_a_id}\0{node_b_id}".encode("utf-8")).hexdigest()[:16]
    return f"p_{digest}"


def _audit_selected(identifier: str, rate: float) -> bool:
    if rate <= 0:
        return False
    value = int(hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return value < rate


def build_candidate_pairs(
    graph: dict[str, Any],
    catalog: list[dict[str, Any]],
    candidates_per_node: int | None = None,
    negative_audit_rate: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    per_node = candidates_per_node or configured_candidates_per_node()
    audit_rate = configured_negative_audit_rate() if negative_audit_rate is None else negative_audit_rate
    node_by_id = {str(node["id"]): node for node in catalog}
    features = {node_id: _text_features(node) for node_id, node in node_by_id.items()}
    main_pairs = {
        frozenset((str(edge.get("source", "")), str(edge.get("target", ""))))
        for edge in graph.get("edges", [])
        if edge.get("source") and edge.get("target")
    }
    all_pairs: dict[str, dict[str, Any]] = {}
    for index, left in enumerate(catalog):
        for right in catalog[index + 1 :]:
            if not left.get("branch_id") or left.get("branch_id") == right.get("branch_id"):
                continue
            if frozenset((left["id"], right["id"])) in main_pairs:
                continue
            identifier = pair_id(left["id"], right["id"])
            node_a_id, node_b_id = sorted((str(left["id"]), str(right["id"])))
            all_pairs[identifier] = {
                "pair_id": identifier,
                "node_a_id": node_a_id,
                "node_b_id": node_b_id,
                "node_a_name": str(node_by_id[node_a_id].get("name", node_a_id)),
                "node_b_name": str(node_by_id[node_b_id].get("name", node_b_id)),
                "node_a_branch": str(node_by_id[node_a_id].get("branch_name", "")),
                "node_b_branch": str(node_by_id[node_b_id].get("branch_name", "")),
                "score": _pair_score(left, right, features),
                "selection_reasons": [],
            }

    incident: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_by_id}
    for pair in all_pairs.values():
        incident[pair["node_a_id"]].append(pair)
        incident[pair["node_b_id"]].append(pair)
    selected_ids: set[str] = set()
    global_slots = max(1, per_node // 2)
    for node_id, pairs in incident.items():
        ranked = sorted(pairs, key=lambda item: (-float(item["score"]), item["pair_id"]))
        selected_for_node: list[dict[str, Any]] = []
        for pair in ranked[:global_slots]:
            selected_for_node.append(pair)
            pair["selection_reasons"].append(f"{node_id}:global_similarity")
        branch_best: dict[str, dict[str, Any]] = {}
        for pair in ranked:
            other_id = pair["node_b_id"] if pair["node_a_id"] == node_id else pair["node_a_id"]
            other_branch = str(node_by_id[other_id].get("branch_id", ""))
            branch_best.setdefault(other_branch, pair)
        for pair in sorted(branch_best.values(), key=lambda item: (-float(item["score"]), item["pair_id"])):
            if len(selected_for_node) >= per_node:
                break
            if pair not in selected_for_node:
                selected_for_node.append(pair)
                pair["selection_reasons"].append(f"{node_id}:branch_diversity")
        for pair in ranked:
            if len(selected_for_node) >= per_node:
                break
            if pair not in selected_for_node:
                selected_for_node.append(pair)
                pair["selection_reasons"].append(f"{node_id}:score_fill")
        selected_ids.update(str(pair["pair_id"]) for pair in selected_for_node)

    shortlisted_count = len(selected_ids)
    audit_count = 0
    for identifier, pair in all_pairs.items():
        if identifier not in selected_ids and _audit_selected(identifier, audit_rate):
            selected_ids.add(identifier)
            pair["selection_reasons"].append("deterministic_negative_audit")
            audit_count += 1
    selected = []
    for identifier in sorted(selected_ids):
        pair = dict(all_pairs[identifier])
        pair["selection_reasons"] = sorted(set(pair["selection_reasons"]))
        selected.append(pair)
    return selected, {
        "cross_branch_pair_count": len(all_pairs),
        "shortlisted_pair_count": shortlisted_count,
        "negative_audit_pair_count": audit_count,
        "candidate_pair_count": len(selected),
        "candidates_per_node": per_node,
        "negative_audit_rate": audit_rate,
    }


def build_pair_prompt(catalog_by_id: dict[str, dict[str, Any]], pairs: list[dict[str, Any]]) -> str:
    pair_inputs = []
    for pair in pairs:
        node_a = catalog_by_id[pair["node_a_id"]]
        node_b = catalog_by_id[pair["node_b_id"]]
        fields = ("id", "name", "path", "branch_name", "chain_position", "description", "direct_child_names")
        pair_inputs.append(
            {
                "pair_id": pair["pair_id"],
                "A": {field: node_a.get(field) for field in fields},
                "B": {field: node_b.get(field) for field in fields},
            }
        )
    expected = "\n".join(str(item["pair_id"]) for item in pair_inputs)
    return f"""
你是证券投研产业链 L2 节点对判定器。每个 pair 都是完全独立的判断，只能依据该 pair 中 A、B 两个节点的名称、分类路径、描述和直接子类判断，不得参考其他 pair。

本次调用不联网、不开启思考、不使用任何工具。

判定标准：
- A_TO_B：A 是 B 的稳定、直接上游，向 B 提供主要原料、材料、设备、包装、物流/渠道承接或其他直接投入。
- B_TO_A：B 是 A 的稳定、直接上游。
- NO：仅同属一个行业、弱相关、间接关系、可能关系、同义/分类关系、连续工艺步骤，或证据不足。

严格要求：
1. 每个 pair_id 必须恰好输出一行。
2. 每行只能是 `pair_id:A_TO_B`、`pair_id:B_TO_A` 或 `pair_id:NO`。
3. 不要解释，不要 Markdown，不要 JSON，不要输出其他文字。

必须逐行覆盖以下 pair_id，并为每个 ID 选择一个三态结果：
{expected}

节点对：
{json.dumps(pair_inputs, ensure_ascii=False)}
""".strip()


def parse_pair_verdicts(text: str, expected_pair_ids: set[str]) -> dict[str, str]:
    pattern = re.compile(r"^\s*(p_[0-9a-f]{16})\s*:\s*(A_TO_B|B_TO_A|NO)\s*$", re.IGNORECASE)
    parsed: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in str(text).replace("```", "").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        identifier, verdict = match.group(1).lower(), match.group(2).upper()
        if identifier not in expected_pair_ids:
            continue
        if identifier in parsed:
            duplicates.add(identifier)
        parsed[identifier] = verdict
    for identifier in duplicates:
        parsed.pop(identifier, None)
    return parsed


def call_pair_batch(
    catalog_by_id: dict[str, dict[str, Any]], pairs: list[dict[str, Any]]
) -> tuple[dict[str, str], str, str]:
    prompt = build_pair_prompt(catalog_by_id, pairs)
    response = call_bailian_responses(
        prompt,
        "L2 节点对判定",
        use_search_tools=False,
        model=configured_model(),
        enable_thinking=False,
        include_web_extractor=False,
        temperature=configured_temperature(),
        max_output_tokens=max(64, len(pairs) * 24),
    )
    raw_text = _response_text(response)
    expected_ids = {str(pair["pair_id"]) for pair in pairs}
    return parse_pair_verdicts(raw_text, expected_ids), prompt, raw_text


def decision_cache_key(pair: dict[str, Any], catalog_by_id: dict[str, dict[str, Any]]) -> str:
    payload = {
        "prompt_version": PAIR_PROMPT_VERSION,
        "model": configured_model(),
        "temperature": configured_temperature(),
        "node_a_id": pair["node_a_id"],
        "node_b_id": pair["node_b_id"],
        "node_a_hash": catalog_by_id[pair["node_a_id"]]["node_content_hash"],
        "node_b_hash": catalog_by_id[pair["node_b_id"]]["node_content_hash"],
    }
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def load_pair_decision_cache(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    return {
        str(row.get("cache_key")): row
        for row in rows
        if row.get("cache_key") and row.get("verdict") in PAIR_VERDICTS
    }


def build_payload(
    industry_id: str,
    graph: dict[str, Any],
    catalog: list[dict[str, Any]],
    candidate_pairs: list[dict[str, Any]],
    pair_decisions: list[dict[str, Any]],
    candidate_summary: dict[str, int | float],
) -> dict[str, Any]:
    graph_node_by_id = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    pair_by_id = {str(pair["pair_id"]): pair for pair in candidate_pairs}
    edges: list[dict[str, Any]] = []
    normalized_decisions: list[dict[str, Any]] = []
    for decision in sorted(pair_decisions, key=lambda item: str(item.get("pair_id", ""))):
        identifier = str(decision.get("pair_id", ""))
        pair = pair_by_id.get(identifier)
        if pair is None:
            continue
        verdict = str(decision.get("verdict", "INVALID")).upper()
        normalized = {
            "pair_id": identifier,
            "node_a_id": pair["node_a_id"],
            "node_b_id": pair["node_b_id"],
            "verdict": verdict,
            "decision_source": str(decision.get("decision_source", "model")),
            "cache_key": str(decision.get("cache_key", "")),
            "selection_reasons": list(pair.get("selection_reasons", [])),
        }
        normalized_decisions.append(normalized)
        if verdict not in {"A_TO_B", "B_TO_A"}:
            continue
        source = pair["node_a_id"] if verdict == "A_TO_B" else pair["node_b_id"]
        target = pair["node_b_id"] if verdict == "A_TO_B" else pair["node_a_id"]
        source_node, target_node = graph_node_by_id[source], graph_node_by_id[target]
        source_urls = sorted(set(source_node.get("source_urls", [])) | set(target_node.get("source_urls", [])))
        evidence_ids = sorted(set(source_node.get("evidence_ids", [])) | set(target_node.get("evidence_ids", [])))
        edges.append(
            {
                "id": edge_id(source, "upstream_downstream", target),
                "source": source,
                "target": target,
                "relation_type": "upstream_downstream",
                "relation_layer": L2_FLOW_RELATION_LAYER,
                "relation_weight": 1.0,
                "description": f"{source_node.get('name', source)}是{target_node.get('name', target)}的直接上游投入或供给环节。",
                "source_urls": source_urls,
                "evidence_ids": evidence_ids,
                "confidence": ACCEPTED_RELATION_CONFIDENCE,
                "evidence_basis": "endpoint_graph_sources_and_pairwise_model_decision",
                "decision_pair_id": identifier,
                "updated_at": now_iso(),
            }
        )
    verdict_counts = Counter(item["verdict"] for item in normalized_decisions)
    audit_pair_ids = {
        str(pair["pair_id"])
        for pair in candidate_pairs
        if "deterministic_negative_audit" in pair.get("selection_reasons", [])
    }
    audit_positive_count = sum(
        item["pair_id"] in audit_pair_ids and item["verdict"] in {"A_TO_B", "B_TO_A"}
        for item in normalized_decisions
    )
    cache_hit_count = sum(item["decision_source"] == "cache" for item in normalized_decisions)
    return {
        "schema_version": L2_FLOW_SCHEMA_VERSION,
        "industry_id": industry_id,
        "industry": graph.get("industry", industry_id),
        "generated_at": now_iso(),
        "graph_fingerprint": graph_fingerprint(graph),
        "generation_config": {
            "model": configured_model(),
            "prompt_version": PAIR_PROMPT_VERSION,
            "decision_mode": "independent_pair_tri_state",
            "temperature": configured_temperature(),
            "pair_batch_size": configured_pair_batch_size(),
            "candidates_per_node": configured_candidates_per_node(),
            "negative_audit_rate": configured_negative_audit_rate(),
            "web_search": False,
            "thinking": False,
            "tools": [],
        },
        "evaluated_node_ids": sorted(str(node["id"]) for node in catalog),
        "candidate_summary": {
            **candidate_summary,
            "pair_decision_count": len(normalized_decisions),
            "cache_hit_count": cache_hit_count,
            "model_decision_count": len(normalized_decisions) - cache_hit_count,
            "negative_audit_positive_count": audit_positive_count,
            "verdict_counts": dict(sorted(verdict_counts.items())),
        },
        "pair_decisions": normalized_decisions,
        "edges": sorted(edges, key=lambda item: (item["source"], item["target"])),
    }


def relation_file_status(
    industry_id: str, graph: dict[str, Any], relation_path: Path
) -> tuple[str, dict[str, Any] | None, str]:
    if not relation_path.exists():
        return "missing", None, "尚未运行 L2 上下游关系建边。"
    try:
        payload = json.loads(relation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid", None, "L2 上下游关系文件无法读取。"
    if payload.get("schema_version") != L2_FLOW_SCHEMA_VERSION or not isinstance(payload.get("edges"), list):
        return "invalid", payload, "L2 上下游关系文件格式无效。"
    if payload.get("industry_id") != industry_id or payload.get("graph_fingerprint") != graph_fingerprint(graph):
        return "stale", payload, "正式图谱已变化，请重新运行 L2 上下游关系建边。"
    return "ready", payload, ""


def payload_fingerprint(payload: dict[str, Any]) -> str:
    stable = {
        "schema_version": payload.get("schema_version"),
        "industry_id": payload.get("industry_id"),
        "graph_fingerprint": payload.get("graph_fingerprint"),
        "edges": payload.get("edges", []),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
