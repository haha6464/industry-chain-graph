from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.agent.bailian_client import BailianAgentError, call_bailian_responses
from tools.agent.company_attachments import _response_text, extract_json_object


def build_company_attachment_repair_prompt(payload: dict[str, Any], report: dict[str, Any]) -> str:
    errors = [item for item in report.get("issues", []) if item.get("severity") == "error"]
    compact = {
        "attachments": payload.get("attachments", []),
        "valid_company_ids": [item.get("company_id") for item in payload.get("companies", [])],
        "hard_rule_errors": errors,
    }
    return """
你是公司节点挂载的格式修复 Agent。只处理给定硬规则错误，禁止重新判断公司业务或新增公司、节点、挂载关系。

你只能从原 attachments 中保留或删除记录；保留的 company_id 和 node_id 组合必须与原记录完全一致。可补齐已有记录的空 reason，或将 confidence 规范到 0 到 1。对于未知公司/节点、L0 挂载、重复关系、祖先-后代重复关系，应删除无效或较浅的记录。

不要联网，不要输出 Markdown。

返回严格 JSON：
{
  "validation_status": "pass/needs_review/fail",
  "summary": "一句话说明",
  "attachments": [原 attachments 的合法子集],
  "modifications": [{"type": "keep/delete/update", "company_id": "", "node_id": "", "reason": ""}],
  "review_items": [{"severity": "warning/error", "item_id": "", "reason": "", "suggestion": ""}]
}

输入：
""".strip() + "\n" + json.dumps(compact, ensure_ascii=False)


def repair_company_attachments(
    payload: dict[str, Any], report: dict[str, Any], prompt_path: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = build_company_attachment_repair_prompt(payload, report)
    if prompt_path:
        prompt_path.write_text(prompt, encoding="utf-8")
    response = call_bailian_responses(
        prompt,
        "公司挂载格式修复",
        use_search_tools=False,
        enable_thinking=False,
    )
    raw_text = _response_text(response)
    result = extract_json_object(raw_text)
    original_by_pair = {
        (str(item.get("company_id", "")), str(item.get("node_id", ""))): item
        for item in payload.get("attachments", []) or []
    }
    repaired_attachments: list[dict[str, Any]] = []
    guardrail_items: list[dict[str, Any]] = []
    for item in result.get("attachments", []) or []:
        key = (str(item.get("company_id", "")), str(item.get("node_id", "")))
        original = original_by_pair.get(key)
        if original is None:
            guardrail_items.append({
                "severity": "error",
                "item_id": f"{key[0]}->{key[1]}",
                "reason": "格式修复 Agent 引入了新的公司-节点挂载。",
                "suggestion": "仅保留原始挂载的子集。",
            })
            continue
        repaired = dict(original)
        reason = str(item.get("reason", original.get("reason", ""))).strip()
        if reason:
            repaired["reason"] = reason
        try:
            repaired["confidence"] = min(1.0, max(0.0, float(item.get("confidence", original.get("confidence", 0.75)))))
        except (TypeError, ValueError):
            repaired["confidence"] = original.get("confidence", 0.75)
        repaired_attachments.append(repaired)
    repaired_payload = dict(payload)
    repaired_payload["attachments"] = repaired_attachments
    review_items = list(result.get("review_items", []) or []) + guardrail_items
    status = str(result.get("validation_status", "needs_review"))
    if any(item.get("severity") == "error" for item in review_items):
        status = "fail"
    return repaired_payload, {
        "validation_status": status,
        "summary": str(result.get("summary", "")),
        "modifications": result.get("modifications", []) or [],
        "review_items": review_items,
    }, raw_text
