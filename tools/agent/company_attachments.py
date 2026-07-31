from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import PROJECT_ROOT, content_hash


COMPANY_ATTACHMENT_SCHEMA_VERSION = "industry_company_attachments_v0.2"
TAXONOMY_COLUMNS = ("indunamesw", "indunamesw1", "indunamesw2", "indunamesw3")
CANDIDATE_CSV_RELATIVE_PATH = "data/company_candidates/申万全量分类结果.csv"
CANDIDATE_CSV_PATH = PROJECT_ROOT / CANDIDATE_CSV_RELATIVE_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def graph_fingerprint(graph: dict[str, Any]) -> str:
    payload = {
        "schema_version": graph.get("schema_version"),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
    }
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _read_csv_rows(path: Path) -> Iterable[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                yield from csv.DictReader(file)
            return
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error or UnicodeDecodeError("utf-8", b"", 0, 1, "Unable to decode candidate CSV")


def company_id(comcode: str) -> str:
    return f"sw_{comcode.strip()}"


def _as_bool(value: str | None) -> bool | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_candidate_companies(path: Path = CANDIDATE_CSV_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"找不到候选公司 CSV：{path}")
    unique: dict[str, dict[str, Any]] = {}
    for row in _read_csv_rows(path):
        code = (row.get("comcode") or row.get("COMCODE") or "").strip()
        name = (row.get("chiname") or row.get("NAME") or row.get("公司名称") or "").strip()
        if not code or not name or code in unique:
            continue
        unique[code] = {
            "company_id": company_id(code),
            "comcode": code,
            "name": name,
            "short_name": (row.get("chinameabbr") or "").strip(),
            "is_listed": _as_bool(row.get("islisted")),
            "is_abroad_listed": _as_bool(row.get("isabroadlisted")),
            "sw_industry": {column: (row.get(column) or "").strip() for column in TAXONOMY_COLUMNS},
        }
    return list(unique.values())


def candidate_index(companies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(company["company_id"]): company for company in companies}


def build_taxonomy_catalog(companies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    catalog: dict[str, list[dict[str, Any]]] = {}
    for column in TAXONOMY_COLUMNS:
        counts = Counter(company.get("sw_industry", {}).get(column, "") for company in companies)
        catalog[column] = [
            {"value": value, "company_count": count}
            for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            if value
        ]
    return catalog


def normalize_taxonomy_label(value: str) -> str:
    """Normalize an SW label for conservative exact matching against graph node names."""
    normalized = re.sub(r"\s+", "", str(value or "")).strip()
    normalized = re.sub(r"[ⅠⅡⅢⅣⅤⅥ]+$", "", normalized)
    return normalized.casefold()


def _taxonomy_node_index(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in graph.get("nodes", []) or []:
        if not node.get("id") or int(node.get("level", 0)) == 0:
            continue
        label = normalize_taxonomy_label(str(node.get("name", "")))
        if label:
            result[label].append(node)
    return result


def augment_scope_with_graph_taxonomy(
    scope: dict[str, Any], graph: dict[str, Any], taxonomy_catalog: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Add auditable exact rules for detailed SW labels omitted from the hierarchy columns."""
    node_index = _taxonomy_node_index(graph)
    existing = {
        (str(rule.get("column", "")), str(value))
        for rule in scope.get("rules", []) or []
        for value in rule.get("values", []) or []
    }
    values = [
        str(item["value"])
        for item in taxonomy_catalog.get("indunamesw", [])
        if normalize_taxonomy_label(str(item.get("value", ""))) in node_index
        and ("indunamesw", str(item.get("value", ""))) not in existing
    ]
    if not values:
        return scope
    completed = dict(scope)
    completed["rules"] = list(scope.get("rules", []) or []) + [
        {
            "column": "indunamesw",
            "values": values,
            "reason": "申万原始细分类与图谱非根节点标准化后精确同名，用于补全层级分类缺失的公司",
            "source": "deterministic_graph_exact_match",
        }
    ]
    completed["deterministic_scope_completion_count"] = len(values)
    return completed


def compact_node_catalog(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {str(node.get("id")): node for node in graph.get("nodes", []) if node.get("id")}
    result: list[dict[str, Any]] = []
    for node in sorted(nodes.values(), key=lambda item: (int(item.get("level", 0)), str(item.get("name", "")))):
        lineage: list[str] = []
        current = node
        visited: set[str] = set()
        while current and current.get("id") not in visited:
            visited.add(str(current.get("id")))
            lineage.append(str(current.get("name", current.get("id"))))
            parent_id = current.get("parent_id") or ""
            current = nodes.get(str(parent_id))
        result.append(
            {
                "id": node["id"],
                "name": node.get("name", ""),
                "level": int(node.get("level", 0)),
                "chain_position": node.get("chain_position", "support"),
                "path": " / ".join(reversed(lineage)),
                "description": node.get("business_description") or node.get("description") or "",
            }
        )
    return result


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for part in content or []:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                chunks.append(str(text))
    return "\n".join(chunks) if chunks else str(response)


def extract_json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise BailianAgentError("公司挂载 Agent 响应中没有 JSON 对象。")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BailianAgentError(f"公司挂载 Agent JSON 解析失败：{exc}") from exc


def build_scope_prompt(graph: dict[str, Any], taxonomy_catalog: dict[str, list[dict[str, Any]]]) -> str:
    roots = [node for node in compact_node_catalog(graph) if int(node["level"]) <= 1]
    payload = {"industry": graph.get("industry"), "level_0_and_level_1_nodes": roots, "taxonomy_catalog": taxonomy_catalog}
    return """
你是证券投研产业链公司的候选范围规划 Agent。根据行业根节点、一级产业链环节和申万分类目录，选择应纳入该行业全产业链公司挂载的申万分类。

这是候选范围过滤，不是公司业务质量评估。选择要兼顾主产业、中上游材料/包装/设备、物流/渠道等图谱已存在的分支；但不要为了覆盖而选择与该图谱无关的过宽分类。优先选择更细的 indunamesw2 或 indunamesw3；只有整个 indunamesw1 均高度相关时才使用一级分类。

仅能返回目录中存在的精确值，且 column 只能是 indunamesw、indunamesw1、indunamesw2、indunamesw3。indunamesw 是原始申万细分类，部分公司的层级分类为空时应使用它；不同规则按 OR 关系筛选公司。不要输出 Markdown。

返回严格 JSON：
{
  "summary": "一句话说明范围",
  "rules": [
    {"column": "indunamesw1", "values": ["精确分类名"], "reason": "覆盖的产业链分支"}
  ]
}

输入：
""".strip() + "\n" + json.dumps(payload, ensure_ascii=False)


def normalize_scope(scope: dict[str, Any], taxonomy_catalog: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    allowed = {column: {item["value"] for item in rows} for column, rows in taxonomy_catalog.items()}
    normalized_rules: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_rule in scope.get("rules", []) or []:
        column = str(raw_rule.get("column", "")).strip()
        if column not in allowed:
            continue
        values = []
        for value in raw_rule.get("values", []) or []:
            value = str(value).strip()
            key = (column, value)
            if value in allowed[column] and key not in seen:
                values.append(value)
                seen.add(key)
        if values:
            normalized_rules.append({"column": column, "values": values, "reason": str(raw_rule.get("reason", "")).strip()})
    return {"summary": str(scope.get("summary", "")).strip(), "rules": normalized_rules}


def select_companies_by_scope(companies: list[dict[str, Any]], scope: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for company in companies:
        industries = company.get("sw_industry", {})
        if any(industries.get(rule["column"], "") in set(rule.get("values", [])) for rule in scope.get("rules", [])):
            selected.append(company)
    return selected


def build_deterministic_taxonomy_matches(
    graph: dict[str, Any], companies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Create high-precision fallback matches when an SW label equals a graph node name."""
    node_index = _taxonomy_node_index(graph)
    results: list[dict[str, Any]] = []
    for company in companies:
        matches: dict[str, dict[str, Any]] = {}
        industries = company.get("sw_industry", {}) or {}
        for column in TAXONOMY_COLUMNS:
            taxonomy_value = str(industries.get(column, "")).strip()
            if not taxonomy_value:
                continue
            nodes = node_index.get(normalize_taxonomy_label(taxonomy_value), [])
            if not nodes:
                continue
            deepest_level = max(int(node.get("level", 0)) for node in nodes)
            for node in nodes:
                if int(node.get("level", 0)) != deepest_level:
                    continue
                node_id = str(node["id"])
                matches[node_id] = {
                    "node_id": node_id,
                    "reason": f"申万分类“{taxonomy_value}”与产业节点“{node.get('name', '')}”精确对应",
                    "confidence": 0.9,
                    "match_method": "deterministic_taxonomy_exact",
                    "taxonomy_column": column,
                    "taxonomy_value": taxonomy_value,
                }
        if matches:
            results.append({"company_id": str(company["company_id"]), "matched_nodes": list(matches.values())})
    return results


def build_match_prompt(graph: dict[str, Any], companies: list[dict[str, Any]]) -> str:
    payload = {"industry": graph.get("industry"), "nodes": compact_node_catalog(graph), "companies": companies}
    return """
你是证券投研产业链公司挂载 Agent。请联网核实每家候选公司的主营业务，再把公司直接挂到最匹配、最具体的产业链节点。

规则：
1. 只能使用输入中 company_id 和节点 id；公司名称、代码不得改写。
2. 只返回主营业务直接相关的节点；不确定、弱相关或仅有极小业务时不要挂载。
3. 同一公司可挂多个相互独立的业务节点；若同时匹配父节点和子节点，只返回更深的子节点。
4. 不得返回 level=0 根节点；没有具体匹配时 matched_nodes 返回空数组。
5. reason 使用不超过 40 个汉字说明主营业务与节点的直接关系；confidence 为 0.5 到 1.0。
6. 不要输出 Markdown 或公司/节点之外的新数据。
7. results 必须覆盖输入中的每一家、每个 company_id 恰好出现一次；确认不适合挂载时也要返回该公司，matched_nodes 为空数组。

返回严格 JSON：
{
  "results": [
    {
      "company_id": "sw_123",
      "matched_nodes": [
        {"node_id": "NODE001", "reason": "主营产品属于该细分品类", "confidence": 0.8}
      ]
    }
  ]
}

输入：
""".strip() + "\n" + json.dumps(payload, ensure_ascii=False)


def call_scope_agent(graph: dict[str, Any], companies: list[dict[str, Any]]) -> tuple[dict[str, Any], str, str]:
    catalog = build_taxonomy_catalog(companies)
    prompt = build_scope_prompt(graph, catalog)
    response = call_bailian_responses(prompt, "公司候选范围规划", use_search_tools=False)
    raw_text = _response_text(response)
    return normalize_scope(extract_json_object(raw_text), catalog), prompt, raw_text


def call_match_agent(graph: dict[str, Any], companies: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    prompt = build_match_prompt(graph, companies)
    response = call_bailian_responses(
        prompt,
        "公司节点挂载",
        use_search_tools=True,
        search_strategy=configured_search_strategy(),
        model=configured_model(),
        enable_thinking=False,
        include_web_extractor=False,
    )
    raw_text = _response_text(response)
    result = extract_json_object(raw_text)
    returned = {
        str(item.get("company_id", "")): item
        for item in result.get("results", []) or []
        if str(item.get("company_id", ""))
    }
    normalized_results = []
    for company in companies:
        identifier = str(company["company_id"])
        item = returned.get(identifier) or {}
        normalized_results.append(
            {
                "company_id": identifier,
                "matched_nodes": list(item.get("matched_nodes", []) or []),
                "result_status": "returned" if identifier in returned else "agent_omitted_filled_empty",
            }
        )
    return normalized_results, prompt, raw_text


def chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), max(1, size)):
        yield items[start : start + max(1, size)]


def configured_batch_size() -> int:
    try:
        return max(1, int(os.getenv("BAILIAN_COMPANY_ATTACH_BATCH_SIZE", "10")))
    except ValueError:
        return 10


def configured_max_concurrency() -> int:
    try:
        return max(1, int(os.getenv("BAILIAN_COMPANY_ATTACH_MAX_CONCURRENCY", "5")))
    except ValueError:
        return 5


def configured_search_strategy() -> str:
    return os.getenv("BAILIAN_COMPANY_ATTACH_SEARCH_STRATEGY", "turbo").strip() or "turbo"


def configured_model() -> str:
    model = os.getenv("BAILIAN_COMPANY_ATTACH_MODEL", "").strip()
    if not model:
        raise BailianAgentError("BAILIAN_COMPANY_ATTACH_MODEL is required for company attachment runs.")
    return model


def _parent_map(graph: dict[str, Any]) -> dict[str, str]:
    nodes = {str(node["id"]): node for node in graph.get("nodes", []) if node.get("id")}
    parents = {node_id: str(node.get("parent_id") or "") for node_id, node in nodes.items()}
    for edge in graph.get("edges", []):
        if edge.get("relation_type") == "contains" and edge.get("source") and edge.get("target"):
            target = str(edge["target"])
            if not parents.get(target):
                parents[target] = str(edge["source"])
        if edge.get("relation_type") == "upstream_downstream" and edge.get("source") and edge.get("target"):
            source, target = str(edge["source"]), str(edge["target"])
            source_level = int(nodes.get(source, {}).get("level", -1))
            target_level = int(nodes.get(target, {}).get("level", -1))
            if source_level == 0 and target_level == 1:
                parents[target] = source
            elif source_level == 1 and target_level == 0:
                parents[source] = target
    return parents


def is_ancestor(ancestor_id: str, node_id: str, parents: dict[str, str]) -> bool:
    current = parents.get(node_id, "")
    visited: set[str] = set()
    while current and current not in visited:
        if current == ancestor_id:
            return True
        visited.add(current)
        current = parents.get(current, "")
    return False


def build_attachment_payload(
    industry_id: str,
    graph: dict[str, Any],
    all_companies: list[dict[str, Any]],
    selected_companies: list[dict[str, Any]],
    scope: dict[str, Any],
    match_results: list[dict[str, Any]],
) -> dict[str, Any]:
    company_by_id = candidate_index(all_companies)
    node_by_id = {str(node["id"]): node for node in graph.get("nodes", []) if node.get("id")}
    parents = _parent_map(graph)
    matches_by_company: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    selected_ids = {str(company["company_id"]) for company in selected_companies}
    for result in match_results:
        current_company_id = str(result.get("company_id", "")).strip()
        if current_company_id not in selected_ids:
            continue
        for match in result.get("matched_nodes", []) or []:
            node_id = str(match.get("node_id", "")).strip()
            node = node_by_id.get(node_id)
            if not node or int(node.get("level", 0)) == 0:
                continue
            reason = str(match.get("reason", "")).strip()
            try:
                confidence = float(match.get("confidence", 0.75))
            except (TypeError, ValueError):
                confidence = 0.75
            matches_by_company[current_company_id][node_id] = {
                "company_id": current_company_id,
                "node_id": node_id,
                "reason": reason,
                "confidence": min(1.0, max(0.0, confidence)),
                "match_method": str(match.get("match_method") or "agent_web_search"),
            }
            for optional_key in ("taxonomy_column", "taxonomy_value"):
                if match.get(optional_key):
                    matches_by_company[current_company_id][node_id][optional_key] = str(match[optional_key])

    attachments: list[dict[str, Any]] = []
    attached_company_ids: set[str] = set()
    for current_company_id, matches in matches_by_company.items():
        node_ids = set(matches)
        retained = [node_id for node_id in node_ids if not any(other != node_id and is_ancestor(node_id, other, parents) for other in node_ids)]
        for node_id in sorted(retained):
            attachments.append(matches[node_id])
            attached_company_ids.add(current_company_id)
    companies = [company_by_id[identifier] for identifier in sorted(attached_company_ids) if identifier in company_by_id]
    method_counts = Counter(str(item.get("match_method", "unknown")) for item in attachments)
    return {
        "schema_version": COMPANY_ATTACHMENT_SCHEMA_VERSION,
        "industry_id": industry_id,
        "generated_at": now_iso(),
        "graph_fingerprint": graph_fingerprint(graph),
        "candidate_source": {
            "path": CANDIDATE_CSV_RELATIVE_PATH,
            "sha256": file_sha256(CANDIDATE_CSV_PATH),
            "total_company_count": len(all_companies),
            "selected_company_count": len(selected_companies),
        },
        "scope": scope,
        "companies": companies,
        "attachments": attachments,
        "matching_summary": {"match_method_counts": dict(sorted(method_counts.items()))},
        "unmatched_company_count": max(0, len(selected_companies) - len(attached_company_ids)),
    }


def is_listed_company(company: dict[str, Any]) -> bool:
    """Return whether the company is listed in mainland China.

    ``islisted`` is blank for roughly half of 申万全量分类结果.csv, and that blank
    marks non-listed entities rather than missing data: the listed entity always
    carries its own ``islisted=1`` row, while group parents and subsidiaries
    (华为投资控股、茅台集团、中芯国际(上海)) stay blank. ``isabroadlisted`` is
    intentionally not sufficient: the delivery scope is domestic listed companies.
    """
    return company.get("is_listed") is True


def filter_listed_attachments(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep domestic listed companies and their direct attachments only.

    Returns the filtered payload plus a statistics block. Metadata that
    ``attachment_file_status`` validates (schema_version, industry_id,
    graph_fingerprint, candidate_source, scope) is carried over untouched so the
    filtered file stays servable without re-running the attachment agent.
    """
    companies = payload.get("companies", []) or []
    attachments = payload.get("attachments", []) or []

    listed_companies = [company for company in companies if is_listed_company(company)]
    listed_ids = {str(company.get("company_id")) for company in listed_companies}
    listed_attachments = [item for item in attachments if str(item.get("company_id")) in listed_ids]

    # Only keep companies that still carry at least one attachment, mirroring how
    # build_payload assembles the company list from attached ids.
    attached_ids = {str(item.get("company_id")) for item in listed_attachments}
    retained_companies = [
        company for company in listed_companies if str(company.get("company_id")) in attached_ids
    ]

    domestic = sum(1 for company in retained_companies if company.get("is_listed") is True)
    abroad_only = sum(
        1 for company in companies
        if company.get("is_listed") is not True and company.get("is_abroad_listed") is True
    )
    removed_flag_counts = Counter(
        "is_listed=false" if company.get("is_listed") is False else "is_listed=null"
        for company in companies if not is_listed_company(company)
    )
    stats = {
        "company_count_before": len(companies),
        "company_count_after": len(retained_companies),
        "company_removed": len(companies) - len(retained_companies),
        "attachment_count_before": len(attachments),
        "attachment_count_after": len(listed_attachments),
        "attachment_removed": len(attachments) - len(listed_attachments),
        "listed_domestic_count": domestic,
        "listed_abroad_only_count": abroad_only,
        "removed_flag_counts": dict(sorted(removed_flag_counts.items())),
    }

    method_counts = Counter(str(item.get("match_method", "unknown")) for item in listed_attachments)
    filtered = {
        **payload,
        "companies": retained_companies,
        "attachments": listed_attachments,
        "matching_summary": {"match_method_counts": dict(sorted(method_counts.items()))},
        "listed_filter": {
            "applied_at": now_iso(),
            "rule": "is_listed is True (domestic listed company only)",
            **stats,
        },
    }
    return filtered, stats


def descendants_for_node(graph: dict[str, Any], node_id: str) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    parents = _parent_map(graph)
    for child_id, parent_id in parents.items():
        if parent_id:
            children[parent_id].append(child_id)
    result, queue = {node_id}, [node_id]
    while queue:
        current = queue.pop(0)
        for child_id in children.get(current, []):
            if child_id not in result:
                result.add(child_id)
                queue.append(child_id)
    return result


def attachment_file_status(industry_id: str, graph: dict[str, Any], attachment_path: Path) -> tuple[str, dict[str, Any] | None, str]:
    if not attachment_path.exists():
        return "missing", None, "尚未运行公司节点挂载。"
    try:
        payload = json.loads(attachment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid", None, "公司挂载文件无法读取。"
    if (
        payload.get("schema_version") != COMPANY_ATTACHMENT_SCHEMA_VERSION
        or not isinstance(payload.get("companies"), list)
        or not isinstance(payload.get("attachments"), list)
    ):
        return "invalid", payload, "公司挂载文件格式无效。"
    if payload.get("industry_id") != industry_id or payload.get("graph_fingerprint") != graph_fingerprint(graph):
        return "stale", payload, "正式图谱已变化，请重新运行公司节点挂载。"
    source = payload.get("candidate_source") or {}
    if not CANDIDATE_CSV_PATH.exists() or source.get("sha256") != file_sha256(CANDIDATE_CSV_PATH):
        return "stale", payload, "候选公司 CSV 已更新，请重新运行公司节点挂载。"
    return "ready", payload, ""


def aggregate_node_companies(
    graph: dict[str, Any], attachments: dict[str, Any], node_id: str, include_descendants: bool = True
) -> list[dict[str, Any]]:
    # Keep the aggregate view available for delivery and non-visual consumers;
    # the graph canvas explicitly requests direct attachments only.
    visible_node_ids = descendants_for_node(graph, node_id) if include_descendants else {node_id}
    company_by_id = {str(company.get("company_id")): company for company in attachments.get("companies", [])}
    direct_nodes: dict[str, set[str]] = defaultdict(set)
    direct_attachment_details: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in attachments.get("attachments", []) or []:
        if item.get("node_id") in visible_node_ids and item.get("company_id") in company_by_id:
            company_id = str(item["company_id"])
            node_id = str(item["node_id"])
            direct_nodes[company_id].add(node_id)
            direct_attachment_details[company_id][node_id] = {
                "node_id": node_id,
                "reason": str(item.get("reason", "")),
                "confidence": float(item.get("confidence", 0.0)),
            }
    node_names = {str(node.get("id")): str(node.get("name", node.get("id"))) for node in graph.get("nodes", [])}
    result = []
    for identifier, direct_ids in direct_nodes.items():
        company = dict(company_by_id[identifier])
        company["direct_node_ids"] = sorted(direct_ids)
        company["direct_node_names"] = [node_names.get(item, item) for item in company["direct_node_ids"]]
        company["direct_attachments"] = [
            {**direct_attachment_details[identifier][item], "node_name": node_names.get(item, item)}
            for item in company["direct_node_ids"]
        ]
        result.append(company)
    return sorted(result, key=lambda item: (str(item.get("name", "")), str(item.get("comcode", ""))))
