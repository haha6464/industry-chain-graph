# AGENTS.md — 项目约定与开发规范

面向 AI 辅助开发的项目架构、数据模型、模块职责和编码约定。

## 1. 项目概述

本项目是面向华泰证券 25 个基础行业的**标清产业链图谱构建与维护 Agent**。系统有两条主线：

- **图谱应用**：FastAPI + React，用于产业链图谱的展示、筛选、节点审计和 AI 问答。
- **构建 Agent**：调用阿里云百炼 Qwen Responses API 分阶段构建：先生成并评估一级骨架，再逐分支扩展并评估分支质量，合并后执行硬规则校验；只有硬规则失败时才调用百炼做格式修复，人工确认后写入正式图谱。

每个行业的最终交付物：`graph.json`（正式图谱）+ 节点 CSV + 关系 CSV。

## 2. 目录结构与模块职责

```text
industry-chain-graph/
  backend/app/              FastAPI 后端
    main.py                   路由定义，所有 API 端点
    schemas.py                Pydantic 数据模型（前后端共享的字段契约）
    agent_service.py          Agent 子进程调度、运行状态管理、产物读写
    ai_service.py             图谱问答（调用 OpenAI 兼容 API）
    graph_loader.py           从 data/industries/{id}/graph.json 加载图谱
    neo4j_client.py           Neo4j 驱动封装（连接管理）
    repository.py             Neo4j 读写（导入图谱、Cypher 查询）
    config.py                 pydantic-settings 配置，读取 backend/.env
  frontend/src/             React + Vite + TypeScript 前端
    App.tsx                   主页面：图谱展示页 + Agent 工作流页（双页面切换）
    api.ts                    所有后端 API 调用封装
    types.ts                  TypeScript 类型定义（必须与 schemas.py 对齐）
  tools/agent/              Agent 工具链（可 CLI 独立运行，也可被后端子进程调用）
    build_candidate_graph.py  候选构建入口（--stage skeleton/branches/all）
    final_validate_graph.py    最终硬规则校验 + 必要时格式修复
    update_graph.py           增量更新主流程入口
    export_csv.py             mentor 格式 CSV 导出
    common.py                 公共工具：路径常量、JSON/JSONL IO、standardize_graph()
    bailian_client.py         百炼 Qwen Responses API 封装
    standardize_graph.py      已有 graph.json 标准化脚本
    search/
      search_planner.py         搜索 query 模板与搜索计划生成
      bailian_responses_agent.py  百炼联网搜索构建（single 策略）+ 证据提取
      staged_bailian_builder.py   分阶段构建：seed 骨架 + 逐分支扩展 + 合并
    validators/
      graph_validator.py        硬规则校验（确定性规则，不调用 AI）
      bailian_graph_validator.py  百炼格式修复（仅硬规则失败时调用）
    mergers/
      graph_merger.py           图谱融合、去重、关系冲突检测、复核队列生成
    updaters/
      bailian_update_agent.py   百炼增量更新 Agent
  data/industries/          行业数据目录
    manifest.json             25 个行业池登记（id、名称、板块、状态、数据路径）
    {industry_id}/            每个行业一个子目录
      graph.json                正式图谱
      candidate_graph.json      候选图谱
      sources.jsonl             证据库
      exports/                  CSV 导出目录
  scripts/                  Windows PowerShell 启动与工具脚本
  environment.yml           Conda 环境定义
  docker-compose.yml        本地 Neo4j Docker 容器
  docs/                     需求讨论、周报、技术方案
```

## 3. 核心数据模型

### 3.1 graph.json 顶层结构

```json
{
  "industry": "食品饮料",
  "version": "v0.2-staged-build",
  "schema_version": "standard_industry_graph_v0.2_agent",
  "generated_at": "2026-06-26T...",
  "scope": "标清产业链图谱；不包含公司节点、股票代码、财务指标。",
  "source_basis": [{"name": "资料标题", "url": "https://...", "note": "..."}],
  "nodes": [...],
  "edges": [...]
}
```

`schema_version` 当前固定为 `"standard_industry_graph_v0.2_agent"`。

