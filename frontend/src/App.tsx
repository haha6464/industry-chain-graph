import { useEffect, useMemo, useRef, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import { Bot, CheckCircle2, Circle, Database, Download, FileText, Filter, GitBranch, Network, RefreshCw, Search, Send, Sparkles, Square, Terminal, Trash2, X } from "lucide-react";
import { applyCandidateGraph, askGraph, buildAgentBranches, buildAgentSkeleton, cancelAgentRun, createSearchPlan, deleteAgentArtifact, exportIndustryCsv, fetchAgentArtifact, fetchAgentArtifacts, fetchAgentRun, fetchGraph, fetchIndustries, fetchIndustryExports, fetchNeighbors, finalValidateAgentGraph, updateAgentGraph } from "./api";
import type { AgentArtifact, AgentArtifactContent, AgentRunResponse, AskResponse, CandidateGraphType, ChainPosition, GraphEdge, GraphFilters, GraphNode, Industry, RelationType, UpdateMode } from "./types";

const nodeTypeOptions: Array<{ value: ChainPosition; label: string; color: string }> = [
  { value: "root", label: "根节点", color: "#334155" },
  { value: "support", label: "支撑节点", color: "#64748b" }
];
const relationOptions: Array<{ value: RelationType; label: string }> = [
  { value: "contains", label: "隶属关系" },
  { value: "upstream_downstream", label: "上下游关系" }
];
type LayoutMode = "forceDirected" | "hierarchical";
type PageMode = "graph" | "agent";
const defaultFilters: GraphFilters = { q: "", chain_positions: [], relation_types: [], levels: [] };
const activeRunStatuses = new Set(["running", "canceling"]);

function isActiveRun(run: AgentRunResponse | null) {
  return Boolean(run && activeRunStatuses.has(run.status));
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}
const levelGradientStops = [
  { position: 0, color: "#ea580c" },
  { position: 0.35, color: "#f59e0b" },
  { position: 0.7, color: "#06b6d4" },
  { position: 1, color: "#2563eb" }
];

function hexToRgb(hex: string) {
  const value = hex.replace("#", "");
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16)
  };
}

function rgbToHex({ r, g, b }: { r: number; g: number; b: number }) {
  return "#" + [r, g, b].map((value) => Math.round(value).toString(16).padStart(2, "0")).join("");
}

