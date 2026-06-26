export type ChainPosition = "root" | "upstream" | "midstream" | "downstream" | "support";
export type RelationType = "contains" | "upstream_downstream";
export type UpdateMode = "check_only" | "propose" | "apply";
export type CandidateGraphType = "candidate_graph" | "update_candidate_graph";

export interface Industry {
  id: string;
  name: string;
  status: string;
  node_count: number;
  edge_count: number;
}

export interface GraphNode {
  id: string;
  industry_id: string;
  name: string;
  node_type: string;
  tags: string[];
  industry?: string | null;
  level: number;
  chain_position: ChainPosition;
  chain_segment?: string | null;
  parent_id?: string | null;
  description: string;
  business_description?: string | null;
  is_key_node: boolean;
  source_urls: string[];
  evidence_ids: string[];
  confidence: number;
  updated_at?: string | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: RelationType;
  description: string;
  relation_weight: number;
  source_urls: string[];
  evidence_ids: string[];
  confidence: number;
  updated_at?: string | null;
}

export interface GraphResponse {
  industry_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface AskResponse {
  answer: string;
  context_nodes: GraphNode[];
  context_edges: GraphEdge[];
  cypher_summary: string;
}

export interface GraphFilters {
  q: string;
  chain_positions: ChainPosition[];
  relation_types: RelationType[];
  levels: number[];
}

export interface AgentRunResponse {
  run_id: string;
  industry_id: string;
  status: string;
  report_path?: string | null;
  kind?: string | null;
  current_step?: string | null;
  command: string[];
  logs: string[];
  started_at?: string | null;
  ended_at?: string | null;
  returncode?: number | null;
}

export interface AgentArtifact {
  name: string;
  label: string;
  kind: "json" | "jsonl" | "markdown" | "text" | "csv";
  path: string;
  exists: boolean;
  size_bytes: number;
  updated_at?: string | null;
}

export interface AgentArtifactContent {
  industry_id: string;
  name: string;
  label: string;
  kind: string;
  path: string;
  content: unknown;
}

export interface ExportResponse {
  industry_id: string;
  node_csv: string;
  edge_csv: string;
}