### 3.2 GraphNode 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 节点唯一 ID，格式 `{INDUSTRY_PREFIX}{序号}` 如 `FOOD000001` |
| `name` | str | 标准节点名称 |
| `node_type` | str | `产业链` (level=0) / `产业链环节` (level=1) / `细分环节` (level>=2) |
| `tags` | list[str] | 如 `["level_1", "upstream"]` |
| `industry` | str | 所属行业名称 |
| `level` | int | 数字层级：0=根节点，1=一级环节，2/3/4/5...=逐级细分 |
| `chain_position` | Literal | `root` / `upstream` / `midstream` / `downstream` / `support` |
| `chain_segment` | str | 位置标签中文：`root` / `上游` / `中游` / `下游` / `支持` |
| `parent_id` | str | 父节点 ID（contains 关系中的上级） |
| `description` | str | 节点描述 |
| `business_description` | str | 业务描述（通常与 description 相同） |
| `is_key_node` | bool | 关键节点标记（level<=1 默认为 true） |
| `source_urls` | list[str] | URL 来源列表（必须至少有 1 个） |
| `evidence_ids` | list[str] | 关联的证据 ID |
| `confidence` | float | 置信度 0-1 |
| `updated_at` | str | ISO 时间戳 |

### 3.3 GraphEdge 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 边 ID，格式 `{source}__{relation_type}__{target}` |
| `source` | str | 起点节点 ID |
| `target` | str | 终点节点 ID |
| `relation_type` | Literal | 只允许 `contains` 或 `upstream_downstream` |
| `relation_weight` | float | 关系权重，默认 1.0 |
| `description` | str | 关系描述 |
| `source_urls` | list[str] | URL 来源（必须至少有 1 个） |
| `confidence` | float | 置信度 0-1 |

**关系方向约定**：

- `contains`：内部存储为 **父节点 -> 子节点**
- `upstream_downstream`：内部存储为 **上游节点 -> 下游节点**
- 同一节点对只允许一种主关系

### 3.4 CSV 导出映射

导出 mentor 格式 CSV 时，关系类型和方向需要反转：

| 内部 relation_type | 内部方向 | CSV 关系类型 | CSV 方向 |
|---|---|---|---|
| `contains` | 父 -> 子 | `SUBORDINATE_TO` | 子 -> 父（A 隶属于 B） |
| `upstream_downstream` | 上游 -> 下游 | `DOWNSTREAM_OF` | 下游 -> 上游（A 是 B 的下游） |

节点 CSV 字段：`节点id,节点类型,节点名称,节点标签,节点行业,业务描述,关键节点,产业链环节`

关系 CSV 字段：`起点节点id,起点节点名称,终点节点id,终点节点名称,关系类型,关系权重,关系描述`

### 3.5 sources.jsonl 证据库

每行一条证据记录：

```json
{"evidence_id": "food_beverage_ev_0001", "url": "https://...", "title": "...", "published_at": "", "retrieved_at": "...", "content_type": "web_search_result", "content_hash": "sha256...", "snippet": "...", "status": "ok"}
```

### 3.6 manifest.json 行业池

`data/industries/manifest.json` 是一个 JSON 数组，每项：

```json
{"id": "food_beverage", "name": "食品饮料行业", "sector": "必选消费", "source_order": 40, "data_path": "data/industries/food_beverage/graph.json", "status": "demo", "node_count": 71, "edge_count": 121}
```

`status` 取值：`pending`（未构建）/ `demo`（已有 demo）/ `ready`（已构建）。

## 4. Agent 构建与更新流程

### 4.1 构建流程（staged 策略）

```text
1. 生成搜索计划 -> search_plan.json
2. 百炼联网搜索构建一级骨架 -> staged_level1_graph.json
3. 评估一级骨架质量 -> staged_level1_evaluation.json；未通过时仅修正骨架
4. 逐个扩展一级分支（最多 BAILIAN_STAGED_MAX_BRANCHES 个） -> staged_branch_fragments.json
5. 逐分支评估质量 -> staged_branch_evaluations.json；未通过时仅修正当前分支
6. 合并图谱与质量意见 -> staged_merged_graph.json / staged_quality_opinions.json
7. 标准化 -> pre_validation_candidate_graph.json
8. 单轮硬规则校验；通过则直接生成 candidate_graph.json
9. 硬规则失败时调用百炼格式修复 -> format_repair_report.json
10. 生成 validation_report.md/json、review_queue.json、sources.jsonl 和 CSV
```

