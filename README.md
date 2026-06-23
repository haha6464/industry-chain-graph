# Agent 化产业链图谱

面向 25 个基础行业的标清产业链图谱构建与维护工具。当前 demo 是食品饮料行业，系统包含两条主线：

- 图谱应用：FastAPI + Neo4j + React，用于展示、筛选查询、AI 问答和图谱可视化。
- 构建 Agent：调用阿里云百炼 Qwen Responses API 自动联网搜索，抽取产业链节点/关系，并通过校验 Agent 做最小修图。

## TLTR (太长不读)

首次配置：

```powershell
.\scripts\setup-conda.ps1
```

自动联网构建食品饮料候选图谱：

```powershell
.\scripts\run-agent.ps1 tools\agent\build_graph.py --industry-id food_beverage --industry-name 食品饮料行业
```

确认校验通过后写回正式 `graph.json`：

```powershell
.\scripts\run-agent.ps1 tools\agent\build_graph.py --industry-id food_beverage --industry-name 食品饮料行业 --apply
```

启动前后端查看图谱：

```powershell
.\scripts\start-neo4j.ps1
.\scripts\start-backend.ps1
.\scripts\start-frontend.ps1
.\scripts\import-food-beverage.ps1
```

## 环境与配置

### 本机依赖

- Windows PowerShell 或 VSCode 终端
- Docker Desktop，并确保 Docker Desktop 已启动
- Conda / Anaconda / Miniconda
- Node.js 18+
- npm

### Conda 环境

项目使用根目录的 `environment.yml` 管理 Python 环境，默认环境名是 `industry-chain-graph`。

```powershell
.\scripts\setup-conda.ps1
```

也可以手动执行：

```powershell
conda env create -n industry-chain-graph -f environment.yml
conda activate industry-chain-graph
```

如果环境已存在，`setup-conda.ps1` 会自动执行 `conda env update --prune`。

### API Key

在 `backend/.env` 中配置。`OPENAI_*` 只用于图谱问答；`DASHSCOPE_API_KEY` 和 `BAILIAN_*` 用于构建 Agent 的联网搜索、抽取和校验修图。

```env
OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=

DASHSCOPE_API_KEY=
BAILIAN_BASE_URL=
BAILIAN_MODEL=
```

## 构建 Agent 流程

完整构建链路：

```text
百炼联网搜索/抽取 -> 原始候选图谱 -> 硬规则预检 -> 百炼校验 Agent 最小修图 -> 硬规则复检 -> CSV/报告/复核队列
```

### 1. 自动构建候选图谱

```powershell
.\scripts\run-agent.ps1 tools\agent\build_graph.py --industry-id food_beverage --industry-name 食品饮料行业
```

构建 Agent 会调用 Qwen Responses API，并启用 `web_search`、`web_extractor`、`code_interpreter`。

### 2. 写回正式图谱

只有硬规则复检无 error，且百炼校验没有 `fail` 时，`--apply` 才会覆盖正式 `graph.json`。

```powershell
.\scripts\run-agent.ps1 tools\agent\build_graph.py --industry-id food_beverage --industry-name 食品饮料行业 --apply
```

### 3. 构建产物

```text
data/industries/food_beverage/search_plan.json
data/industries/food_beverage/agent_raw_response.txt
data/industries/food_beverage/pre_validation_candidate_graph.json
data/industries/food_beverage/validation_agent_raw_response.txt
data/industries/food_beverage/semantic_validation_report.json
data/industries/food_beverage/candidate_graph.json
data/industries/food_beverage/sources.jsonl
data/industries/food_beverage/validation_report.md
data/industries/food_beverage/validation_report.json
data/industries/food_beverage/review_queue.json
data/industries/food_beverage/build_report.md
data/industries/food_beverage/exports/
```

- `pre_validation_candidate_graph.json`：百炼抽取后的原始候选图谱。
- `candidate_graph.json`：百炼校验 Agent 最小修正后的候选图谱。
- `semantic_validation_report.json`：百炼校验、修改和复核建议。
- `validation_report.md/json`：硬规则 + 百炼校验综合报告。
- `review_queue.json`：需要人工复核的问题。

## 图谱应用启动

以下命令都在项目根目录执行，建议分多个 PowerShell 窗口运行。

### 1. 启动 Neo4j

```powershell
.\scripts\start-neo4j.ps1
```

Neo4j Browser：`http://localhost:7474`，账号：`neo4j / password123`。

