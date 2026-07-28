from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.common import PROJECT_ROOT, now_iso
from tools.agent.search.bailian_responses_agent import _extract_json_object, _response_text

SHENWAN_SOURCE_PATH = PROJECT_ROOT / "data" / "company_candidates" / "申万全量分类结果.csv"
INDUNAMESW_TABLE_PATH = PROJECT_ROOT / "data" / "company_candidates" / "indunamesw.csv"
INDUNAMESW_COLUMNS = ("indunamesw", "indunamesw1", "indunamesw2", "indunamesw3")
SHENWAN_LEVEL_COLUMNS = ("indunamesw1", "indunamesw2", "indunamesw3")
SHENWAN_FILTER_MODEL = "qwen3.7-plus"
REQUIRED_REFERENCE_ROLES = frozenset({"core", "upstream", "downstream"})
_UNICODE_LEVEL_SUFFIX_PATTERN = re.compile(r"\s*[\u2160-\u2169]+\s*$")
_ASCII_LEVEL_SUFFIX_PATTERN = re.compile(r"(?<=[\u3400-\u9fff])\s*[IVX]{1,4}\s*$")


def normalize_indunamesw(value: str) -> str:
    """Remove the SW Roman level suffix while keeping hierarchy in separate fields."""
    without_unicode_level = _UNICODE_LEVEL_SUFFIX_PATTERN.sub("", str(value or ""))
    cleaned = unicodedata.normalize("NFKC", without_unicode_level).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return _ASCII_LEVEL_SUFFIX_PATTERN.sub("", cleaned).strip()


def _normalized_tree_path(row: dict[str, str]) -> tuple[str, ...]:
    path: list[str] = []
    for column in SHENWAN_LEVEL_COLUMNS:
        name = normalize_indunamesw(row.get(column) or "")
        if name and (not path or path[-1] != name):
            path.append(name)

    # indunamesw is the current leaf and normally duplicates indunamesw3. Use it
    # as a fallback for incomplete rows without discarding the fourth source field.
    current_leaf = normalize_indunamesw(row.get("indunamesw") or "")
    if current_leaf and len(path) < len(SHENWAN_LEVEL_COLUMNS) and (not path or path[-1] != current_leaf):
        path.append(current_leaf)
    return tuple(path)


def build_indunamesw_rows(source_path: Path = SHENWAN_SOURCE_PATH) -> list[dict[str, Any]]:
    """Build one unique root-to-leaf SW classification path per output row."""
    if not source_path.exists():
        raise FileNotFoundError(f"找不到申万全量分类结果：{source_path}")

    occurrence_count: dict[tuple[str, ...], int] = defaultdict(int)
    raw_paths: dict[tuple[str, ...], set[str]] = defaultdict(set)
    raw_leaf_names: dict[tuple[str, ...], set[str]] = defaultdict(set)
    with source_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = [column for column in INDUNAMESW_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"申万分类 CSV 缺少列：{', '.join(missing)}")
        for row in reader:
            path = _normalized_tree_path(row)
            if not path:
                continue
            occurrence_count[path] += 1
            raw_level_path = [str(row.get(column) or "").strip() for column in SHENWAN_LEVEL_COLUMNS]
            raw_paths[path].add(" > ".join(name for name in raw_level_path if name))
            for column in ("indunamesw3", "indunamesw"):
                raw_leaf = str(row.get(column) or "").strip()
                if raw_leaf:
                    raw_leaf_names[path].add(raw_leaf)

    rows: list[dict[str, Any]] = []
    for path in sorted(occurrence_count):
        padded_path = [*path, "", ""]
        rows.append(
            {
                "indunamesw": path[-1],
                "level": len(path),
                "indunamesw1": padded_path[0],
                "indunamesw2": padded_path[1],
                "indunamesw3": padded_path[2],
                "indunamesw_path": " > ".join(path),
                "raw_leaf_names": "|".join(sorted(raw_leaf_names[path])),
                "raw_paths": "|".join(sorted(raw_paths[path])),
                "occurrence_count": occurrence_count[path],
            }
        )
    return rows


def refresh_indunamesw_table(
    source_path: Path = SHENWAN_SOURCE_PATH,
    output_path: Path = INDUNAMESW_TABLE_PATH,
) -> list[dict[str, Any]]:
    rows = build_indunamesw_rows(source_path)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=(
            "indunamesw",
            "level",
            "indunamesw1",
            "indunamesw2",
            "indunamesw3",
            "indunamesw_path",
            "raw_leaf_names",
            "raw_paths",
            "occurrence_count",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != content:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8", newline="")
    return rows


def _compact_tree_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(row.get("indunamesw_path") or ""),
            "leaf_name": str(row.get("indunamesw") or ""),
            "level": int(row.get("level") or 0),
        }
        for row in rows
        if row.get("indunamesw_path")
    ]