**apply 条件**：最终硬规则 `error_count == 0` 且格式修复未失败时，前端或 `apply-candidate` API 可将 candidate_graph.json 写入正式 graph.json。

single 策略（`--strategy single`）仅作为 CLI 调试路径，前端默认使用 staged 的骨架/分支拆分流程。

### 4.2 增量更新流程

```text
1. 读取正式 graph.json + sources.jsonl
2. 百炼联网搜索增量资料
3. 生成 update_proposal.json（add_nodes/add_edges/modify_nodes/modify_edges/remove_or_deprecate）
4. 应用提案到候选图谱 -> update_candidate_graph.json
5. 硬规则校验
6. 按 mode 决定是否写回：
   - check_only: 只检查不写回
   - propose: 生成提案不写回
   - apply: 校验通过且无 error 时写回正式 graph.json
```

增量更新原则：没有足够新增证据就输出 `no_change`，不为更新而更新。

## 5. 后端 API

### 5.1 路由一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/industries` | 行业列表（从 manifest.json 读取） |
| GET | `/api/graph` | 获取图谱（支持 q/chain_positions/relation_types/levels 筛选） |
| GET | `/api/nodes/{node_id}/neighbors` | 获取节点邻居 |
| POST | `/api/ask` | 图谱问答 |
| POST | `/api/agent/search-plan` | 生成搜索计划 |
| POST | `/api/agent/build-skeleton` | 构建并评估一级骨架 |
| POST | `/api/agent/build-branches` | 逐分支扩展、评估并合并校验前候选图谱 |
| POST | `/api/agent/final-validate` | 最终硬规则校验，必要时格式修复 |
| POST | `/api/agent/update` | 启动更新 |
| GET | `/api/agent/runs/{run_id}` | 查看运行状态 |
| POST | `/api/agent/runs/{run_id}/cancel` | 中断运行 |
| GET | `/api/agent/runs/{run_id}/report` | 查看运行报告 |
| POST | `/api/industries/{id}/apply-candidate` | 应用候选图谱 |
| POST | `/api/industries/{id}/export-csv` | 导出 CSV |
| GET | `/api/industries/{id}/exports` | 查看导出文件列表 |
| GET | `/api/industries/{id}/agent-artifacts` | 查看 Agent 产物列表 |
| GET | `/api/industries/{id}/agent-artifacts/{name}` | 查看单个产物内容 |
| POST | `/api/import/food-beverage` | 导入食品饮料图谱到 Neo4j |

### 5.2 Agent 运行模型

构建和更新通过 `agent_service.py` 以**后端子进程**方式运行（调用 `build_candidate_graph.py`、`final_validate_graph.py` 或 `update_graph.py`）。运行状态由内存中的 `AgentRunState` 字典跟踪，前端通过轮询 `GET /api/agent/runs/{run_id}` 获取实时日志和步骤。进程存储在 `RUNS: dict[str, AgentRunState]`，重启后会丢失。

Agent 工具既支持 CLI 直接运行（通过 `scripts/run-agent.ps1` 包装以激活 conda 环境），也支持后端 API 触发子进程。

## 6. 技术栈与环境

| 项 | 说明 |
|---|---|
| Python 环境 | conda `industry-chain-graph`，由 `environment.yml` 管理 |
| 后端框架 | FastAPI + pydantic-settings，端口 8010 |
| 前端框架 | React 18 + Vite + TypeScript，端口 5173 |
| 图数据库 | Neo4j Docker（可选，前端直接读 graph.json，Neo4j 仅用于调试） |
| 构建/校验/更新 AI | 阿里云百炼 Qwen Responses API（`DASHSCOPE_API_KEY` + `BAILIAN_*` 环境变量） |
| 问答 AI | OpenAI 兼容 API（`OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL`） |
| 环境变量文件 | `backend/.env` |

**百炼关键配置**（`backend/.env`）：

- `BAILIAN_MODEL` — 模型名，默认 `qwen3.7-max`
- `BAILIAN_SEARCH_STRATEGY` — 搜索策略，默认 `agent_max`
- `BAILIAN_TIMEOUT_SECONDS` — 超时秒数，默认 600
- `BAILIAN_STAGED_MAX_BRANCHES` — 分阶段构建最多扩展多少个一级分支，默认 8
- `BAILIAN_ENABLE_THINKING` — 是否开启思考模式，默认 true
- `BAILIAN_ENABLE_CODE_INTERPRETER` — 是否开启代码解释器，默认 false（通常关闭以节省时间）