function levelColor(level: number, maxLevel = 6) {
  const safeMaxLevel = Math.max(1, maxLevel);
  const ratio = Math.min(Math.max(level / safeMaxLevel, 0), 1);
  const rightIndex = levelGradientStops.findIndex((stop) => stop.position >= ratio);
  const rightStop = levelGradientStops[rightIndex === -1 ? levelGradientStops.length - 1 : rightIndex];
  const leftStop = levelGradientStops[Math.max(0, (rightIndex === -1 ? levelGradientStops.length - 1 : rightIndex) - 1)];
  const span = Math.max(0.001, rightStop.position - leftStop.position);
  const localRatio = (ratio - leftStop.position) / span;
  const left = hexToRgb(leftStop.color);
  const right = hexToRgb(rightStop.color);
  return rgbToHex({
    r: left.r + (right.r - left.r) * localRatio,
    g: left.g + (right.g - left.g) * localRatio,
    b: left.b + (right.b - left.b) * localRatio
  });
}
function relationLabel(type: RelationType) {
  return relationOptions.find((item) => item.value === type)?.label ?? type;
}
function formatPercent(value?: number) {
  return value === undefined || Number.isNaN(value) ? "-" : Math.round(value * 100) + "%";
}
function artifactPreview(content: AgentArtifactContent | null) {
  if (!content) return "选择一个产物查看内容。";
  return typeof content.content === "string" ? content.content : JSON.stringify(content.content, null, 2);
}
export function App() {
  const nvlRef = useRef<any>(null);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const runLogRef = useRef<HTMLPreElement | null>(null);
  const [pageMode, setPageMode] = useState<PageMode>("graph");
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [industryId, setIndustryId] = useState("");
  const [draftFilters, setDraftFilters] = useState<GraphFilters>(defaultFilters);
  const [appliedFilters, setAppliedFilters] = useState<GraphFilters>(defaultFilters);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [neighborEdges, setNeighborEdges] = useState<GraphEdge[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [activeRun, setActiveRun] = useState<AgentRunResponse | null>(null);
  const [runDrawerOpen, setRunDrawerOpen] = useState(false);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<AgentArtifactContent | null>(null);
  const [exportPaths, setExportPaths] = useState<string[]>([]);
  const [message, setMessage] = useState("选择行业后会直接加载正式图谱；未构建行业请切到 Agent 工作流生成 graph.json。");
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("forceDirected");

  const industryName = industries.find((industry) => industry.id === industryId)?.name ?? industryId;
  const hasSelectedIndustry = industryId.trim().length > 0;
  const existingArtifacts = artifacts.filter((artifact) => artifact.exists);
  const levelOptions = useMemo(() => {
    const values = new Set<number>();
    nodes.forEach((node) => values.add(node.level));
    draftFilters.levels.forEach((level) => values.add(level));
    return Array.from(values).sort((left, right) => left - right);
  }, [nodes, draftFilters.levels]);
  const maxVisibleLevel = useMemo(() => {
    const graphMax = nodes.reduce((maxLevel, node) => Math.max(maxLevel, node.level), 0);
    const filterMax = draftFilters.levels.reduce((maxLevel, level) => Math.max(maxLevel, level), 0);
    return Math.max(graphMax, filterMax, 1);
  }, [nodes, draftFilters.levels]);
  const hasArtifact = (name: string) => artifacts.some((artifact) => artifact.name === name && artifact.exists);
  const workflowSteps = [
    { id: "plan", title: "搜索规划", summary: "生成行业检索 query，并记录 search_plan.json。", done: hasArtifact("search_plan"), artifacts: ["search_plan"], action: "plan" },
    { id: "skeleton", title: "一级骨架构建", summary: "联网生成 level=1 一级产业链骨架，并立即评估分类质量；不通过时只修骨架。", done: hasArtifact("staged_level1_graph") && hasArtifact("staged_level1_evaluation"), artifacts: ["agent_request_prompt", "staged_level1_graph", "staged_level1_evaluation", "staged_quality_opinions"], action: "skeleton" },
    { id: "branches", title: "分支扩展与评估", summary: "基于一级骨架逐分支扩展，每条分支单独评估；不通过时只修当前分支，最后合并为校验前候选图谱。", done: hasArtifact("pre_validation_candidate_graph"), artifacts: ["staged_branch_fragments", "staged_branch_evaluations", "staged_quality_opinions", "staged_merged_graph", "staged_errors", "agent_raw_response", "pre_validation_candidate_graph"], action: "branches" },
    { id: "validate_repair", title: "最终校验与格式修复", summary: "单轮硬规则校验；只有不通过才请求百炼做格式修复，不再做整图质量审查。", done: hasArtifact("candidate_graph") && hasArtifact("validation_report"), artifacts: ["validation_agent_request_prompt", "validation_agent_raw_response", "format_repair_report", "candidate_graph", "sources", "validation_report", "validation_report_json", "review_queue"], action: "final_validate" },
    { id: "update", title: "增量更新", summary: "联网搜索新增证据，默认生成 no_change 或 update_proposal。", done: hasArtifact("update_proposal") || hasArtifact("update_report"), artifacts: ["update_agent_request_prompt", "update_proposal", "update_candidate_graph", "update_report", "update_agent_raw_response"], action: "update" },
    { id: "export", title: "CSV 交付", summary: "按 mentor 格式导出节点 CSV 和关系 CSV。", done: exportPaths.length > 0, artifacts: [], action: "export" }
  ];

  async function loadIndustries() {
    try {
      const data = await fetchIndustries();
      setIndustries(data);
      if (data.length > 0) setIndustryId((current) => (data.some((industry) => industry.id === current) ? current : ""));
    } catch {
      setIndustries([]);
    }
  }
  async function loadGraph(nextFilters = appliedFilters) {
    if (!hasSelectedIndustry) {
      setNodes([]);
      setEdges([]);
      setMessage("请选择行业后查看图谱。");
      return;
    }
    setGraphLoading(true);
    try {
      const data = await fetchGraph(industryId, nextFilters);
      setNodes(data.nodes);
      setEdges(data.edges);
      setMessage("已加载 " + data.nodes.length + " 个节点、" + data.edges.length + " 条关系。");
      if (selectedNode && !data.nodes.some((node) => node.id === selectedNode.id)) {
        setSelectedNode(null);
        setNeighborEdges([]);
      }
    } catch (error) {
      setNodes([]);
      setEdges([]);
      setMessage(error instanceof Error ? error.message : "图谱加载失败");
    } finally {
      setGraphLoading(false);
    }
  }
  async function loadArtifacts() {
    if (!hasSelectedIndustry) {
      setArtifacts([]);
      return;
    }
    setArtifactLoading(true);
    try {
      const data = await fetchAgentArtifacts(industryId);
      setArtifacts(data.artifacts);
    } catch {
      setArtifacts([]);
    } finally {
      setArtifactLoading(false);
    }
  }
  async function refreshArtifactsSilently() {
    if (!hasSelectedIndustry) return;
    try {
      const data = await fetchAgentArtifacts(industryId);
      setArtifacts(data.artifacts);
    } catch {
      // Keep the current artifact list while a long-running Agent call is still in flight.
    }
  }
  async function loadExports() {
    if (!hasSelectedIndustry) {
      setExportPaths([]);
      return;
    }
    try {
      const data = await fetchIndustryExports(industryId);
      setExportPaths(data.exports);
    } catch {
      setExportPaths([]);
    }
  }
  async function trackAgentRun(initialRun: AgentRunResponse, successMessage: string, afterDone?: () => Promise<void>) {
    setActiveRun(initialRun);
    setRunDrawerOpen(true);
    setAgentBusy(isActiveRun(initialRun));
    setMessage((initialRun.current_step || "Agent 已启动") + "：run " + initialRun.run_id);
    let current = initialRun;
    let pollCount = 0;
    try {
      while (isActiveRun(current)) {
        await wait(1200);
        current = await fetchAgentRun(initialRun.run_id);
        pollCount += 1;
        setActiveRun(current);
        setMessage((current.current_step || current.status) + "：run " + current.run_id);
        if (pollCount % 3 === 0) await refreshArtifactsSilently();
      }
      setAgentBusy(false);
      await loadArtifacts();
      await loadExports();
      if (current.status === "completed") {
        setMessage(successMessage + "：run " + current.run_id + "。");
        if (afterDone) await afterDone();
      } else if (current.status === "canceled") {
        setMessage("运行已中断：run " + current.run_id + "。");
      } else {
        setMessage("运行失败：" + (current.current_step || current.status));
      }
    } catch (error) {
      setAgentBusy(false);
      setMessage(error instanceof Error ? error.message : "读取 Agent 运行状态失败");
    }
  }

  async function handleCancelRun() {
    if (!activeRun || !isActiveRun(activeRun)) return;
    try {
      const canceled = await cancelAgentRun(activeRun.run_id);
      setActiveRun(canceled);
      setMessage("正在中断 run " + activeRun.run_id + "...");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "中断运行失败");
    }
  }

  async function handleSearchPlan() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await createSearchPlan(industryId, industryName);
      setActiveRun(result);
      setRunDrawerOpen(true);
      setMessage("搜索规划完成：run " + result.run_id + "。");
      await loadArtifacts();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "搜索规划失败");
    } finally {
      setAgentBusy(false);
    }
  }


  async function handleBuildSkeleton() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await buildAgentSkeleton(industryId, industryName);
      await trackAgentRun(result, "一级骨架构建完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "一级骨架构建失败，请检查 DASHSCOPE_API_KEY 和百炼配置。");
    } finally {
      setAgentBusy(false);
    }
  }

  async function handleBuildBranches() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (!hasArtifact("staged_level1_graph")) {
      setMessage("请先运行一级骨架构建，生成 staged_level1_graph.json 后再扩展分支。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await buildAgentBranches(industryId, industryName);
      await trackAgentRun(result, "分支扩展与评估完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "分支扩展与评估失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleFinalValidate() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (!hasArtifact("pre_validation_candidate_graph")) {
      setMessage("请先运行分支扩展，生成 pre_validation_candidate_graph.json 后再运行最终校验。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await finalValidateAgentGraph(industryId);
      await trackAgentRun(result, "最终校验完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "最终校验失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleUpdate(mode: UpdateMode) {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await updateAgentGraph(industryId, mode);
      await trackAgentRun(result, "更新流程完成", mode === "apply" ? async () => { await loadGraph(); } : undefined);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 更新失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleApplyCandidate(candidateType: CandidateGraphType) {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    const label = candidateType === "candidate_graph" ? "候选图谱" : "更新候选图谱";
    if (!window.confirm("确认将" + label + "应用为正式 graph.json 吗？")) return;
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await applyCandidateGraph(industryId, candidateType);
      setActiveRun(result);
      setRunDrawerOpen(true);
      setMessage(label + "已应用为正式图谱：run " + result.run_id + "。");
      await loadArtifacts();
      await loadExports();
      await loadGraph();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : label + "应用失败");
    } finally {
      setAgentBusy(false);
    }
  }

  async function handleExport() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    setAgentBusy(true);
    try {
      const result = await exportIndustryCsv(industryId);
      setExportPaths([result.node_csv, result.edge_csv]);
      setMessage("CSV 导出完成。");
      await loadExports();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "CSV 导出失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleArtifactOpen(name: string) {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    setArtifactLoading(true);
    try {
      setSelectedArtifact(await fetchAgentArtifact(industryId, name));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "产物读取失败");
    } finally {
      setArtifactLoading(false);
    }
  }  async function handleArtifactDelete() {
    if (!hasSelectedIndustry || !selectedArtifact) return;
    if (!window.confirm("确认删除当前产物「" + selectedArtifact.label + "」吗？")) return;
    setArtifactLoading(true);
    try {
      await deleteAgentArtifact(industryId, selectedArtifact.name);
      setMessage("已删除产物：" + selectedArtifact.label + "。");
      setSelectedArtifact(null);
      await loadArtifacts();
      await loadExports();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "产物删除失败");
    } finally {
      setArtifactLoading(false);
    }
  }
  async function handleNodeClick(nodeId: string) {
    if (!hasSelectedIndustry) return;
    const current = nodes.find((node) => node.id === nodeId) ?? null;
    setSelectedNode(current);
    if (!current) return;
    try {
      const data = await fetchNeighbors(industryId, nodeId);
      setNeighborEdges(data.edges);
    } catch {
      setNeighborEdges([]);
    }
  }
  async function handleAsk() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (!question.trim()) return;
    setAsking(true);
    setAnswer(null);
    try {
      const result = await askGraph(industryId, question, appliedFilters);
      setAnswer(result);
      setMessage(result.cypher_summary);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "AI 问答失败");
    } finally {
      setAsking(false);
    }
  }
  function applyFilters(next: GraphFilters) {
    setDraftFilters(next);
    setAppliedFilters(next);
    void loadGraph(next);
  }

  useEffect(() => { void loadIndustries(); }, []);
  useEffect(() => {
    setDraftFilters(defaultFilters);
    setAppliedFilters(defaultFilters);
    setSelectedNode(null);
    setNeighborEdges([]);
    setSelectedArtifact(null);
    if (hasSelectedIndustry) {
      void loadGraph(defaultFilters);
      void loadArtifacts();
      void loadExports();
    } else {
      setNodes([]);
      setEdges([]);
      setArtifacts([]);
      setExportPaths([]);
      setMessage("请选择行业后查看图谱。");
    }
  }, [industryId]);

  useEffect(() => {
    detailPanelRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [selectedNode?.id]);

  useEffect(() => {
    if (!runLogRef.current) return;
    runLogRef.current.scrollTop = runLogRef.current.scrollHeight;
  }, [activeRun?.logs.length]);

  useEffect(() => {
    if (nodes.length === 0 || pageMode !== "graph") return;
    const timer = window.setTimeout(() => nvlRef.current?.fit?.(nodes.map((node) => node.id)), 500);
    return () => window.clearTimeout(timer);
  }, [nodes, pageMode]);


  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const parentNode = selectedNode?.parent_id ? nodeById.get(selectedNode.parent_id) : null;
  const childEdges = selectedNode ? neighborEdges.filter((edge) => edge.relation_type === "contains" && edge.source === selectedNode.id) : [];
  const childNodes = childEdges.map((edge) => nodeById.get(edge.target)).filter((node): node is GraphNode => Boolean(node));
  const otherNeighborEdges = selectedNode
    ? neighborEdges.filter((edge) => !(edge.relation_type === "contains" && edge.source === selectedNode.id))
    : [];

  function nodeLabel(nodeId: string) {
    const node = nodeById.get(nodeId);
    return node ? node.name + "（" + node.id + "）" : nodeId;
  }
  function nodeName(nodeId: string) {
    return nodeById.get(nodeId)?.name ?? nodeId;
  }

  function relationSentence(edge: GraphEdge) {
    if (edge.relation_type === "contains") {
      return nodeName(edge.target) + " 隶属于 " + nodeName(edge.source);
    }
    return nodeName(edge.target) + " 是 " + nodeName(edge.source) + " 的下游";
  }

  const nvlNodes = useMemo(() => nodes.map((node) => ({
    id: node.id,
    caption: node.name,
    size: node.level === 0 ? 50 : node.is_key_node ? 38 : Math.max(18, 34 - node.level * 3),
    color: levelColor(node.level, maxVisibleLevel)
  })), [nodes]);
  const nvlRelationships = useMemo(() => edges.map((edge) => ({
    id: edge.id,
    from: edge.source,
    to: edge.target,
    caption: edge.relation_type === "contains" ? "隶属" : "上下游",
    color: edge.relation_type === "contains" ? "#64748b" : "#dc2626",
    width: edge.relation_type === "contains" ? 1 : 2
  })), [edges]);

  const industrySelector = (
    <label className="field">
      <span>行业</span>
      <select value={industryId} onChange={(event) => setIndustryId(event.target.value)}>
        <option value="">请选择行业</option>
        {industries.map((industry) => <option key={industry.id} value={industry.id}>{industry.name}</option>)}
      </select>
    </label>
  );
  const pageTabs = (
    <div className="page-tabs">
      <button type="button" className={pageMode === "graph" ? "active" : ""} onClick={() => setPageMode("graph")}>图谱展示</button>
      <button type="button" className={pageMode === "agent" ? "active" : ""} onClick={() => setPageMode("agent")}>Agent 工作流</button>
    </div>
  );

  if (pageMode === "agent") {
    return (
      <main className="agent-page-shell">
        <aside className="workflow-sidebar">
          <div className="brand"><GitBranch size={24} /><div><h1>Agent 工作流</h1><span>构建、校验、更新、导出</span></div></div>
          {pageTabs}
          <section className="panel"><div className="panel-title"><Database size={16} /><span>行业</span></div>{industrySelector}<button type="button" className="secondary-button" onClick={() => { void loadArtifacts(); void loadExports(); }} disabled={artifactLoading}><RefreshCw size={15} />刷新产物</button></section>
          <section className="workflow-list" aria-label="Agent 工作流状态">
            {workflowSteps.map((step, index) => (
              <article key={step.id} className={"workflow-step " + (step.done ? "done" : "pending")}>
                <div className="workflow-rail">{step.done ? <CheckCircle2 size={20} /> : <Circle size={20} />}{index < workflowSteps.length - 1 && <span />}</div>
                <div className="workflow-card">
                  <div className="workflow-card-title"><h3>{step.title}</h3><small>{step.done ? "已生成" : "待运行"}</small></div>
                  <p>{step.summary}</p>
                  {step.artifacts.length > 0 && <div className="workflow-artifacts">{step.artifacts.map((artifactName) => { const artifact = artifacts.find((item) => item.name === artifactName); return artifact?.exists ? <button key={artifactName} type="button" onClick={() => handleArtifactOpen(artifactName)}>{artifact.label}</button> : <span key={artifactName}>{artifact?.label ?? artifactName}</span>; })}</div>}
                  {step.action === "plan" && <button type="button" className="action-button" onClick={handleSearchPlan} disabled={agentBusy}>{agentBusy ? <span className="spinner" /> : <Search size={15} />}运行规划</button>}
                  {step.action === "skeleton" && <button type="button" className="action-button" onClick={handleBuildSkeleton} disabled={agentBusy}>{agentBusy ? <span className="spinner" /> : <Sparkles size={15} />}运行骨架</button>}{step.action === "branches" && <button type="button" className="action-button" onClick={handleBuildBranches} disabled={agentBusy || !hasArtifact("staged_level1_graph")}>{agentBusy ? <span className="spinner" /> : <GitBranch size={15} />}运行分支</button>}
                  {step.action === "final_validate" && <div className="button-grid vertical"><button type="button" className="action-button" onClick={handleFinalValidate} disabled={agentBusy || !hasArtifact("pre_validation_candidate_graph")}>{agentBusy ? <span className="spinner" /> : <CheckCircle2 size={15} />}运行最终校验</button><button type="button" className="secondary-button" onClick={() => handleApplyCandidate("candidate_graph")} disabled={agentBusy || !hasArtifact("candidate_graph")}>应用候选</button></div>}
                  {step.action === "update" && <div className="button-grid tight"><button type="button" className="secondary-button" onClick={() => handleUpdate("check_only")} disabled={agentBusy}>检查</button><button type="button" className="secondary-button" onClick={() => handleUpdate("propose")} disabled={agentBusy}>提案</button><button type="button" className="secondary-button" onClick={() => handleApplyCandidate("update_candidate_graph")} disabled={agentBusy || !hasArtifact("update_candidate_graph")}>应用候选</button></div>}
                  {step.action === "export" && <button type="button" className="action-button" onClick={handleExport} disabled={agentBusy}><Download size={15} />导出 CSV</button>}
                </div>
              </article>
            ))}
          </section>
        </aside>
        <section className="artifact-workspace">
          <header className="stage-header"><div><h2>Agent 产物展示</h2><p>{message}</p></div><div className="stats"><span>{existingArtifacts.length} 个产物</span><span>{exportPaths.length} 个 CSV</span>{activeRun && <button type="button" className={"run-drawer-toggle " + (isActiveRun(activeRun) ? "live" : "")} onClick={() => setRunDrawerOpen(true)}><Terminal size={14} />运行监控</button>}</div></header>
          <div className="artifact-layout">
            <aside className="artifact-index">
              <div className="panel-title"><FileText size={16} /><span>文件</span></div>
              <div className="artifact-list large">
                {existingArtifacts.map((artifact) => <button key={artifact.name} type="button" onClick={() => handleArtifactOpen(artifact.name)} className={selectedArtifact?.name === artifact.name ? "active" : ""}><span>{artifact.label}</span><small>{artifact.kind} · {Math.ceil(artifact.size_bytes / 1024)} KB</small></button>)}
                {!artifactLoading && existingArtifacts.length === 0 && <span className="muted">暂无 Agent 产物。运行构建或更新后会出现在这里。</span>}
                {artifactLoading && <span className="muted">读取中...</span>}
              </div>
              {exportPaths.length > 0 && <div className="path-list"><strong>CSV 导出</strong>{exportPaths.map((item) => <span key={item}>{item}</span>)}</div>}
            </aside>
            <section className="artifact-reader"><div className="artifact-reader-header"><div><h3>{selectedArtifact?.label ?? "产物预览"}</h3><span>{selectedArtifact?.path ?? "选择左侧文件或工作流节点中的产物"}</span></div>{selectedArtifact && <button type="button" className="artifact-delete-button" title="删除当前产物" aria-label="删除当前产物" onClick={handleArtifactDelete} disabled={artifactLoading}><Trash2 size={15} /></button>}</div><pre className="artifact-viewer full">{artifactPreview(selectedArtifact)}</pre></section>
          </div>
        </section>
        {activeRun && <aside className={"run-drawer " + (runDrawerOpen ? "open" : "")} aria-hidden={!runDrawerOpen}>
          <div className="run-drawer-header">
            <div><strong><Terminal size={17} />运行监控</strong><span>{activeRun.kind ?? "agent"} · {activeRun.status} · run {activeRun.run_id}</span></div>
            <button type="button" className="icon-button" aria-label="关闭运行监控" onClick={() => setRunDrawerOpen(false)}><X size={16} /></button>
          </div>
          <div className="run-drawer-body">
            <div className="run-current"><span>当前步骤</span><strong>{activeRun.current_step || "等待日志"}</strong></div>
            {activeRun.command.length > 0 && <div className="run-command"><span>命令</span><code>{activeRun.command.join(" ")}</code></div>}
            <pre ref={runLogRef} className="run-log">{activeRun.logs.length > 0 ? activeRun.logs.join("\n") : "等待后端日志..."}</pre>
          </div>
          <div className="run-drawer-footer">
            <button type="button" className="danger-button" onClick={handleCancelRun} disabled={!isActiveRun(activeRun)}><Square size={14} />中断运行</button>
          </div>
        </aside>}
      </main>
    );
  }

  return (
    <main className="app-shell graph-page">
      <aside className="sidebar">
        <div className="brand"><Network size={24} /><div><h1>产业链图谱</h1><span>图谱展示与问答</span></div></div>
        {pageTabs}
        <section className="panel"><div className="panel-title"><Database size={16} /><span>数据</span></div>{industrySelector}<button className="secondary-button" type="button" onClick={() => void loadGraph(defaultFilters)} disabled={graphLoading || !hasSelectedIndustry}><RefreshCw size={15} />刷新图谱</button></section>
        <section className="panel">
          <div className="panel-title"><Filter size={16} /><span>筛选</span></div>
          <label className="field"><span>关键词</span><div className="search-box"><Search size={16} /><input placeholder="节点名称或简介" value={draftFilters.q} onChange={(event) => setDraftFilters({ ...draftFilters, q: event.target.value })} onKeyDown={(event) => { if (event.key === "Enter") applyFilters(draftFilters); }} /></div></label>
          <div className="filter-group"><span>节点类型</span>{nodeTypeOptions.map((option) => <label key={option.value} className="check-row"><input type="checkbox" checked={draftFilters.chain_positions.includes(option.value)} onChange={() => setDraftFilters({ ...draftFilters, chain_positions: toggleValue(draftFilters.chain_positions, option.value) })} /><span className="dot" style={{ backgroundColor: option.color }} />{option.label}</label>)}{levelOptions.map((level) => <label key={level} className="check-row"><input type="checkbox" checked={draftFilters.levels.includes(level)} onChange={() => setDraftFilters({ ...draftFilters, levels: toggleValue(draftFilters.levels, level) })} /><span className="dot" style={{ backgroundColor: levelColor(level, maxVisibleLevel) }} />L{level}</label>)}</div>
          <div className="filter-group"><span>关系类型</span>{relationOptions.map((option) => <label key={option.value} className="check-row"><input type="checkbox" checked={draftFilters.relation_types.includes(option.value)} onChange={() => setDraftFilters({ ...draftFilters, relation_types: toggleValue(draftFilters.relation_types, option.value) })} />{option.label}</label>)}</div>
          <div className="toolbar"><button type="button" title="应用筛选" onClick={() => applyFilters(draftFilters)} disabled={graphLoading || !hasSelectedIndustry}>{graphLoading ? <span className="spinner dark" /> : <Search size={16} />}</button><button type="button" title="重置筛选" onClick={() => applyFilters(defaultFilters)} disabled={graphLoading || !hasSelectedIndustry}><RefreshCw size={16} /></button></div>
        </section>
      </aside>
      <section className="graph-stage">
        <header className="stage-header"><div><h2>Neo4j 图谱可视化</h2><p>{message}</p></div><div className="stats"><div className="layout-switch" aria-label="图谱布局"><button type="button" title="力导向布局" className={layoutMode === "forceDirected" ? "active" : ""} onClick={() => setLayoutMode("forceDirected")}>力导向</button><button type="button" title="层级布局" className={layoutMode === "hierarchical" ? "active" : ""} onClick={() => setLayoutMode("hierarchical")}>层级</button></div><span>{nodes.length} 节点</span><span>{edges.length} 关系</span><button type="button" title="重新居中图谱" className="fit-button" onClick={() => nvlRef.current?.fit?.(nodes.map((node) => node.id))} disabled={nodes.length === 0}><RefreshCw size={14} /></button></div></header>
        <div className="graph-canvas">{nodes.length > 0 ? <InteractiveNvlWrapper ref={nvlRef} nodes={nvlNodes} rels={nvlRelationships} layout={layoutMode} layoutOptions={layoutMode === "hierarchical" ? { direction: "right", packing: "bin" } : { enableCytoscape: true }} nvlOptions={{ disableTelemetry: true, renderer: "canvas", minZoom: 0.02, maxZoom: 8, allowDynamicMinZoom: true }} mouseEventCallbacks={{ onNodeClick: (node: { id: string }) => handleNodeClick(node.id), onPan: true, onZoom: true, onZoomAndPan: true, onDrag: true, onDragStart: true, onDragEnd: true }} /> : <div className="empty-state"><Sparkles size={28} /><span>{graphLoading ? "加载中" : hasSelectedIndustry ? "该行业暂无正式图谱，请到 Agent 工作流生成 graph.json" : "请选择行业后查看图谱"}</span></div>}</div>
      </section>
      <aside className="inspector">
        <section ref={detailPanelRef} className="panel detail-panel"><div className="panel-title"><Network size={16} /><span>节点审计</span></div>{selectedNode ? <><h3>{selectedNode.name}</h3><div className="meta-row"><span>L{selectedNode.level}</span><span>{selectedNode.node_type}</span>{selectedNode.is_key_node && <span>关键节点</span>}<span>置信度 {formatPercent(selectedNode.confidence)}</span></div><p>{selectedNode.business_description || selectedNode.description || "暂无描述"}</p><dl className="kv-list"><div><dt>行业</dt><dd>{selectedNode.industry || selectedNode.industry_id}</dd></div><div><dt>层级</dt><dd>L{selectedNode.level}</dd></div><div><dt>更新时间</dt><dd>{selectedNode.updated_at || "-"}</dd></div></dl>{selectedNode.tags.length > 0 && <div className="chips compact">{selectedNode.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}<div className="node-link-list compact"><strong>父节点</strong>{parentNode ? <button type="button" onClick={() => handleNodeClick(parentNode.id)}>{parentNode.name}<small>{parentNode.id}</small></button> : selectedNode.parent_id ? <button type="button" onClick={() => handleNodeClick(selectedNode.parent_id!)}>{nodeLabel(selectedNode.parent_id)}</button> : <span>暂无父节点</span>}</div><div className="node-link-list compact two-col"><strong>子节点</strong>{childNodes.length > 0 ? childNodes.map((node) => <button key={node.id} type="button" onClick={() => handleNodeClick(node.id)}>{node.name}<small>{node.id}</small></button>) : <span>暂无子节点</span>}</div><div className="source-list"><strong>来源 URL</strong>{selectedNode.source_urls.length > 0 ? selectedNode.source_urls.slice(0, 5).map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>) : <span>暂无来源</span>}</div><div className="source-list"><strong>证据 ID</strong>{selectedNode.evidence_ids.length > 0 ? <span>{selectedNode.evidence_ids.join(", ")}</span> : <span>暂无证据</span>}</div><div className="neighbor-list"><strong>邻接关系</strong>{otherNeighborEdges.slice(0, 10).map((edge) => <button key={edge.id} type="button" onClick={() => handleNodeClick(edge.source === selectedNode.id ? edge.target : edge.source)}><span>{relationSentence(edge)}</span><small>置信度 {formatPercent(edge.confidence)}</small></button>)}{otherNeighborEdges.length === 0 && <span>暂无其他邻接关系</span>}</div></> : <p className="muted">点击图谱节点查看描述、层级、来源、证据、置信度和邻接关系。</p>}</section>
        <section className="panel ask-panel"><div className="panel-title"><Bot size={16} /><span>AI 问答</span></div><textarea value={question} placeholder="例如：该行业的上游主要有哪些？" onChange={(event) => setQuestion(event.target.value)} /><button className="action-button" type="button" title="发送问题" onClick={handleAsk} disabled={asking || !question.trim() || !hasSelectedIndustry}>{asking ? <span className="spinner" /> : <Send size={16} />}<span>{asking ? "思考中" : "发送问题"}</span></button>{asking && <div className="thinking" aria-live="polite"><span>正在基于图谱检索上下文</span><i /><i /><i /></div>}{answer && <div className="answer"><strong>回答</strong><p>{answer.answer}</p><strong>引用节点</strong><div className="chips">{answer.context_nodes.slice(0, 12).map((node) => <span key={node.id}>{node.name}</span>)}</div><strong>引用关系</strong><div className="chips">{answer.context_edges.slice(0, 8).map((edge) => <span key={edge.id}>{relationLabel(edge.relation_type)}</span>)}</div></div>}</section>
      </aside>
    </main>
  );
}












