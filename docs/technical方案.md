# 产业链图谱系统技术方案

## 1. 项目目标

本项目面向基础行业标清产业链图谱建设，第一版以食品饮料行业为 demo，后续扩展至 25 个基础行业。系统需要支持三个核心模块：

1. 筛选查询：按关键词、上下游分类、关系类型、层级筛选图谱。
2. AI 问答：基于图谱检索上下文进行产业链问答。
3. Neo4j 图谱可视化：展示产业链节点、隶属关系和上下游关系。

第一版不包含登录、权限、多用户、在线编辑图谱和自动生成 24 个剩余行业。

## 2. 技术栈

### 前端

- React + Vite + TypeScript
- Neo4j NVL React wrapper：图谱可视化
- lucide-react：按钮和状态图标

选择理由：

- Vite 适合本地快速开发，便于 Windows VSCode / PowerShell 调试。
- React 适合构建筛选、图谱、详情、问答并列的工作台界面。
- Neo4j NVL 是 Neo4j 官方图谱可视化库，便于与 Neo4j 数据模型对齐。

### 后端

- FastAPI
- Neo4j Python Driver
- Pydantic / pydantic-settings
- httpx

选择理由：

- FastAPI 自动生成 OpenAPI 文档，适合快速迭代接口。
- Neo4j Python Driver 是 Neo4j 官方驱动，支持参数化 Cypher。
- Python 更适合后续接入图谱生成、清洗、评估和 AI 能力。

### 数据库

- Neo4j 5 Community，本地 Docker 运行。

第一版只在本地使用 Neo4j，不接 Neo4j Aura。

### AI 能力

- OpenAI-compatible Chat Completions API。
- 通过环境变量配置 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`。

这样可以兼容 OpenAI、DeepSeek、通义千问等支持 OpenAI 格式的模型服务。

## 3. 仓库结构

```text
industry-chain-graph/
  backend/
    app/
      main.py
      config.py
      schemas.py
      graph_loader.py
      neo4j_client.py
      repository.py
      ai_service.py
    requirements.txt
    .env.example
  frontend/
    src/
      App.tsx
      api.ts
      types.ts
      styles.css
    package.json
    vite.config.ts
  data/
    industries/
      manifest.json
      food_beverage/
        graph.json
  docs/
    technical方案.md
  docker-compose.yml
  README.md
```

## 4. Neo4j 数据模型

### 节点

`:Industry`

| 字段 | 说明 |
| --- | --- |
| id | 行业 ID，例如 `food_beverage` |
| name | 行业名称 |
| status | 数据状态 |
| node_count | 节点数量 |
| edge_count | 关系数量 |

`:IndustryNode`

| 字段 | 说明 |
| --- | --- |
| id | 图谱节点 ID |
| industry_id | 行业 ID |
| name | 节点名称 |
| level | 层级 |
| chain_position | root/upstream/midstream/downstream/support |
| parent_id | 上级节点 ID |
| description | 节点简介 |

### 关系

`:CONTAINS`

- 表示隶属/分类关系。
- 示例：食品饮料行业 -> 食品制造。

`:UPSTREAM_DOWNSTREAM`

- 表示上下游流向。
- 示例：农产品原料 -> 食品制造。

## 5. API 设计

### `GET /api/health`

返回后端和 Neo4j 状态。

### `POST /api/import/food-beverage`

导入 `data/industries/food_beverage/graph.json`。

- 使用 `MERGE`，可重复执行。
- 导入后应得到 71 个节点、121 条关系。

### `GET /api/industries`

返回已导入 Neo4j 的行业。

### `GET /api/graph`

参数：

- `industry_id`
- `q`
- `chain_positions`
- `relation_types`
- `levels`

返回：

- `nodes`
- `edges`

用于筛选查询和图谱可视化。

### `GET /api/nodes/{node_id}/neighbors`

返回某个节点的入边、出边和邻接节点。

### `POST /api/ask`

请求：

```json
{
  "industry_id": "food_beverage",
  "question": "食品饮料行业上游有哪些？",
  "filters": {
    "q": "",
    "chain_positions": ["upstream"],
    "relation_types": [],
    "levels": []
  }
}
```

返回：

- `answer`
- `context_nodes`
- `context_edges`
- `cypher_summary`

## 6. AI 问答流程

第一版采用 GraphRAG-lite：

```text
用户问题 + 当前筛选条件
-> Neo4j 检索相关节点和关系
-> 整理为结构化上下文
-> 调用 OpenAI-compatible 模型
-> 返回回答、引用节点、引用关系
```

如果未配置 `OPENAI_API_KEY`，接口返回明确错误，不静默 mock。

## 7. 前端模块

### 筛选查询

- 行业选择，第一版只有食品饮料。
- 关键词搜索节点名称和简介。
- 按上下游分类筛选。
- 按关系类型筛选。
- 按层级筛选。

### 图谱可视化

- 使用 Neo4j NVL React wrapper。
- 节点颜色：
  - root：深灰
  - upstream：青绿
  - midstream：紫色
  - downstream：橙色
  - support：蓝色
- 关系颜色：
  - contains：灰色
  - upstream_downstream：红色

### 节点详情和 AI 问答

- 点击节点后展示简介、层级、所属环节和邻接关系。
- AI 问答展示回答、引用节点和引用关系。

## 8. Windows 本地运行方式

当前推荐直接在 Windows PowerShell 中运行项目。Neo4j 使用本机 Docker Desktop 启动，前端和后端直接在 Windows 环境运行。

### 8.1 打开仓库

```powershell
cd D:\Work\实习-华泰\industry-chain-graph
```

### 8.2 启动 Neo4j

先打开 Docker Desktop，再运行：

```powershell
docker compose up -d neo4j
```

Neo4j Browser:

```text
http://localhost:7474
```

账号：

```text
neo4j / password123
```

### 8.3 启动后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

如果 PowerShell 阻止激活虚拟环境，可以临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 8.4 启动前端

另开一个 PowerShell：

```powershell
cd D:\Work\实习-华泰\industry-chain-graph\frontend
npm install
npm run dev
```

### 8.5 导入食品饮料数据

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8010/api/import/food-beverage
```

访问：

```text
http://localhost:5173
```

### 8.6 一键脚本

项目提供 Windows PowerShell 脚本：

```powershell
.\scripts\start-neo4j.ps1
.\scripts\start-backend.ps1
.\scripts\start-frontend.ps1
.\scripts\import-food-beverage.ps1
```

## 9. 扩展到 25 个行业

后续每个行业新增：

```text
data/industries/{industry_id}/graph.json
```

同时更新：

```text
data/industries/manifest.json
```

后端接口从一开始使用 `industry_id` 参数，不写死食品饮料。当前只有导入接口为了开发方便保留 `POST /api/import/food-beverage`，后续可扩展为：

```text
POST /api/import/{industry_id}
```

## 10. 验收标准

1. Windows Docker Desktop 能正常启动 Neo4j。
2. `POST /api/import/food-beverage` 能重复执行。
3. 图谱导入后节点数为 71，关系数为 121。
4. 前端能加载食品饮料图谱。
5. 筛选条件变化后，节点和关系同步更新。
6. 点击节点能看到节点详情和邻接关系。
7. 未配置 AI key 时，AI 问答给出明确错误。
8. 配置 AI key 后，AI 问答返回回答、引用节点和引用关系。
