import { useEffect, useMemo, useRef, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import { Bot, Database, Filter, Network, RefreshCw, Search, Send, Sparkles } from "lucide-react";
import {
  askGraph,
  fetchGraph,
  fetchIndustries,
  fetchNeighbors,
  importFoodBeverage
} from "./api";
import type { AskResponse, ChainPosition, GraphEdge, GraphFilters, GraphNode, Industry, RelationType } from "./types";

const chainOptions: Array<{ value: ChainPosition; label: string; color: string }> = [
  { value: "root", label: "根节点", color: "#334155" },
  { value: "upstream", label: "上游", color: "#0f766e" },
  { value: "midstream", label: "中游", color: "#7c3aed" },
  { value: "downstream", label: "下游", color: "#c2410c" },
  { value: "support", label: "支撑", color: "#2563eb" }
];

const relationOptions: Array<{ value: RelationType; label: string }> = [
  { value: "contains", label: "隶属关系" },
  { value: "upstream_downstream", label: "上下游关系" }
];

const levelOptions = [0, 1, 2];
type LayoutMode = "forceDirected" | "hierarchical";

const defaultFilters: GraphFilters = {
  q: "",
  chain_positions: [],
  relation_types: [],
  levels: []
};

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function chainLabel(position: ChainPosition) {
  return chainOptions.find((item) => item.value === position)?.label ?? position;
}

function relationLabel(type: RelationType) {
  return relationOptions.find((item) => item.value === type)?.label ?? type;
}

export function App() {
  const nvlRef = useRef<any>(null);
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [industryId, setIndustryId] = useState("food_beverage");
  const [draftFilters, setDraftFilters] = useState<GraphFilters>(defaultFilters);
  const [appliedFilters, setAppliedFilters] = useState<GraphFilters>(defaultFilters);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [neighborEdges, setNeighborEdges] = useState<GraphEdge[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState("启动后请先导入食品饮料图谱。");
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("forceDirected");

  async function loadIndustries() {
    try {
      const data = await fetchIndustries();
      setIndustries(data);
      if (data.length > 0) setIndustryId(data[0].id);
    } catch {
      setIndustries([]);
    }
  }

  async function loadGraph(nextFilters = appliedFilters) {
    setGraphLoading(true);
    try {
      const data = await fetchGraph(industryId, nextFilters);
      setNodes(data.nodes);
      setEdges(data.edges);
      setMessage(`已加载 ${data.nodes.length} 个节点、${data.edges.length} 条关系。`);
      if (selectedNode && !data.nodes.some((node) => node.id === selectedNode.id)) {
        setSelectedNode(null);
        setNeighborEdges([]);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "图谱加载失败");
    } finally {
      setGraphLoading(false);
    }
  }

  async function handleImport() {
    setImporting(true);
    try {
      const result = await importFoodBeverage();
      setMessage(`导入完成：${result.node_count} 个节点、${result.edge_count} 条关系。`);
      await loadIndustries();
      await loadGraph();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  async function handleNodeClick(nodeId: string) {
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

  useEffect(() => {
    void loadIndustries();
  }, []);

  useEffect(() => {
    setDraftFilters(defaultFilters);
    setAppliedFilters(defaultFilters);
    void loadGraph(defaultFilters);
  }, [industryId]);

  useEffect(() => {
    if (nodes.length === 0) return;
    const timer = window.setTimeout(() => {
      nvlRef.current?.fit?.(nodes.map((node) => node.id));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [nodes]);

  const nvlNodes = useMemo(
    () =>
      nodes.map((node) => ({
        id: node.id,
        caption: node.name,
        size: node.level === 0 ? 48 : node.level === 1 ? 34 : 22,
        color: chainOptions.find((item) => item.value === node.chain_position)?.color ?? "#475569"
      })),
    [nodes]
  );

  const nvlRelationships = useMemo(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        from: edge.source,
        to: edge.target,
        caption: edge.relation_type === "contains" ? "隶属" : "上下游",
        color: edge.relation_type === "contains" ? "#64748b" : "#dc2626",
        width: edge.relation_type === "contains" ? 1 : 2
      })),
    [edges]
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Network size={24} />
          <div>
            <h1>产业链图谱</h1>
            <span>食品饮料 demo</span>
          </div>
        </div>

        <section className="panel">
          <div className="panel-title">
            <Database size={16} />
            <span>数据</span>
          </div>
          <label className="field">
            <span>行业</span>
            <select value={industryId} onChange={(event) => setIndustryId(event.target.value)}>
              {industries.length === 0 && <option value="food_beverage">food_beverage</option>}
              {industries.map((industry) => (
                <option key={industry.id} value={industry.id}>
                  {industry.name}
                </option>
              ))}
            </select>
          </label>
          <button className="action-button" type="button" title="导入食品饮料图谱" onClick={handleImport} disabled={importing}>
            {importing ? <span className="spinner" /> : <Database size={16} />}
            <span>{importing ? "导入中" : "导入食品饮料"}</span>
          </button>
        </section>

        <section className="panel">
          <div className="panel-title">
            <Filter size={16} />
            <span>筛选</span>
          </div>
          <label className="field">
            <span>关键词</span>
            <div className="search-box">
              <Search size={16} />
              <input
                placeholder="节点名称或简介"
                value={draftFilters.q}
                onChange={(event) => setDraftFilters({ ...draftFilters, q: event.target.value })}
                onKeyDown={(event) => {
                  if (event.key === "Enter") applyFilters(draftFilters);
                }}
              />
            </div>
          </label>

          <div className="filter-group">
            <span>上下游分类</span>
            {chainOptions.map((option) => (
              <label key={option.value} className="check-row">
                <input
                  type="checkbox"
                  checked={draftFilters.chain_positions.includes(option.value)}
                  onChange={() =>
                    setDraftFilters({
                      ...draftFilters,
                      chain_positions: toggleValue(draftFilters.chain_positions, option.value)
                    })
                  }
                />
                <span className="dot" style={{ backgroundColor: option.color }} />
                {option.label}
              </label>
            ))}
          </div>

          <div className="filter-group">
            <span>关系类型</span>
            {relationOptions.map((option) => (
              <label key={option.value} className="check-row">
                <input
                  type="checkbox"
                  checked={draftFilters.relation_types.includes(option.value)}
                  onChange={() =>
                    setDraftFilters({
                      ...draftFilters,
                      relation_types: toggleValue(draftFilters.relation_types, option.value)
                    })
                  }
                />
                {option.label}
              </label>
            ))}
          </div>

          <div className="filter-group">
            <span>层级</span>
            <div className="segmented">
              {levelOptions.map((level) => (
                <button
                  key={level}
                  type="button"
                  title={`筛选层级 ${level}`}
                  className={draftFilters.levels.includes(level) ? "active" : ""}
                  onClick={() => setDraftFilters({ ...draftFilters, levels: toggleValue(draftFilters.levels, level) })}
                >
                  L{level}
                </button>
              ))}
            </div>
          </div>

          <div className="toolbar">
            <button type="button" title="应用筛选" onClick={() => applyFilters(draftFilters)} disabled={graphLoading}>
              {graphLoading ? <span className="spinner dark" /> : <Search size={16} />}
            </button>
            <button type="button" title="重置筛选" onClick={() => applyFilters(defaultFilters)} disabled={graphLoading}>
              <RefreshCw size={16} />
            </button>
          </div>
        </section>
      </aside>

      <section className="graph-stage">
        <header className="stage-header">
          <div>
            <h2>Neo4j 图谱可视化</h2>
            <p>{message}</p>
          </div>
          <div className="stats">
            <div className="layout-switch" aria-label="图谱布局">
              <button
                type="button"
                title="力导向布局"
                className={layoutMode === "forceDirected" ? "active" : ""}
                onClick={() => setLayoutMode("forceDirected")}
              >
                力导向
              </button>
              <button
                type="button"
                title="层级布局"
                className={layoutMode === "hierarchical" ? "active" : ""}
                onClick={() => setLayoutMode("hierarchical")}
              >
                层级
              </button>
            </div>
            <span>{nodes.length} 节点</span>
            <span>{edges.length} 关系</span>
            <button
              type="button"
              title="重新居中图谱"
              className="fit-button"
              onClick={() => nvlRef.current?.fit?.(nodes.map((node) => node.id))}
              disabled={nodes.length === 0}
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </header>
        <div className="graph-canvas">
          {nodes.length > 0 ? (
            <InteractiveNvlWrapper
              ref={nvlRef}
              nodes={nvlNodes}
              rels={nvlRelationships}
              layout={layoutMode}
              layoutOptions={
                layoutMode === "hierarchical"
                  ? { direction: "right", packing: "bin" }
                  : { enableCytoscape: true }
              }
              nvlOptions={{
                disableTelemetry: true,
                renderer: "canvas",
                minZoom: 0.02,
                maxZoom: 8,
                allowDynamicMinZoom: true
              }}
              mouseEventCallbacks={{
                onNodeClick: (node: { id: string }) => handleNodeClick(node.id),
                onPan: true,
                onZoom: true,
                onZoomAndPan: true,
                onDrag: true,
                onDragStart: true,
                onDragEnd: true
              }}
            />
          ) : (
            <div className="empty-state">
              <Sparkles size={28} />
              <span>{graphLoading ? "加载中" : "请先导入或加载图谱"}</span>
            </div>
          )}
        </div>
      </section>

      <aside className="inspector">
        <section className="panel detail-panel">
          <div className="panel-title">
            <Network size={16} />
            <span>节点详情</span>
          </div>
          {selectedNode ? (
            <>
              <h3>{selectedNode.name}</h3>
              <div className="meta-row">
                <span>{chainLabel(selectedNode.chain_position)}</span>
                <span>L{selectedNode.level}</span>
                <span>{selectedNode.id}</span>
              </div>
              <p>{selectedNode.description}</p>
              <div className="neighbor-list">
                <strong>邻接关系</strong>
                {neighborEdges.slice(0, 8).map((edge) => (
                  <span key={edge.id}>
                    {edge.source} {edge.relation_type === "contains" ? "包含" : "流向"} {edge.target}
                  </span>
                ))}
                {neighborEdges.length === 0 && <span>暂无邻接关系</span>}
              </div>
            </>
          ) : (
            <p className="muted">点击图谱节点查看简介、层级和上下游邻居。</p>
          )}
        </section>

        <section className="panel ask-panel">
          <div className="panel-title">
            <Bot size={16} />
            <span>AI 问答</span>
          </div>
          <textarea
            value={question}
            placeholder="例如：食品饮料行业的上游主要有哪些？"
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button className="action-button" type="button" title="发送问题" onClick={handleAsk} disabled={asking || !question.trim()}>
            {asking ? <span className="spinner" /> : <Send size={16} />}
            <span>{asking ? "思考中" : "发送问题"}</span>
          </button>
          {asking && (
            <div className="thinking" aria-live="polite">
              <span>正在基于图谱检索上下文</span>
              <i />
              <i />
              <i />
            </div>
          )}
          {answer && (
            <div className="answer">
              <strong>回答</strong>
              <p>{answer.answer}</p>
              <strong>引用节点</strong>
              <div className="chips">
                {answer.context_nodes.slice(0, 12).map((node) => (
                  <span key={node.id}>{node.name}</span>
                ))}
              </div>
              <strong>引用关系</strong>
              <div className="chips">
                {answer.context_edges.slice(0, 8).map((edge) => (
                  <span key={edge.id}>{relationLabel(edge.relation_type)}</span>
                ))}
              </div>
            </div>
          )}
        </section>
      </aside>
    </main>
  );
}
