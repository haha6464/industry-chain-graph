import type {
  AgentArtifact,
  AgentArtifactContent,
  AgentRunResponse,
  AskResponse,
  CandidateGraphType,
  ChainPosition,
  CompanyAttachmentItem,
  ExportResponse,
  GraphFilters,
  GraphResponse,
  Industry,
  NodeCompaniesResponse,
  RelationType,
  UpdateMode
} from "./types";

// `import.meta.env` exists in Vite's normal development build.  The optional
// access also lets the self-contained offline IIFE run directly from file://.
const API_BASE = import.meta.env?.VITE_API_BASE ?? "";

type OfflineSnapshot = {
  generated_at: string;
  industries: Industry[];
  graphs: Record<string, GraphResponse>;
  company_attachments?: Record<string, OfflineCompanyAttachments>;
};

type OfflineCompanyAttachments = {
  companies: Array<Omit<CompanyAttachmentItem, "direct_node_ids" | "direct_node_names" | "direct_attachments">>;
  attachments: Array<{ company_id: string; node_id: string; reason?: string; confidence?: number }>;
};

declare global {
  interface Window {
    __INDUSTRY_GRAPH_OFFLINE_SNAPSHOT__?: OfflineSnapshot;
  }
}

function offlineSnapshot() {
  return typeof window === "undefined" ? undefined : window.__INDUSTRY_GRAPH_OFFLINE_SNAPSHOT__;
}

function filterOfflineGraph(graph: GraphResponse, filters: GraphFilters): GraphResponse {
  const keyword = filters.q.trim().toLowerCase();
  const matchedNodes = graph.nodes.filter((node) => {
    const text = [node.name, node.description, node.business_description, node.node_type, ...(node.tags ?? [])]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return (!keyword || text.includes(keyword))
      && (filters.chain_positions.length === 0 || filters.chain_positions.includes(node.chain_position))
      && (filters.levels.length === 0 || filters.levels.includes(node.level));
  });
  const nodeIds = new Set(matchedNodes.map((node) => node.id));
  return {
    industry_id: graph.industry_id,
    nodes: matchedNodes,
    edges: graph.edges.filter((edge) =>
      nodeIds.has(edge.source)
      && nodeIds.has(edge.target)
      && (filters.relation_types.length === 0 || filters.relation_types.includes(edge.relation_type))
    )
  };
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {})
    },
    ...options
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

function appendList<T extends string | number>(params: URLSearchParams, key: string, values: T[]) {
  values.forEach((value) => params.append(key, String(value)));
}


export async function fetchIndustries() {
  const snapshot = offlineSnapshot();
  if (snapshot) return snapshot.industries;
  return request<Industry[]>("/api/industries");
}

export async function fetchGraph(industryId: string, filters: GraphFilters) {
  const snapshot = offlineSnapshot();
  if (snapshot) {
    const graph = snapshot.graphs[industryId];
    if (!graph) throw new Error("离线展示包中未包含该行业图谱。");
    return filterOfflineGraph(graph, filters);
  }
  const params = new URLSearchParams({ industry_id: industryId });
  if (filters.q.trim()) params.set("q", filters.q.trim());
  appendList<ChainPosition>(params, "chain_positions", filters.chain_positions);
  appendList<RelationType>(params, "relation_types", filters.relation_types);
  appendList<number>(params, "levels", filters.levels);
  return request<GraphResponse>(`/api/graph?${params.toString()}`);
}

export async function fetchNeighbors(industryId: string, nodeId: string) {
  const snapshot = offlineSnapshot();
  if (snapshot) {
    const graph = snapshot.graphs[industryId];
    if (!graph) throw new Error("离线展示包中未包含该行业图谱。");
    const nodes = graph.nodes.filter((node) => node.id === nodeId || graph.edges.some((edge) =>
      (edge.source === nodeId && edge.target === node.id) || (edge.target === nodeId && edge.source === node.id)
    ));
    return { industry_id: industryId, nodes, edges: graph.edges.filter((edge) => edge.source === nodeId || edge.target === nodeId) };
  }
  return request<GraphResponse>(`/api/nodes/${nodeId}/neighbors?industry_id=${industryId}`);
}