**sys.path 补丁**：`tools/agent/` 下的 CLI 入口文件都有 `sys.path` 自动发现逻辑，通过向上查找 `data/industries/manifest.json` 来定位项目根目录。

## 7. 开发规范与注意事项

### 7.1 数据格式权威来源

`tools/agent/common.py` 中的 `standardize_graph()` 函数是图谱数据格式的权威实现。所有写入 graph.json 的数据都必须经过此函数标准化。它负责补全缺失字段、统一 node_type 推断、生成 edge ID、设置默认置信度等。

### 7.2 前后端类型对齐

`backend/app/schemas.py`（Pydantic）和 `frontend/src/types.ts`（TypeScript）定义了前后端共享的数据类型。**修改任一方时必须同步修改另一方**。关键类型包括 `GraphNode`、`GraphEdge`、`GraphFilters`、`AgentRunResponse`、`AgentArtifact` 等。

### 7.3 添加新 API 的标准模式

1. 在 `schemas.py` 定义请求/响应模型
2. 在 `main.py` 添加路由
3. 在 `agent_service.py`（Agent 相关）或 `graph_loader.py`（图谱读取相关）实现业务逻辑
4. 在 `frontend/src/types.ts` 添加对应 TypeScript 类型
5. 在 `frontend/src/api.ts` 添加 API 调用封装

### 7.4 Agent 产物命名

所有 Agent 产物存放在 `data/industries/{industry_id}/` 下。产物名称和路径在 `backend/app/agent_service.py` 的 `ARTIFACT_SPECS` 列表中统一定义，新增产物时必须在此处注册。

### 7.5 校验规则阈值

定义在 `tools/agent/validators/graph_validator.py`：

- `MIN_CONFIDENCE = 0.5` — 节点/关系置信度下限
- `MIN_TARGET_NODES = 60` — 节点数量下限（低于此值触发 warning）
- `MAX_TARGET_NODES = 150` — 节点数量硬上限（超过触发 warning）
- `MIN_LEVEL_ONE_NODES = 5` — level=1 一级环节最少数量

### 7.6 业务约束

- **当前版本不涉及公司信息**：不抽取公司节点，不保留 `company_list`，不处理股票代码、财务指标和个股内容。校验规则会检测并拒绝包含公司字段的节点。
- **关系类型只有两种**：`contains` 和 `upstream_downstream`。
- **每个节点和关系必须至少有 1 个 URL 来源**。
- **候选图谱不自动覆盖正式图谱**：必须通过校验 + 人工确认（或 `--apply` 标志）。

### 7.7 运行方式

所有 CLI 工具通过 PowerShell 脚本运行以激活 conda 环境：

```powershell
.\scripts\run-agent.ps1 tools\agent\build_candidate_graph.py --industry-id food_beverage --industry-name 食品饮料行业 --stage skeleton
.\scripts\run-agent.ps1 tools\agent\build_candidate_graph.py --industry-id food_beverage --industry-name 食品饮料行业 --stage branches
.\scripts\run-agent.ps1 tools\agent\final_validate_graph.py --industry-id food_beverage
```

语法检查：

```powershell
conda run -n industry-chain-graph python -m compileall backend\app tools\agent
```

前端构建检查：

```powershell
cd frontend && npm run build && cd ..
```

## 8. 当前状态与已知限制

- **已完成行业**：仅食品饮料（`food_beverage`）有完整 demo 图谱（71 节点，121 关系），其余 24 个行业均为 `pending`。
- **Agent 输出稳定性**：不同行业的资料结构差异大，输出质量受搜索结果和模型抽取稳定性影响，仍需通过多行业试点继续调 prompt 和规则。
- **图谱广度不足**：横向覆盖不够，容易形成少数分支深挖。已通过 prompt 和校验规则增加广度约束，但尚未在多行业上充分验证。
- **前端复核能力缺失**：已有 `review_queue.json`，但前端还没有复核编辑界面（接受/拒绝/改名/合并/删除）。
- **批量运行脚本未实现**：25 个行业的批量构建、校验、导出尚无统一脚本。
- **Neo4j 非前置依赖**：图谱展示直接读 graph.json，Neo4j 仅用于可选的数据库调试和已有的 Cypher 查询逻辑。


