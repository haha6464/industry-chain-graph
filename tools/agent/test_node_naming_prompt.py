from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

if __package__ is None or __package__ == "":
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "data" / "industries" / "manifest.json").exists():
            sys.path.insert(0, str(parent))
            break

from tools.agent.bailian_client import call_bailian_responses
from tools.agent.common import PROJECT_ROOT, write_json

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

DEFAULT_CANDIDATE_NODES = [
    {"id": "CAND001", "name": "动力电池", "parent": "新能源汽车产业链"},
    {"id": "CAND002", "name": "动力电池热管理系统", "parent": "动力电池"},
    {"id": "CAND003", "name": "电池产线设备", "parent": "锂电设备"},
    {"id": "CAND004", "name": "正极材料", "parent": "电池材料"},
    {"id": "CAND005", "name": "固态电解质材料", "parent": "固态电池"},
    {"id": "CAND006", "name": "高压电池包", "parent": "动力电池Pack"},
    {"id": "CAND007", "name": "BMS", "parent": "电池管理系统"},
    {"id": "CAND008", "name": "车机系统", "parent": "智能座舱"},
    {"id": "CAND009", "name": "智能驾驶系统", "parent": "智能驾驶"},
    {"id": "CAND010", "name": "智驾芯片", "parent": "汽车电子"},
    {"id": "CAND011", "name": "电机零部件", "parent": "电驱系统"},
    {"id": "CAND012", "name": "检测认证服务", "parent": "支撑服务"},
    {"id": "CAND013", "name": "CoWoS先进封装", "parent": "先进封装"},
    {"id": "CAND014", "name": "TCB设备", "parent": "半导体封装设备"},
    {"id": "CAND015", "name": "半导体设备零部件", "parent": "半导体设备"},
    {"id": "CAND016", "name": "半导体陶瓷零部件", "parent": "半导体设备零部件"},
    {"id": "CAND017", "name": "光模块", "parent": "光通信"},
    {"id": "CAND018", "name": "AI服务器", "parent": "算力基础设施"},
]


def _cell_column(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", NS):
        texts = [text.text or "" for text in item.findall(".//main:t", NS)]
        values.append("".join(texts))
    return values


def _first_sheet_path(archive: zipfile.ZipFile, sheet_name: str | None) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("pkgrel:Relationship", NS)
    }
    sheets = workbook.findall("main:sheets/main:sheet", NS)
    if not sheets:
        raise ValueError("Workbook does not contain sheets.")

    selected = sheets[0]
    if sheet_name:
        for sheet in sheets:
            if sheet.attrib.get("name") == sheet_name:
                selected = sheet
                break
        else:
            available = [sheet.attrib.get("name", "") for sheet in sheets]
            raise ValueError(f"Sheet {sheet_name!r} not found. Available sheets: {available}")

    rel_id = selected.attrib[f"{{{NS['rel']}}}id"]
    target = rel_targets[rel_id].lstrip("/")
    if not target.startswith("xl/"):
        target = f"xl/{target}"
    return target.replace("\\", "/")


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("main:v", NS)
    if cell_type == "s":
        if value is None or value.text is None:
            return ""
        return shared_strings[int(value.text)]
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//main:t", NS))
    return value.text if value is not None and value.text is not None else ""


def read_xlsx_rows(path: Path, sheet_name: str | None, limit: int) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_path = _first_sheet_path(archive, sheet_name)
        root = ET.fromstring(archive.read(sheet_path))

    rows: list[list[str]] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        values: list[str] = []
        for cell in row.findall("main:c", NS):
            column = _cell_column(cell.attrib.get("r", "A1"))
            while len(values) <= column:
                values.append("")
            values[column] = _cell_value(cell, shared_strings).strip()
        rows.append(values)

    if not rows:
        return []
    headers = rows[0]
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        record = {
            headers[index]: row[index] if index < len(row) else ""
            for index in range(len(headers))
            if headers[index]
        }
        if record.get("node_name"):
            records.append(record)
        if len(records) >= limit:
            break
    return records