export async function fetchNodeCompanies(industryId: string, nodeId: string, limit = 500, offset = 0, includeDescendants = true): Promise<NodeCompaniesResponse> {
  const snapshot = offlineSnapshot();
  if (snapshot) {
    const graph = snapshot.graphs[industryId];
    if (!graph || !graph.nodes.some((node) => node.id === nodeId)) throw new Error(`Node not found: ${nodeId}`);
    const payload = snapshot.company_attachments?.[industryId];
    if (!payload) {
      return { industry_id: industryId, node_id: nodeId, status: "missing", message: "离线展示包未包含公司挂载结果。", total: 0, limit, offset, items: [] };
    }
    const visibleNodeIds = new Set([nodeId]);
    if (includeDescendants) {
      let changed = true;
      while (changed) {
        changed = false;
        graph.nodes.forEach((node) => {
          if (node.parent_id && visibleNodeIds.has(node.parent_id) && !visibleNodeIds.has(node.id)) {
            visibleNodeIds.add(node.id);
            changed = true;
          }
        });
      }
    }
    const nodeNames = new Map(graph.nodes.map((node) => [node.id, node.name]));
    const companies = new Map(payload.companies.map((company) => [company.company_id, company]));
    const matched = new Map<string, Array<{ node_id: string; node_name: string; reason: string; confidence: number }>>();
    payload.attachments.forEach((attachment) => {
      if (!visibleNodeIds.has(attachment.node_id) || !companies.has(attachment.company_id)) return;
      const entries = matched.get(attachment.company_id) ?? [];
      entries.push({
        node_id: attachment.node_id,
        node_name: nodeNames.get(attachment.node_id) ?? attachment.node_id,
        reason: attachment.reason ?? "",
        confidence: attachment.confidence ?? 0
      });
      matched.set(attachment.company_id, entries);
    });
    const items: CompanyAttachmentItem[] = Array.from(matched.entries()).map(([companyId, directAttachments]) => ({
      ...companies.get(companyId)!,
      direct_attachments: directAttachments,
      direct_node_ids: directAttachments.map((attachment) => attachment.node_id),
      direct_node_names: directAttachments.map((attachment) => attachment.node_name)
    })).sort((left, right) => left.name.localeCompare(right.name, "zh-CN") || left.comcode.localeCompare(right.comcode));
    return { industry_id: industryId, node_id: nodeId, status: "ready", message: "", total: items.length, limit, offset, items: items.slice(offset, offset + limit) };
  }
  const params = new URLSearchParams({ industry_id: industryId, limit: String(limit), offset: String(offset), include_descendants: String(includeDescendants) });
  return request<NodeCompaniesResponse>(`/api/nodes/${nodeId}/companies?${params.toString()}`);
}

export async function askGraph(industryId: string, question: string, filters: GraphFilters) {
  return request<AskResponse>("/api/ask", {
    method: "POST",
    body: JSON.stringify({
      industry_id: industryId,
      question,
      filters: {
        q: filters.q || null,
        chain_positions: filters.chain_positions,
        relation_types: filters.relation_types,
        levels: filters.levels
      }
    })
  });
}

export async function createSearchPlan(industryId: string, industryName: string) {
  return request<AgentRunResponse>("/api/agent/search-plan", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId, industry_name: industryName })
  });
}

export async function finalValidateAgentGraph(industryId: string) {
  return request<AgentRunResponse>("/api/agent/final-validate", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId, mode: "check_only" })
  });
}

export async function buildAgentSkeleton(industryId: string, industryName: string, targetDepth = "5-6 层，60-100 个节点，最多 150 个节点") {
  return request<AgentRunResponse>("/api/agent/build-skeleton", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId, industry_name: industryName, target_depth: targetDepth })
  });
}

export async function buildAgentBranches(industryId: string, industryName: string, targetDepth = "5-6 层，60-100 个节点，最多 150 个节点") {
  return request<AgentRunResponse>("/api/agent/build-branches", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId, industry_name: industryName, target_depth: targetDepth })
  });
}

export async function updateAgentGraph(industryId: string, mode: UpdateMode = "check_only") {
  return request<AgentRunResponse>("/api/agent/update", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId, mode })
  });
}

export async function attachCompanies(industryId: string) {
  return request<AgentRunResponse>("/api/agent/attach-companies", {
    method: "POST",
    body: JSON.stringify({ industry_id: industryId })
  });
}

export async function fetchAgentRun(runId: string) {
  return request<AgentRunResponse>(`/api/agent/runs/${runId}`);
}

export async function cancelAgentRun(runId: string) {
  return request<AgentRunResponse>(`/api/agent/runs/${runId}/cancel`, { method: "POST" });
}

export async function fetchAgentReport(runId: string) {
  return request<{ run_id: string; report_path: string; content: string }>(`/api/agent/runs/${runId}/report`);
}

export async function fetchAgentArtifacts(industryId: string) {
  return request<{ industry_id: string; artifacts: AgentArtifact[] }>(`/api/industries/${industryId}/agent-artifacts`);
}

export async function fetchAgentArtifact(industryId: string, artifactName: string) {
  return request<AgentArtifactContent>(`/api/industries/${industryId}/agent-artifacts/${artifactName}`);
}

export async function deleteAgentArtifact(industryId: string, artifactName: string) {
  return request<{ industry_id: string; name: string; label: string; path: string; deleted: boolean }>(`/api/industries/${industryId}/agent-artifacts/${artifactName}`, {
    method: "DELETE"
  });
}


export async function applyCandidateGraph(industryId: string, candidateType: CandidateGraphType) {
  return request<AgentRunResponse>(`/api/industries/${industryId}/apply-candidate`, {
    method: "POST",
    body: JSON.stringify({ candidate_type: candidateType })
  });
}

export async function exportIndustryCsv(industryId: string) {
  return request<ExportResponse>(`/api/industries/${industryId}/export-csv`, { method: "POST" });
}

export async function fetchIndustryExports(industryId: string) {
  return request<{ industry_id: string; exports: string[] }>(`/api/industries/${industryId}/exports`);
}







