import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import { Bot, CheckCircle2, ChevronDown, ChevronRight, Circle, Database, Download, FileText, Filter, GitBranch, Network, RefreshCw, Search, Send, Sparkles, Square, Terminal, Trash2, X } from "lucide-react";
import { applyCandidateGraph, askGraph, attachCompanies, buildAgentBranches, buildAgentSkeleton, buildL2FlowRelations, cancelAgentRun, createSearchPlan, deleteAgentArtifact, exportAllOfflineGraphs, exportCurrentOfflineGraph, exportIndustryCsv, fetchAgentArtifact, fetchAgentArtifacts, fetchAgentRun, fetchGraph, fetchIndustries, fetchIndustryExports, fetchNeighbors, fetchNodeCompanies, fetchNodeL2FlowRelations, finalValidateAgentGraph, updateAgentGraph } from "./api";
import type { AgentArtifact, AgentArtifactContent, AgentRunResponse, AskResponse, CandidateGraphType, ChainPosition, CompanyAttachmentItem, GraphEdge, GraphFilters, GraphNode, Industry, NodeCompaniesResponse, NodeL2FlowRelationsResponse, RelationType, UpdateMode } from "./types";

const nodeTypeOptions: Array<{ value: ChainPosition; label: string; color: string }> = [
  { value: "root", label: "根节点", color: "#334155" },
  { value: "support", label: "支撑节点", color: "#64748b" }
];
const relationOptions: Array<{ value: RelationType; label: string }> = [
  { value: "contains", label: "隶属关系" },
  { value: "upstream_downstream", label: "一级上下游关系" }
];
type LayoutMode = "forceDirected" | "hierarchical";
type CompanyScope = "listed" | "all";
type PageMode = "graph" | "agent";
const defaultFilters: GraphFilters = { q: "", chain_positions: [], relation_types: [], levels: [] };
const activeRunStatuses = new Set(["running", "canceling"]);
const INSPECTOR_WIDTH_STORAGE_KEY = "industry-chain-graph.inspector-width";
const DEFAULT_INSPECTOR_WIDTH = 420;
const MIN_INSPECTOR_WIDTH = 320;
const MAX_INSPECTOR_WIDTH = 720;
const DESKTOP_LAYOUT_BREAKPOINT = 1280;
const DESKTOP_SIDEBAR_AND_GRAPH_MIN_WIDTH = 820;

function clampInspectorWidth(value: number) {
  const boundedValue = Math.min(MAX_INSPECTOR_WIDTH, Math.max(MIN_INSPECTOR_WIDTH, value));
  if (typeof window === "undefined" || window.innerWidth <= DESKTOP_LAYOUT_BREAKPOINT) return Math.round(boundedValue);
  const availableWidth = Math.max(MIN_INSPECTOR_WIDTH, window.innerWidth - DESKTOP_SIDEBAR_AND_GRAPH_MIN_WIDTH);
  return Math.round(Math.min(boundedValue, availableWidth));
}