def build_prompt(records: list[dict[str, str]], candidate_nodes: list[dict[str, str]]) -> str:
    return f"""
你是证券研究场景的产业链节点命名评估员。请根据候选节点和新闻抽取文本，判断每条文本应匹配已有节点、生成新节点、不要新增节点，还是进入人工复核。

产业链节点定义：
- 节点必须是稳定存在、可用于投研分析的产业分类单元，能服务于成本拆解、供需分析、上下游传导、公司业务归因或产品/材料/设备/工艺/应用场景比较。
- 优先认可：上游资源/原材料/关键材料、核心零部件/模组/系统、生产制造/集成环节、关键工艺/技术路线、专用设备/检测设备、产品/服务形态、下游应用/需求场景、必要渠道/物流/检测/认证/运维等支撑环节。
- 不要把公司/品牌/股票/财务指标、估值、利润、收入、毛利率、订单增长、出货量提升、市场份额变化、产能利用率变化、价格变化、景气度变化、竞争格局变化、融资并购、建厂意愿、政策跟进、新闻事件、市场情绪作为节点。

命名规则：
- 必须先从文本中剥离事件/指标词，抽取稳定产业对象；再判断是否匹配候选节点或需要新增节点。
- 如果剥离事件/指标词后能得到稳定产业对象，且该对象能明显归入候选节点，返回 match_existing，并给出 matched_node_id 和 matched_node_name。不要因为原句包含“订单增长/需求提升/竞争格局/市占率”等指标词就判为 no_new_node。
- 如果剥离后得到的稳定产业对象没有被候选节点覆盖，但可挂入产业链，返回 generate_new，并生成 2-10 个字左右的行业名词短语。
- 只有当剥离公司名、事件词和指标词后，剩余内容不再是稳定产业对象时，才返回 no_new_node。
- 生成新节点时，删除公司名、品牌名，以及“厂商/企业/供应商/服务商/集成商、订单、收入、利润、份额、市占率、估值、产能利用率、出货量、需求提升、竞争格局、增长、下降、改善、承压、放量、加速、分化、重塑”等事件或指标词。
- 如果需要研究员判断粒度或是否与已有节点重复，返回 needs_review。

判断示例：
- “隆盛科技新能源业务收入占比提升” -> no_new_node，因为剥离公司和收入指标后没有具体产业对象。
- “电机零部件行业竞争格局重塑” -> match_existing: 电机零部件。
- “动力电池热管理系统集成商订单需求提升” -> match_existing: 动力电池热管理系统。
- “航盛电子车机系统订单增长” -> match_existing: 车机系统。
- “同类定转子供应商市场份额下降” -> generate_new: 定转子，除非候选节点已有电机定转子/电机零部件且可归入。
- “德赛西威/华阳集团等估值承压” -> no_new_node。

候选节点：
{json.dumps(candidate_nodes, ensure_ascii=False, indent=2)}

待判断文本：
{json.dumps(records, ensure_ascii=False, indent=2)}

请只返回严格 JSON，不要 Markdown 或解释文字。格式如下：
{{
  "items": [
    {{
      "event_id": "原 event_id",
      "node_target": "原 node_name",
      "decision": "match_existing/generate_new/no_new_node/needs_review",
      "matched_node_id": "如匹配已有节点则填写，否则为空",
      "matched_node_name": "如匹配已有节点则填写，否则为空",
      "generated_node_name": "如生成新节点则填写，否则为空",
      "confidence": 0.0,
      "reason": "一句话说明"
    }}
  ]
}}
""".strip()


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        content = getattr(item, "content", None)
        if isinstance(item, dict):
            content = item.get("content")
        for part in content or []:
            text = getattr(part, "text", None)
            if isinstance(part, dict):
                text = part.get("text")
            if text:
                chunks.append(str(text))
    if chunks:
        return "\n".join(chunks)
    if hasattr(response, "model_dump_json"):
        return response.model_dump_json(indent=2)
    return str(response)


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response did not contain a JSON object.")
    return json.loads(text[start : end + 1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Bailian node naming prompt on extracted news text.")
    parser.add_argument(
        "--xlsx",
        default=str(PROJECT_ROOT.parent / "test_node.xlsx"),
        help="Path to the Excel file containing event_id and node_name columns.",
    )
    parser.add_argument("--sheet", default="Sheet0", help="Worksheet name to read.")
    parser.add_argument("--limit", type=int, default=10, help="Number of rows to test.")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "node_naming_test_result.json"),
        help="Where to write the parsed JSON result plus prompt metadata.",
    )
    parser.add_argument(
        "--prompt-output",
        default=str(PROJECT_ROOT / "data" / "node_naming_test_prompt.txt"),
        help="Where to write the exact request prompt.",
    )
    parser.add_argument("--no-call", action="store_true", help="Only write the prompt, do not call Bailian.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = read_xlsx_rows(Path(args.xlsx), args.sheet, args.limit)
    if not records:
        raise ValueError(f"No records with node_name found in {args.xlsx}.")

    prompt = build_prompt(records, DEFAULT_CANDIDATE_NODES)
    prompt_path = Path(args.prompt_output)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"[agent] 已写入测试 prompt：{prompt_path}", flush=True)

    if args.no_call:
        print("[agent] --no-call 已启用，未调用百炼。", flush=True)
        return 0

    response = call_bailian_responses(prompt, "节点命名临时测试", use_search_tools=False)
    raw_text = _response_text(response)
    result = _extract_json_object(raw_text)
    payload = {
        "input_file": str(Path(args.xlsx).resolve()),
        "limit": args.limit,
        "candidate_nodes": DEFAULT_CANDIDATE_NODES,
        "records": records,
        "result": result,
        "raw_response": raw_text,
    }
    output_path = Path(args.output)
    write_json(output_path, payload)
    print(f"[agent] 已写入测试结果：{output_path}", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