### 2. 启动后端

```powershell
.\scripts\start-backend.ps1
```

后端 API：`http://127.0.0.1:8010`

### 3. 启动前端

```powershell
.\scripts\start-frontend.ps1
```

前端页面：`http://localhost:5173`

### 4. 导入食品饮料图谱到 Neo4j

```powershell
.\scripts\import-food-beverage.ps1
```

导入成功后刷新前端页面，即可查看食品饮料行业图谱。

## 单独工具

### 标准化已有 graph.json

```powershell
.\scripts\run-agent.ps1 tools\agent\standardize_graph.py --industry-id food_beverage
```

### 确定性规则校验

只运行硬规则校验，不调用百炼。完整构建流程会自动额外调用百炼校验 Agent 做最小修图。

```powershell
.\scripts\run-agent.ps1 tools\agent\validators\graph_validator.py --industry-id food_beverage
```

### 导出 CSV

```powershell
.\scripts\run-agent.ps1 tools\agent\export_csv.py --industry-id food_beverage
```

节点 CSV 字段：`节点id,节点类型,节点名称,节点标签,节点行业,业务描述,关键节点,产业链环节`

关系 CSV 字段：`起点节点id,起点节点名称,终点节点id,终点节点名称,关系类型,关系权重,关系描述`

关系导出规则：

- 内部 `contains`：父节点 -> 子节点；CSV 导出为 `子节点 SUBORDINATE_TO 父节点`。
- 内部 `upstream_downstream`：上游 -> 下游；CSV 导出为 `下游 DOWNSTREAM_OF 上游`。

### 生成搜索计划

用于查看和留档 Agent 应覆盖的 query 模板。构建 Agent 会自动调用百炼联网搜索。

```powershell
.\scripts\run-agent.ps1 tools\agent\search\search_planner.py --industry-id food_beverage --industry-name 食品饮料行业
```

### 更新维护 Agent MVP

增量更新会调用百炼 Responses API 联网搜索新增资料，生成小范围 update proposal。默认 `check_only` 和 `propose` 不写回正式图谱；只有 `apply` 且通过硬规则校验、无 error 复核项时才写回。

```powershell
.\scripts\run-agent.ps1 tools\agent\update_graph.py --industry-id food_beverage --mode check_only
```

生成 proposal 但不写回：

```powershell
.\scripts\run-agent.ps1 tools\agent\update_graph.py --industry-id food_beverage --mode propose
```

校验通过后应用增量：

```powershell
.\scripts\run-agent.ps1 tools\agent\update_graph.py --industry-id food_beverage --mode apply
```

主要产物：

```text
data/industries/food_beverage/update_proposal.json
data/industries/food_beverage/update_candidate_graph.json
data/industries/food_beverage/update_agent_raw_response.txt
data/industries/food_beverage/update_report.md
```

## 后端 Agent API

```text
POST /api/agent/build
POST /api/agent/update
GET  /api/agent/runs/{run_id}
GET  /api/agent/runs/{run_id}/report
POST /api/industries/{industry_id}/export-csv
GET  /api/industries/{industry_id}/exports
```

触发食品饮料构建：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/api/agent/build" -ContentType "application/json" -Body '{"industry_id":"food_beverage","industry_name":"食品饮料行业","target_depth":"5-6 层"}'
```

导出 CSV：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/api/industries/food_beverage/export-csv"
```

## 目录结构

```text
industry-chain-graph/
  backend/                  FastAPI 后端
  frontend/                 React 前端
  data/industries/          行业图谱数据与 Agent 产物
  docs/                     技术方案文档
  scripts/                  Windows 启动脚本
  tools/agent/              Agent 构建、校验、导出、更新工具链
  environment.yml           Conda 环境配置
  docker-compose.yml        本地 Neo4j 容器配置
```

## 常用开发检查

后端和 Agent 工具语法检查：

```powershell
conda run -n industry-chain-graph python -m compileall backend\app tools\agent
```

前端构建：

```powershell
cd frontend
npm run build
cd ..
```

## 下一步扩展建议

- 优化百炼抽取和校验 prompt，提高 5-6 层图谱深度、关系方向和最小修图稳定性。
- 增加批量行业运行脚本，对 25 个行业统一执行构建、校验、更新检查和导出。
- 为 25 个行业补齐 `manifest.json` 和初始目录。
- 做前端 Agent 工作台：行业选择、构建按钮、更新检查、运行报告、复核队列和 CSV 下载。
