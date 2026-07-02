import type {
  AgentArtifact,
  AgentArtifactContent,
  AgentRunResponse,
  AskResponse,
  CandidateGraphType,
  ChainPosition,
  ExportResponse,
  GraphFilters,
  GraphResponse,
  Industry,
  RelationType,
  UpdateMode
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

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
  return request<Industry[]>("/api/industries");
}

export async function fetchGraph(industryId: string, filters: GraphFilters) {
  const params = new URLSearchParams({ industry_id: industryId });
  if (filters.q.trim()) params.set("q", filters.q.trim());
  appendList<ChainPosition>(params, "chain_positions", filters.chain_positions);
  appendList<RelationType>(params, "relation_types", filters.relation_types);
  appendList<number>(params, "levels", filters.levels);
  return request<GraphResponse>(`/api/graph?${params.toString()}`);
}

export async function fetchNeighbors(industryId: string, nodeId: string) {
  return request<GraphResponse>(`/api/nodes/${nodeId}/neighbors?industry_id=${industryId}`);
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