function initialInspectorWidth() {
  if (typeof window === "undefined") return DEFAULT_INSPECTOR_WIDTH;
  try {
    const storedWidth = Number(window.localStorage.getItem(INSPECTOR_WIDTH_STORAGE_KEY));
    return clampInspectorWidth(Number.isFinite(storedWidth) && storedWidth > 0 ? storedWidth : DEFAULT_INSPECTOR_WIDTH);
  } catch {
    return clampInspectorWidth(DEFAULT_INSPECTOR_WIDTH);
  }
}

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
function companyGraphNodeId(companyId: string) {
  return "company:" + companyId;
}
function artifactPreview(content: AgentArtifactContent | null) {
  if (!content) return "选择一个产物查看内容。";
  return typeof content.content === "string" ? content.content : JSON.stringify(content.content, null, 2);
}
function companyRunProgress(logs: string[]) {
  const line = [...logs].reverse().find((item) => item.includes("公司匹配进度"));
  const match = line?.match(/(\d+)\/(\d+) \(([\d.]+)%\)(.*)$/);
  if (!match) return null;
  const current = Number(match[1]);
  const total = Number(match[2]);
  return {
    current,
    total,
    percent: Math.min(100, Number(match[3])),
    detail: match[4].replace(/^\s*·\s*/, "")
  };
}
export function App({ offline = false }: { offline?: boolean } = {}) {
  const nvlRef = useRef<any>(null);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const runLogRef = useRef<HTMLPreElement | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(initialInspectorWidth);
  const [inspectorResizing, setInspectorResizing] = useState(false);
  const [pageMode, setPageMode] = useState<PageMode>("graph");
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [industryId, setIndustryId] = useState("");
  const [draftFilters, setDraftFilters] = useState<GraphFilters>(defaultFilters);
  const [appliedFilters, setAppliedFilters] = useState<GraphFilters>(defaultFilters);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<CompanyAttachmentItem | null>(null);
  const [neighborEdges, setNeighborEdges] = useState<GraphEdge[]>([]);
  const [expandedL2FlowNodeIds, setExpandedL2FlowNodeIds] = useState<string[]>([]);
  const [l2FlowResponses, setL2FlowResponses] = useState<Record<string, NodeL2FlowRelationsResponse>>({});
  const [l2FlowLoading, setL2FlowLoading] = useState(false);
  const [expandedCompanyNodeIds, setExpandedCompanyNodeIds] = useState<string[]>([]);
  const [companyResponses, setCompanyResponses] = useState<Record<string, NodeCompaniesResponse>>({});
  const [companyLoading, setCompanyLoading] = useState(false);
  const [companyScope, setCompanyScope] = useState<CompanyScope>("listed");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [useShenwanReference, setUseShenwanReference] = useState(false);
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
  const selectedCompanyResponse = selectedNode ? companyResponses[selectedNode.id] ?? null : null;
  const selectedCompaniesExpanded = Boolean(selectedNode && expandedCompanyNodeIds.includes(selectedNode.id));
  const selectedL2FlowResponse = selectedNode ? l2FlowResponses[selectedNode.id] ?? null : null;
  const selectedL2FlowExpanded = Boolean(selectedNode && expandedL2FlowNodeIds.includes(selectedNode.id));
  const existingArtifacts = artifacts.filter((artifact) => artifact.exists);
  const activeCompanyProgress = activeRun?.kind === "attach_companies" ? companyRunProgress(activeRun.logs) : null;
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
    { id: "skeleton", title: "一级骨架构建", summary: "默认按原流程研究行业边界与分类轴；也可在运行前手动启用申万分类树作为辅助参考。", done: hasArtifact("staged_level1_graph") && hasArtifact("staged_level1_evaluation"), artifacts: ["staged_level1_blueprint", ...(hasArtifact("staged_indunamesw_reference") ? ["staged_indunamesw_reference"] : []), "agent_request_prompt", "staged_level1_graph", "staged_level1_evaluation", "staged_quality_opinions"], action: "skeleton" },
    { id: "branches", title: "分支扩展与评估", summary: "基于一级骨架逐分支扩展，每条分支单独评估；不通过时只修当前分支，最后合并为校验前候选图谱。", done: hasArtifact("pre_validation_candidate_graph"), artifacts: ["staged_branch_fragments", "staged_branch_evaluations", "staged_quality_opinions", "staged_merged_graph", "staged_errors", "agent_raw_response", "pre_validation_candidate_graph"], action: "branches" },
    { id: "validate_repair", title: "最终校验与格式修复", summary: "单轮硬规则校验；只有不通过才请求百炼做格式修复，不再做整图质量审查。", done: hasArtifact("candidate_graph") && hasArtifact("validation_report"), artifacts: ["validation_agent_request_prompt", "validation_agent_raw_response", "format_repair_report", "candidate_graph", "sources", "validation_report", "validation_report_json", "review_queue"], action: "final_validate" },
    { id: "l2_flow", title: "L2 上下游关系建边", summary: "L2 建边完成后执行确定性跨层投影：A→B 同步生成 parent(A)→B 和 A→parent(B)；后处理不调用模型，也不改变原始 L2 边。", done: hasArtifact("l2_flow_relations") && hasArtifact("l2_flow_relation_validation_report"), artifacts: ["l2_flow_candidate_pairs", "l2_flow_pair_decisions", "l2_flow_relation_candidate", "l2_flow_relations", "l2_flow_relation_raw_responses", "l2_flow_relation_validation_report", "l2_flow_relation_validation_report_json"], action: "l2_flow" },
    { id: "company_attach", title: "公司节点挂载（硬校验）", summary: "在正式主图和 L2 上下游关系完成后，筛选全链条候选公司并挂载到最深节点；图谱画布按需展开公司。", done: hasArtifact("company_attachments") && hasArtifact("company_attachment_validation_report"), artifacts: ["company_scope", "company_attachment_candidate", "company_attachments", "company_attachment_raw_responses", "company_attachment_validation_report", "company_attachment_validation_report_json", "company_attachment_repair_report"], action: "company_attach" },
    { id: "update", title: "增量更新", summary: "联网搜索新增证据，默认生成 no_change 或 update_proposal。", done: hasArtifact("update_proposal") || hasArtifact("update_report"), artifacts: ["update_agent_request_prompt", "update_proposal", "update_candidate_graph", "update_report", "update_agent_raw_response"], action: "update" },
    { id: "export", title: "CSV 交付", summary: "按 mentor 格式导出节点 CSV 和关系 CSV。", done: exportPaths.length > 0, artifacts: [], action: "export" }
  ];

  async function loadIndustries() {
    try {
      const data = await fetchIndustries();
      setIndustries(data);
      if (data.length > 0) {
        setIndustryId((current) => (data.some((industry) => industry.id === current) ? current : (offline ? data[0].id : "")));
      }
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
      const result = await buildAgentSkeleton(industryId, industryName, useShenwanReference);
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
  async function handleAttachCompanies() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (!hasArtifact("graph")) {
      setMessage("请先在最终校验步骤应用候选图谱，生成正式 graph.json 后再挂载公司。");
      return;
    }
    if (!hasArtifact("l2_flow_relations")) {
      setMessage("请先运行 L2 上下游关系建边并通过硬规则校验，再挂载公司。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await attachCompanies(industryId);
      await trackAgentRun(result, "公司节点挂载完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "公司节点挂载失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleBuildL2FlowRelations() {
    if (!hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (!hasArtifact("graph")) {
      setMessage("请先应用候选图谱，生成正式 graph.json 后再运行 L2 上下游关系建边。");
      return;
    }
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = await buildL2FlowRelations(industryId);
      await trackAgentRun(result, "L2 上下游关系建边完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "L2 上下游关系建边失败");
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
      setExportPaths([result.industry_node_csv, result.industrynode_edge_csv, result.industrynode_industry_edge_csv, result.industrynode_node_csv, result.company_node_csv, result.company_edge_csv].filter((path): path is string => Boolean(path)));
      setMessage(result.company_node_csv ? "六类产业链与公司 CSV 导出完成。" : "四类产业链 CSV 导出完成；未找到有效公司挂载附件。");
      await loadExports();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "CSV 导出失败");
    } finally {
      setAgentBusy(false);
    }
  }
  async function handleOfflineExport(scope: "current" | "all") {
    if (scope === "current" && !hasSelectedIndustry) {
      setMessage("请先选择行业。");
      return;
    }
    if (scope === "all" && !window.confirm("将刷新全部正式图谱的交付目录和 CSV，并覆盖已有离线 HTML。是否继续？")) return;
    setAgentBusy(true);
    setSelectedArtifact(null);
    try {
      const result = scope === "current"
        ? await exportCurrentOfflineGraph(industryId)
        : await exportAllOfflineGraphs();
      const successMessage = scope === "current"
        ? `已生成「${industryName}图谱」交付目录（HTML + CSV）`
        : "已生成全部行业交付目录和 25行业产业链图谱.html";
      await trackAgentRun(result, successMessage);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "离线图谱导出失败");
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
    if (nodeId.startsWith("company:")) {
      const companyId = nodeId.slice("company:".length);
      const matches = Object.values(companyResponses).flatMap((response) => response.items.filter((item) => item.company_id === companyId));
      if (matches.length > 0) {
        const first = matches[0];
        const attachmentByNode = new Map(first.direct_attachments.map((item) => [item.node_id, item]));
        matches.slice(1).forEach((item) => item.direct_attachments.forEach((attachment) => attachmentByNode.set(attachment.node_id, attachment)));
        const directAttachments = Array.from(attachmentByNode.values());
        setSelectedCompany({
          ...first,
          direct_attachments: directAttachments,
          direct_node_ids: directAttachments.map((item) => item.node_id),
          direct_node_names: directAttachments.map((item) => item.node_name)
        });
        setSelectedNode(null);
        setNeighborEdges([]);
      }
      return;
    }
    if (!hasSelectedIndustry) return;
    const current = nodes.find((node) => node.id === nodeId)
      ?? Object.values(l2FlowResponses).flatMap((response) => response.nodes).find((node) => node.id === nodeId)
      ?? null;
    setSelectedNode(current);
    setSelectedCompany(null);
    if (!current) return;
    try {
      const data = await fetchNeighbors(industryId, nodeId);
      setNeighborEdges(data.edges);
    } catch {
      setNeighborEdges([]);
    }
  }
  async function handleToggleCompanies() {
    if (!selectedNode) return;
    const nodeId = selectedNode.id;
    if (expandedCompanyNodeIds.includes(nodeId)) {
      setExpandedCompanyNodeIds((current) => current.filter((id) => id !== nodeId));
      return;
    }
    const cached = companyResponses[nodeId];
    if (cached?.status === "ready") {
      setExpandedCompanyNodeIds((current) => [...current, nodeId]);
      return;
    }
    setCompanyLoading(true);
    try {
      const response = await fetchNodeCompanies(industryId, nodeId, 500, 0, false, companyScope === "listed");
      setCompanyResponses((current) => ({ ...current, [nodeId]: response }));
      if (response.status === "ready") {
        setExpandedCompanyNodeIds((current) => current.includes(nodeId) ? current : [...current, nodeId]);
      }
    } catch (error) {
      setCompanyResponses((current) => ({
        ...current,
        [nodeId]: { industry_id: industryId, node_id: nodeId, status: "invalid" as const, message: error instanceof Error ? error.message : "公司节点读取失败", total: 0, limit: 500, offset: 0, items: [] }
      }));
    } finally {
      setCompanyLoading(false);
    }
  }
  async function handleCompanyScopeChange(scope: CompanyScope) {
    if (scope === companyScope) return;
    setCompanyScope(scope);
    // Cached responses were fetched under the previous scope, so drop them and
    // refetch whatever is currently expanded on the canvas.
    setCompanyResponses({});
    setSelectedCompany(null);
    const expanded = expandedCompanyNodeIds;
    if (expanded.length === 0) return;
    setCompanyLoading(true);
    try {
      const responses = await Promise.all(
        expanded.map((nodeId) => fetchNodeCompanies(industryId, nodeId, 500, 0, false, scope === "listed"))
      );
      setCompanyResponses(Object.fromEntries(expanded.map((nodeId, index) => [nodeId, responses[index]])));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "切换公司范围失败");
      setExpandedCompanyNodeIds([]);
    } finally {
      setCompanyLoading(false);
    }
  }
  async function handleToggleL2FlowRelations() {
    if (!selectedNode || (selectedNode.level !== 1 && selectedNode.level !== 2)) return;
    const nodeId = selectedNode.id;
    if (expandedL2FlowNodeIds.includes(nodeId)) {
      setExpandedL2FlowNodeIds((current) => current.filter((id) => id !== nodeId));
      return;
    }
    const cached = l2FlowResponses[nodeId];
    if (cached?.status === "ready") {
      setExpandedL2FlowNodeIds((current) => current.includes(nodeId) ? current : [...current, nodeId]);
      return;
    }
    setL2FlowLoading(true);
    try {
      const response = await fetchNodeL2FlowRelations(industryId, nodeId);
      setL2FlowResponses((current) => ({ ...current, [nodeId]: response }));
      if (response.status === "ready") {
        setExpandedL2FlowNodeIds((current) => current.includes(nodeId) ? current : [...current, nodeId]);
      }
    } catch (error) {
      setL2FlowResponses((current) => ({
        ...current,
        [nodeId]: { industry_id: industryId, node_id: nodeId, status: "invalid", message: error instanceof Error ? error.message : "上下游关系读取失败", total: 0, nodes: [], edges: [] }
      }));
    } finally {
      setL2FlowLoading(false);
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
    setSelectedCompany(null);
    setNeighborEdges([]);
    setExpandedL2FlowNodeIds([]);
    setL2FlowResponses({});
    setExpandedCompanyNodeIds([]);
    setCompanyResponses({});
    setSelectedArtifact(null);
    if (hasSelectedIndustry) {
      void loadGraph(defaultFilters);
      if (!offline) {
        void loadArtifacts();
        void loadExports();
      }
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
  }, [selectedNode?.id, selectedCompany?.company_id]);

  useEffect(() => {
    if (!runLogRef.current) return;
    runLogRef.current.scrollTop = runLogRef.current.scrollHeight;
  }, [activeRun?.logs.length]);

  useEffect(() => {
    if (nodes.length === 0 || pageMode !== "graph") return;
    const timer = window.setTimeout(() => nvlRef.current?.fit?.(nodes.map((node) => node.id)), 500);
    return () => window.clearTimeout(timer);
  }, [nodes, pageMode]);

  useEffect(() => {
    try {
      window.localStorage.setItem(INSPECTOR_WIDTH_STORAGE_KEY, String(inspectorWidth));
    } catch {
      // Offline file previews and privacy modes may disable localStorage.
    }
  }, [inspectorWidth]);

  useEffect(() => {
    const handleWindowResize = () => {
      if (window.innerWidth > DESKTOP_LAYOUT_BREAKPOINT) {
        setInspectorWidth((currentWidth) => clampInspectorWidth(currentWidth));
      }
    };
    window.addEventListener("resize", handleWindowResize);
    return () => window.removeEventListener("resize", handleWindowResize);
  }, []);

  useEffect(() => {
    if (!inspectorResizing) return;
    const handlePointerMove = (event: PointerEvent) => {
      setInspectorWidth(clampInspectorWidth(window.innerWidth - event.clientX));
    };
    const finishResize = () => setInspectorResizing(false);
    document.body.classList.add("inspector-resizing");
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", finishResize);
    window.addEventListener("pointercancel", finishResize);
    return () => {
      document.body.classList.remove("inspector-resizing");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishResize);
      window.removeEventListener("pointercancel", finishResize);
    };
  }, [inspectorResizing]);


  const expandedL2FlowGroups = useMemo(
    () => expandedL2FlowNodeIds
      .map((nodeId) => ({ nodeId, response: l2FlowResponses[nodeId] }))
      .filter((item): item is { nodeId: string; response: NodeL2FlowRelationsResponse } => item.response?.status === "ready"),
    [expandedL2FlowNodeIds, l2FlowResponses]
  );
  const l2FlowOverlayNodes = useMemo(() => {
    const result = new Map<string, GraphNode>();
    expandedL2FlowGroups.forEach(({ response }) => response.nodes.forEach((node) => result.set(node.id, node)));
    return Array.from(result.values());
  }, [expandedL2FlowGroups]);
  const l2FlowOverlayEdges = useMemo(() => {
    const result = new Map<string, GraphEdge>();
    expandedL2FlowGroups.forEach(({ response }) => response.edges.forEach((edge) => result.set(edge.id, edge)));
    return Array.from(result.values());
  }, [expandedL2FlowGroups]);
  const nodeById = useMemo(() => {
    const result = new Map(nodes.map((node) => [node.id, node]));
    l2FlowOverlayNodes.forEach((node) => result.set(node.id, node));
    if (selectedNode) result.set(selectedNode.id, selectedNode);
    return result;
  }, [nodes, l2FlowOverlayNodes, selectedNode]);
  const parentNode = selectedNode?.parent_id ? nodeById.get(selectedNode.parent_id) : null;
  const childEdges = selectedNode ? neighborEdges.filter((edge) => edge.relation_type === "contains" && edge.source === selectedNode.id) : [];
  const childNodes = childEdges.map((edge) => nodeById.get(edge.target)).filter((node): node is GraphNode => Boolean(node));
  const otherNeighborEdges = selectedNode
    ? neighborEdges.filter((edge) => !(edge.relation_type === "contains" && edge.source === selectedNode.id))
    : [];
  const selectedL2UpstreamEdges = selectedNode && selectedL2FlowResponse?.status === "ready"
    ? selectedL2FlowResponse.edges.filter((edge) => edge.target === selectedNode.id)
    : [];
  const selectedL2DownstreamEdges = selectedNode && selectedL2FlowResponse?.status === "ready"
    ? selectedL2FlowResponse.edges.filter((edge) => edge.source === selectedNode.id)
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

  const expandedCompanyGroups = useMemo(
    () => expandedCompanyNodeIds
      .map((nodeId) => ({ nodeId, response: companyResponses[nodeId] }))
      .filter((item): item is { nodeId: string; response: NodeCompaniesResponse } => item.response?.status === "ready"),
    [expandedCompanyNodeIds, companyResponses]
  );
  const companyNvlNodes = useMemo(() => {
    const result = new Map<string, { id: string; caption: string; size: number; color: string }>();
    expandedCompanyGroups.forEach(({ response }) => response.items.forEach((company) => {
      const id = companyGraphNodeId(company.company_id);
      if (!result.has(id)) result.set(id, { id, caption: company.name, size: 17, color: "#16a34a" });
    }));
    return Array.from(result.values());
  }, [expandedCompanyGroups]);
  const companyNvlRelationships = useMemo(
    () => expandedCompanyGroups.flatMap(({ nodeId, response }) => response.items.map((company) => ({
      id: `${nodeId}__company_attachment__${company.company_id}`,
      from: nodeId,
      to: companyGraphNodeId(company.company_id),
      caption: "公司",
      color: "#16a34a",
      width: 1
    }))),
    [expandedCompanyGroups]
  );
  const nvlNodes = useMemo(() => {
    const result = new Map<string, { id: string; caption: string; size: number; color: string }>();
    [...nodes, ...l2FlowOverlayNodes].forEach((node) => result.set(node.id, {
      id: node.id,
      caption: node.name,
      size: node.level === 0 ? 50 : node.is_key_node ? 38 : Math.max(18, 34 - node.level * 3),
      color: levelColor(node.level, maxVisibleLevel)
    }));
    companyNvlNodes.forEach((node) => result.set(node.id, node));
    return Array.from(result.values());
  }, [nodes, l2FlowOverlayNodes, maxVisibleLevel, companyNvlNodes]);
  const nvlRelationships = useMemo(() => {
    const result = new Map<string, { id: string; from: string; to: string; caption: string; color: string; width: number }>();
    edges.forEach((edge) => result.set(edge.id, {
      id: edge.id,
      from: edge.source,
      to: edge.target,
      caption: edge.relation_type === "contains" ? "隶属" : "上下游",
      color: edge.relation_type === "contains" ? "#64748b" : "#dc2626",
      width: edge.relation_type === "contains" ? 1 : 2
    }));
    l2FlowOverlayEdges.forEach((edge) => result.set(edge.id, {
      id: edge.id,
      from: edge.source,
      to: edge.target,
      caption: edge.relation_layer === "l1_l2_flow_projection" ? "L1-L2 上下游" : "L2 上下游",
      color: edge.relation_layer === "l1_l2_flow_projection" ? "#7c3aed" : "#f97316",
      width: 3
    }));
    companyNvlRelationships.forEach((edge) => result.set(edge.id, edge));
    return Array.from(result.values());
  }, [edges, l2FlowOverlayEdges, companyNvlRelationships]);

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
      {!offline && <button type="button" className={pageMode === "agent" ? "active" : ""} onClick={() => setPageMode("agent")}>Agent 工作流</button>}
    </div>
  );

  if (!offline && pageMode === "agent") {
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
                  {step.action === "skeleton" && (
                    <div className="skeleton-run-options">
                      <label className="workflow-checkbox">
                        <input
                          type="checkbox"
                          checked={useShenwanReference}
                          onChange={(event) => setUseShenwanReference(event.target.checked)}
                          disabled={agentBusy}
                        />
                        <span>
                          <strong>参考申万分类</strong>
                          <em>勾选后会额外调用 qwen3.7-plus 筛选申万分类树；不勾选则完全按原骨架流程运行。</em>
                        </span>
                      </label>
                      <button type="button" className="action-button" onClick={handleBuildSkeleton} disabled={agentBusy}>{agentBusy ? <span className="spinner" /> : <Sparkles size={15} />}运行骨架</button>
                    </div>
                  )}
                  {step.action === "branches" && <button type="button" className="action-button" onClick={handleBuildBranches} disabled={agentBusy || !hasArtifact("staged_level1_graph")}>{agentBusy ? <span className="spinner" /> : <GitBranch size={15} />}运行分支</button>}
                  {step.action === "final_validate" && <div className="button-grid vertical"><button type="button" className="action-button" onClick={handleFinalValidate} disabled={agentBusy || !hasArtifact("pre_validation_candidate_graph")}>{agentBusy ? <span className="spinner" /> : <CheckCircle2 size={15} />}运行最终校验</button><button type="button" className="secondary-button" onClick={() => handleApplyCandidate("candidate_graph")} disabled={agentBusy || !hasArtifact("candidate_graph")}>应用候选</button></div>}
                  {step.action === "l2_flow" && <button type="button" className="action-button" onClick={handleBuildL2FlowRelations} disabled={agentBusy || !hasArtifact("graph")}>{agentBusy ? <span className="spinner" /> : <Network size={15} />}运行 L2 建边</button>}
                  {step.action === "company_attach" && <button type="button" className="action-button" onClick={handleAttachCompanies} disabled={agentBusy || !hasArtifact("graph") || !hasArtifact("l2_flow_relations")}>{agentBusy ? <span className="spinner" /> : <Network size={15} />}运行公司挂载</button>}
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
            {activeRun.kind === "attach_companies" ? <div className="company-run-progress">
              <div className="company-run-progress-head"><strong>{activeCompanyProgress ? `公司匹配 ${activeCompanyProgress.current}/${activeCompanyProgress.total}` : "准备公司挂载"}</strong><span>{activeCompanyProgress ? `${activeCompanyProgress.percent.toFixed(1)}%` : activeRun.current_step || "等待运行"}</span></div>
              <div className="company-run-progress-track"><i style={{ width: `${activeCompanyProgress?.percent ?? 0}%` }} /></div>
              <small>{activeCompanyProgress?.detail || "正在规划候选范围或等待首个匹配批次完成。"}</small>
            </div> : <pre ref={runLogRef} className="run-log">{activeRun.logs.length > 0 ? activeRun.logs.join("\n") : "等待后端日志..."}</pre>}
          </div>
          <div className="run-drawer-footer">
            <button type="button" className="danger-button" onClick={handleCancelRun} disabled={!isActiveRun(activeRun)}><Square size={14} />中断运行</button>
          </div>
        </aside>}
      </main>
    );
  }

  return (
    <main className="app-shell graph-page" style={{ "--inspector-width": `${inspectorWidth}px` } as CSSProperties}>
      <aside className="sidebar">
        <div className="brand"><Network size={24} /><div><h1>产业链图谱</h1><span>{offline ? "离线只读展示版" : "图谱展示与问答"}</span></div></div>
        {pageTabs}
        <section className="panel"><div className="panel-title"><Database size={16} /><span>数据</span></div>{industrySelector}<button className="secondary-button" type="button" onClick={() => void loadGraph(defaultFilters)} disabled={graphLoading || !hasSelectedIndustry}><RefreshCw size={15} />刷新图谱</button></section>
        {!offline && <section className="panel offline-export-panel"><div className="panel-title"><Download size={16} /><span>离线交付</span></div><button className="action-button" type="button" onClick={() => void handleOfflineExport("current")} disabled={agentBusy || !hasSelectedIndustry}><Download size={15} />导出当前图谱</button><button className="secondary-button" type="button" onClick={() => void handleOfflineExport("all")} disabled={agentBusy}><Download size={15} />导出所有图谱</button><small className="filter-hint">当前行业导出为“行业名图谱”目录；全部导出会在交付根目录生成“25行业产业链图谱.html”。</small></section>}
        <section className="panel">
          <div className="panel-title"><Filter size={16} /><span>筛选</span></div>
          <label className="field"><span>关键词</span><div className="search-box"><Search size={16} /><input placeholder="节点名称或简介" value={draftFilters.q} onChange={(event) => setDraftFilters({ ...draftFilters, q: event.target.value })} onKeyDown={(event) => { if (event.key === "Enter") applyFilters(draftFilters); }} /></div></label>
          <div className="filter-group"><span>节点类型</span>{nodeTypeOptions.map((option) => <label key={option.value} className="check-row"><input type="checkbox" checked={draftFilters.chain_positions.includes(option.value)} onChange={() => setDraftFilters({ ...draftFilters, chain_positions: toggleValue(draftFilters.chain_positions, option.value) })} /><span className="dot" style={{ backgroundColor: option.color }} />{option.label}</label>)}{levelOptions.map((level) => <label key={level} className="check-row"><input type="checkbox" checked={draftFilters.levels.includes(level)} onChange={() => setDraftFilters({ ...draftFilters, levels: toggleValue(draftFilters.levels, level) })} /><span className="dot" style={{ backgroundColor: levelColor(level, maxVisibleLevel) }} />L{level}</label>)}</div>
          <div className="filter-group"><span>关系类型</span>{relationOptions.map((option) => <label key={option.value} className="check-row"><input type="checkbox" checked={draftFilters.relation_types.includes(option.value)} onChange={() => setDraftFilters({ ...draftFilters, relation_types: toggleValue(draftFilters.relation_types, option.value) })} />{option.label}</label>)}</div>
          <div className="filter-group"><span>公司范围</span><div className="layout-switch company-scope-switch" aria-label="公司范围"><button type="button" title="只显示境内上市公司" className={companyScope === "listed" ? "active" : ""} onClick={() => void handleCompanyScopeChange("listed")} disabled={companyLoading}>仅境内上市</button><button type="button" title="显示全部挂载公司" className={companyScope === "all" ? "active" : ""} onClick={() => void handleCompanyScopeChange("all")} disabled={companyLoading}>全部</button></div><small className="filter-hint">{companyScope === "listed" ? "展开节点时只挂载境内上市公司。" : "展开节点时挂载全部公司，含非境内上市主体。"}</small></div>
          <div className="toolbar"><button type="button" title="应用筛选" onClick={() => applyFilters(draftFilters)} disabled={graphLoading || !hasSelectedIndustry}>{graphLoading ? <span className="spinner dark" /> : <Search size={16} />}</button><button type="button" title="重置筛选" onClick={() => applyFilters(defaultFilters)} disabled={graphLoading || !hasSelectedIndustry}><RefreshCw size={16} /></button></div>
        </section>
      </aside>
      <section className="graph-stage">
        <header className="stage-header"><div><h2>{offline ? "产业链图谱可视化" : "Neo4j 图谱可视化"}</h2><p>{message}</p></div><div className="stats"><div className="layout-switch" aria-label="图谱布局"><button type="button" title="力导向布局" className={layoutMode === "forceDirected" ? "active" : ""} onClick={() => setLayoutMode("forceDirected")}>力导向</button><button type="button" title="层级布局" className={layoutMode === "hierarchical" ? "active" : ""} onClick={() => setLayoutMode("hierarchical")}>层级</button></div><span>{nvlNodes.length} 节点</span><span>{nvlRelationships.length} 关系</span><button type="button" title="重新居中图谱" className="fit-button" onClick={() => nvlRef.current?.fit?.(nvlNodes.map((node) => node.id))} disabled={nvlNodes.length === 0}><RefreshCw size={14} /></button></div></header>
        <div className="graph-canvas">{nodes.length > 0 ? <InteractiveNvlWrapper ref={nvlRef} nodes={nvlNodes} rels={nvlRelationships} layout={layoutMode} layoutOptions={layoutMode === "hierarchical" ? { direction: "right", packing: "bin" } : { enableCytoscape: true }} nvlOptions={{ disableTelemetry: true, renderer: "canvas", minZoom: 0.02, maxZoom: 8, allowDynamicMinZoom: true }} mouseEventCallbacks={{ onNodeClick: (node: { id: string }) => handleNodeClick(node.id), onPan: true, onZoom: true, onZoomAndPan: true, onDrag: true, onDragStart: true, onDragEnd: true }} /> : <div className="empty-state"><Sparkles size={28} /><span>{graphLoading ? "加载中" : hasSelectedIndustry ? (offline ? "该行业未包含在当前离线图谱快照中" : "该行业暂无正式图谱，请到 Agent 工作流生成 graph.json") : "请选择行业后查看图谱"}</span></div>}</div>
      </section>
      <aside className="inspector">
        <div
          className="inspector-resize-handle"
          role="separator"
          aria-label="调整节点审计宽度"
          aria-orientation="vertical"
          aria-valuemin={MIN_INSPECTOR_WIDTH}
          aria-valuemax={MAX_INSPECTOR_WIDTH}
          aria-valuenow={inspectorWidth}
          tabIndex={0}
          title="拖动调整宽度，双击恢复默认宽度"
          onPointerDown={(event) => {
            if (event.button !== 0 || window.innerWidth <= DESKTOP_LAYOUT_BREAKPOINT) return;
            event.preventDefault();
            setInspectorResizing(true);
          }}
          onDoubleClick={() => setInspectorWidth(clampInspectorWidth(DEFAULT_INSPECTOR_WIDTH))}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              setInspectorWidth((currentWidth) => clampInspectorWidth(currentWidth + 20));
            } else if (event.key === "ArrowRight") {
              event.preventDefault();
              setInspectorWidth((currentWidth) => clampInspectorWidth(currentWidth - 20));
            } else if (event.key === "Home") {
              event.preventDefault();
              setInspectorWidth(clampInspectorWidth(MIN_INSPECTOR_WIDTH));
            } else if (event.key === "End") {
              event.preventDefault();
              setInspectorWidth(clampInspectorWidth(MAX_INSPECTOR_WIDTH));
            }
          }}
        />
        <section ref={detailPanelRef} className="panel detail-panel">
          <div className="panel-title"><Network size={16} /><span>节点审计</span></div>
          {selectedNode ? <>
            <h3>{selectedNode.name}</h3>
            <div className="meta-row"><span>L{selectedNode.level}</span><span>{selectedNode.node_type}</span>{selectedNode.is_key_node && <span>关键节点</span>}<span>置信度 {formatPercent(selectedNode.confidence)}</span></div>
            <p>{selectedNode.business_description || selectedNode.description || "暂无描述"}</p>
            <dl className="kv-list"><div><dt>行业</dt><dd>{selectedNode.industry || selectedNode.industry_id}</dd></div><div><dt>层级</dt><dd>L{selectedNode.level}</dd></div><div><dt>更新时间</dt><dd>{selectedNode.updated_at || "-"}</dd></div></dl>
            {selectedNode.tags.length > 0 && <div className="chips compact">{selectedNode.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
            <div className="node-link-list compact"><strong>父节点</strong>{parentNode ? <button type="button" onClick={() => handleNodeClick(parentNode.id)}>{parentNode.name}<small>{parentNode.id}</small></button> : selectedNode.parent_id ? <button type="button" onClick={() => handleNodeClick(selectedNode.parent_id!)}>{nodeLabel(selectedNode.parent_id)}</button> : <span>暂无父节点</span>}</div>
            <div className="node-link-list compact two-col"><strong>子节点</strong>{childNodes.length > 0 ? childNodes.map((node) => <button key={node.id} type="button" onClick={() => handleNodeClick(node.id)}>{node.name}<small>{node.id}</small></button>) : <span>暂无子节点</span>}</div>
            <div className="source-list"><strong>来源 URL</strong>{selectedNode.source_urls.length > 0 ? selectedNode.source_urls.slice(0, 5).map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>) : <span>暂无来源</span>}</div>
            <div className="source-list"><strong>证据 ID</strong>{selectedNode.evidence_ids.length > 0 ? <span>{selectedNode.evidence_ids.join(", ")}</span> : <span>暂无证据</span>}</div>
            {(selectedNode.level === 1 || selectedNode.level === 2) && <div className="l2-flow-attachment">
              <button type="button" className="company-toggle" onClick={handleToggleL2FlowRelations} aria-expanded={selectedL2FlowExpanded}>
                {selectedL2FlowExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                {selectedL2FlowExpanded ? `收起 L${selectedNode.level} 上下游关系` : `展开 L${selectedNode.level} 上下游关系`}
                {selectedL2FlowResponse?.status === "ready" ? `（${selectedL2FlowResponse.total}）` : ""}
              </button>
              {l2FlowLoading && <span className="muted">正在加入图谱...</span>}
              {!l2FlowLoading && selectedL2FlowResponse && selectedL2FlowResponse.status !== "ready" && <span className="muted">{selectedL2FlowResponse.message || "暂无可展示的上下游关系。"}</span>}
              {!l2FlowLoading && selectedL2FlowResponse?.status === "ready" && selectedL2FlowResponse.total === 0 && <span className="muted">该节点没有可靠的上下游关系。</span>}
              {selectedL2FlowResponse?.status === "ready" && selectedL2FlowExpanded && <div className="l2-flow-lists">
                <div className="neighbor-list"><strong>上游关系</strong>{selectedL2UpstreamEdges.map((edge) => <button key={edge.id} type="button" onClick={() => handleNodeClick(edge.source)}><span>{nodeName(edge.source)}</span><small>{edge.description || "上游关系"} · 置信度 {formatPercent(edge.confidence)}</small></button>)}{selectedL2UpstreamEdges.length === 0 && <span>暂无上游关系</span>}</div>
                <div className="neighbor-list"><strong>下游关系</strong>{selectedL2DownstreamEdges.map((edge) => <button key={edge.id} type="button" onClick={() => handleNodeClick(edge.target)}><span>{nodeName(edge.target)}</span><small>{edge.description || "下游关系"} · 置信度 {formatPercent(edge.confidence)}</small></button>)}{selectedL2DownstreamEdges.length === 0 && <span>暂无下游关系</span>}</div>
              </div>}
            </div>}
            <div className="company-attachment">
              <button type="button" className="company-toggle" onClick={handleToggleCompanies} aria-expanded={selectedCompaniesExpanded}>
                {selectedCompaniesExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                {selectedCompaniesExpanded ? "收起公司节点" : "展开公司节点"}
                {selectedCompanyResponse?.status === "ready" ? `（${selectedCompanyResponse.total}）` : ""}
              </button>
              {companyLoading && <span className="muted">正在加入图谱...</span>}
              {!companyLoading && selectedCompanyResponse && selectedCompanyResponse.status !== "ready" && <span className="muted">{selectedCompanyResponse.message || "暂无可展示公司附件。"}</span>}
              {!companyLoading && selectedCompanyResponse?.status === "ready" && selectedCompanyResponse.total === 0 && <span className="muted">该节点没有直接挂载的公司。</span>}
            </div>
            <div className="neighbor-list"><strong>邻接关系</strong>{otherNeighborEdges.slice(0, 10).map((edge) => <button key={edge.id} type="button" onClick={() => handleNodeClick(edge.source === selectedNode.id ? edge.target : edge.source)}><span>{relationSentence(edge)}</span><small>置信度 {formatPercent(edge.confidence)}</small></button>)}{otherNeighborEdges.length === 0 && <span>暂无其他邻接关系</span>}</div>
          </> : selectedCompany ? <>
            <h3>{selectedCompany.name}</h3>
            <div className="meta-row"><span>公司节点</span><span>{selectedCompany.comcode}</span>{selectedCompany.is_listed !== null && selectedCompany.is_listed !== undefined && <span>{selectedCompany.is_listed ? "上市" : "非上市"}</span>}</div>
            <dl className="kv-list">
              <div><dt>公司简称</dt><dd>{selectedCompany.short_name || "-"}</dd></div>
              <div><dt>申万分类</dt><dd>{Object.values(selectedCompany.sw_industry).filter(Boolean).join(" / ") || "-"}</dd></div>
              <div><dt>直接挂载数</dt><dd>{selectedCompany.direct_attachments.length}</dd></div>
            </dl>
            <div className="company-description">
              <strong>公司简介</strong>
              <p>{selectedCompany.direct_attachments.map((attachment) => attachment.reason).filter(Boolean).join("；") || "暂无公司简介"}</p>
            </div>
            <div className="node-link-list company-direct-links">
              <strong>直接挂载节点</strong>
              {selectedCompany.direct_attachments.map((attachment) => <button key={attachment.node_id} type="button" onClick={() => handleNodeClick(attachment.node_id)}><div className="company-node-name">{attachment.node_name}</div><div className="company-attachment-reason">{attachment.reason || "主营业务直接匹配"}</div><small>置信度 {formatPercent(attachment.confidence)}</small></button>)}
            </div>
          </> : <p className="muted">点击图谱节点或已展开的公司节点查看审计信息。</p>}
        </section>
        {!offline && <section className="panel ask-panel"><div className="panel-title"><Bot size={16} /><span>AI 问答</span></div><textarea value={question} placeholder="例如：该行业的上游主要有哪些？" onChange={(event) => setQuestion(event.target.value)} /><button className="action-button" type="button" title="发送问题" onClick={handleAsk} disabled={asking || !question.trim() || !hasSelectedIndustry}>{asking ? <span className="spinner" /> : <Send size={16} />}<span>{asking ? "思考中" : "发送问题"}</span></button>{asking && <div className="thinking" aria-live="polite"><span>正在基于图谱检索上下文</span><i /><i /><i /></div>}{answer && <div className="answer"><strong>回答</strong><p>{answer.answer}</p><strong>引用节点</strong><div className="chips">{answer.context_nodes.slice(0, 12).map((node) => <span key={node.id}>{node.name}</span>)}</div><strong>引用关系</strong><div className="chips">{answer.context_edges.slice(0, 8).map((edge) => <span key={edge.id}>{relationLabel(edge.relation_type)}</span>)}</div></div>}</section>}
      </aside>
    </main>
  );
}












