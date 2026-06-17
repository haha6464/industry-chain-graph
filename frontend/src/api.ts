import type { AskResponse, ChainPosition, GraphFilters, GraphResponse, Industry, RelationType } from "./types";

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

export async function importFoodBeverage() {
  return request<{ industry_id: string; node_count: number; edge_count: number }>("/api/import/food-beverage", {
    method: "POST"
  });
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