def build_indunamesw_filter_prompt(industry_name: str, rows: Iterable[dict[str, Any]]) -> str:
    tree_paths = _compact_tree_candidates(rows)
    return f"""
你是券商行业分类研究助手。请从给定的申万行业分类树中，筛选出可能对“{industry_name}”一级产业链骨架设计有参考价值的分类路径。

注意：
1. 每项 path 按“一级 > 二级 > 三级”表达原申万等级；相邻层级同名项已折叠，例如“食品饮料 > 白酒Ⅱ > 白酒Ⅲ”规范为“食品饮料 > 白酒”。
2. 这些路径只是召回参考，不是最终交付口径。申万分类主要服务公司行业归属，可能存在层级不适合、粒度不一致、范围过窄或缺少上下游等问题。
3. 只选择输入列表中确实存在的完整 path，不得自行新增、改写或拼接路径。
4. 优先召回行业核心经营品类、重要上游供给、关键设备、渠道和下游需求分类；排除公司名、相邻行业的泛化分类及明显无关项。
5. 必须扫描整张分类树，而不是只看与目标行业同属一个申万一级根节点的路径。上下游和渠道经常归在其他申万一级行业，例如原料可能在农林牧渔，设备可能在机械，零售/电商可能在商贸零售，餐饮可能在社会服务；只要产业传导关系明确就应召回。
6. selected_categories 必须同时覆盖 core、upstream、downstream 三种 suggested_role；support 在存在关键设备、物流、检测等明确候选时也应召回。不得因为下游公司的申万归属不同而省略 downstream。
7. 宁可适度多召回供后续骨架模型判断，也不要把申万分类直接当成最终 L1；通常选择 8-30 条路径，确有必要时可少于 8 条。
8. 不需要联网，不要调用任何工具，只基于给定分类树判断。

申万分类树的唯一根到叶路径：
{json.dumps(tree_paths, ensure_ascii=False)}

请返回严格 JSON，不要 Markdown 或解释文字：
{{
  "industry": "{industry_name}",
  "selected_categories": [{{
    "path": "必须原样来自输入列表的完整 path",
    "name": "该 path 的 leaf_name",
    "reason": "与该行业一级骨架可能相关的简短理由",
    "suggested_role": "core/upstream/downstream/support"
  }}],
  "selection_notes": "整体筛选边界和使用提醒"
}}
""".strip()


def validate_indunamesw_selection(
    payload: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    required_roles: Iterable[str] = (),
) -> dict[str, Any]:
    candidate_by_path = {
        str(row.get("indunamesw_path") or ""): row
        for row in rows
        if row.get("indunamesw_path")
    }
    selected = payload.get("selected_categories")
    if not isinstance(selected, list):
        raise BailianAgentError("申万分类筛选响应缺少 selected_categories 数组")

    valid_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        candidate = candidate_by_path.get(path)
        if not candidate or path in seen:
            continue
        role = str(item.get("suggested_role") or "").strip().lower()
        if role not in {"core", "upstream", "downstream", "support"}:
            role = "core"
        valid_items.append(
            {
                "path": path,
                "name": str(candidate.get("indunamesw") or ""),
                "level": int(candidate.get("level") or 0),
                "reason": str(item.get("reason") or "").strip(),
                "suggested_role": role,
            }
        )
        seen.add(path)
    if not valid_items:
        raise BailianAgentError("申万分类筛选未返回任何输入分类树中的有效路径")
    role_counts = {
        role: sum(1 for item in valid_items if item["suggested_role"] == role)
        for role in ("core", "upstream", "downstream", "support")
    }
    missing_roles = sorted(set(required_roles) - {role for role, count in role_counts.items() if count > 0})
    if missing_roles:
        raise BailianAgentError("申万分类筛选缺少必要角色覆盖：" + ", ".join(missing_roles))
    return {
        "industry": str(payload.get("industry") or ""),
        "selected_categories": valid_items,
        "role_coverage": role_counts,
        "selection_notes": str(payload.get("selection_notes") or "").strip(),
    }


def call_bailian_indunamesw_filter(
    industry_name: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, str]:
    prompt = build_indunamesw_filter_prompt(industry_name, rows)
    response = call_bailian_responses(
        prompt,
        "申万行业分类 L1 参考筛选",
        use_search_tools=False,
        model=SHENWAN_FILTER_MODEL,
        enable_thinking=False,
        temperature=0.1,
        max_output_tokens=4000,
    )
    raw_text = _response_text(response)
    try:
        selection = validate_indunamesw_selection(
            _extract_json_object(raw_text),
            rows,
            required_roles=REQUIRED_REFERENCE_ROLES,
        )
        return selection, raw_text, prompt
    except BailianAgentError as first_error:
        correction_prompt = f"""
{prompt}

上一次筛选未通过角色覆盖校验：{first_error}
上一次响应：
{raw_text}

请重新返回完整 JSON。必须从整张申万分类树中补足缺失角色，特别检查归属于其他申万一级根节点的上游、下游渠道和终端需求路径；不得只在“{industry_name}”同根分类下选择。
""".strip()
        retry_response = call_bailian_responses(
            correction_prompt,
            "申万行业分类 L1 参考筛选覆盖修正",
            use_search_tools=False,
            model=SHENWAN_FILTER_MODEL,
            enable_thinking=False,
            temperature=0.1,
            max_output_tokens=4000,
        )
        retry_raw_text = _response_text(retry_response)
        selection = validate_indunamesw_selection(
            _extract_json_object(retry_raw_text),
            rows,
            required_roles=REQUIRED_REFERENCE_ROLES,
        )
        return selection, retry_raw_text, correction_prompt


def build_indunamesw_reference_record(industry_name: str) -> dict[str, Any]:
    rows = refresh_indunamesw_table()
    selection, raw_text, prompt = call_bailian_indunamesw_filter(industry_name, rows)
    return {
        "generated_at": now_iso(),
        "source_csv": str(SHENWAN_SOURCE_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "deduplicated_table": str(INDUNAMESW_TABLE_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "deduplicated_path_count": len(rows),
        "source_columns": list(INDUNAMESW_COLUMNS),
        "normalization": "保留 indunamesw1/2/3 等级路径；去除名称末尾的申万层级罗马数字，并折叠路径中相邻同名层级。",
        "model": SHENWAN_FILTER_MODEL,
        "enable_thinking": False,
        "search_enabled": False,
        "tools_enabled": False,
        "prompt": prompt,
        "raw_response": raw_text,
        "selection": selection,
    }


if __name__ == "__main__":
    generated_rows = refresh_indunamesw_table()
    print(f"refreshed {INDUNAMESW_TABLE_PATH} with {len(generated_rows)} unique tree paths")
