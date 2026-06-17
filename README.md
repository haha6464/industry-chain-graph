# 产业链图谱应用

面向 25 个基础行业的标清产业链图谱应用。当前 demo 适配食品饮料行业，包含筛选查询、AI 问答、Neo4j 图谱可视化三个模块。

## 环境配置提醒

运行前请确认本机已安装并可用：

- Windows PowerShell 或 VSCode 终端
- Docker Desktop，并确保 Docker Desktop 已启动
- Python 3.10+
- Node.js 18+
- npm

如果 PowerShell 阻止执行脚本，请在当前 PowerShell 窗口先运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

AI 问答默认使用 OpenAI-compatible API。需要真实调用模型时，请在 `backend/.env` 中配置：

```env
OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=
```

不配置 API key 时，筛选查询和图谱可视化仍可正常使用，AI 问答会提示未配置模型密钥。

## 启动方式

在 Windows PowerShell 中进入项目根目录：

```powershell
cd D:\Work\实习-华泰\industry-chain-graph
```

### 1. 启动 Neo4j

```powershell
.\scripts\start-neo4j.ps1
```

Neo4j Browser:

```text
http://localhost:7474
```

账号：

```text
neo4j / password123
```

### 2. 启动后端

另开一个 PowerShell：

```powershell
cd D:\Work\实习-华泰\industry-chain-graph
.\scripts\start-backend.ps1
```

后端 API：

```text
http://127.0.0.1:8010
```

### 3. 启动前端

另开一个 PowerShell：

```powershell
cd D:\Work\实习-华泰\industry-chain-graph
.\scripts\start-frontend.ps1
```

前端页面：

```text
http://localhost:5173
```

### 4. 导入食品饮料图谱

确认 Neo4j 和后端都已启动后，另开一个 PowerShell：

```powershell
cd D:\Work\实习-华泰\industry-chain-graph
.\scripts\import-food-beverage.ps1
```

导入成功后，刷新前端页面即可查看食品饮料行业图谱。

## 项目结构

```text
industry-chain-graph/
  backend/                  FastAPI 后端
  frontend/                 React 前端
  data/industries/          行业图谱数据
  docs/                     技术方案文档
  scripts/                  Windows 启动脚本
  docker-compose.yml        本地 Neo4j 容器配置
```

