export type ChainPosition = "root" | "upstream" | "midstream" | "downstream" | "support";
export type RelationType = "contains" | "upstream_downstream";

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
  level: number;
  chain_position: ChainPosition;
  parent_id?: string | null;
  description: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: RelationType;
  description: string;
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

