/**
 * Generates a self-contained, double-clickable offline viewer.
 * The output deliberately contains only formal graph snapshots and validated
 * relation/company attachments, never API keys or intermediate Agent artefacts.
 */
import { execFileSync } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontend = path.join(root, "frontend");
const outputDirectory = path.join(root, "deliverables");
const temporaryDirectory = path.join(outputDirectory, ".offline-viewer-build");
const outputFile = path.join(outputDirectory, "产业链图谱离线展示.html");
const frontendRequire = createRequire(pathToFileURL(path.join(frontend, "package.json")));
const { build } = frontendRequire("esbuild");
function graphFingerprint(graphFile) {
  const script = [
    "import hashlib,json,sys",
    "from pathlib import Path",
    "graph=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))",
    "stable={'schema_version':graph.get('schema_version'),'nodes':graph.get('nodes',[]),'edges':graph.get('edges',[])}",
    "text=json.dumps(stable,ensure_ascii=False,sort_keys=True,separators=(',',':'))",
    "print(hashlib.sha256(text.encode('utf-8')).hexdigest())"
  ].join(";");
  return execFileSync(process.env.PYTHON ?? "python", ["-c", script, graphFile], { encoding: "utf8" }).trim();
}

const manifest = JSON.parse(await readFile(path.join(root, "data", "industries", "manifest.json"), "utf8"));
const industries = [];
const graphs = {};
const companyAttachments = {};
const l2FlowRelations = {};

for (const item of manifest) {
  const graphFile = path.join(root, item.data_path);
  try {
    const graph = JSON.parse(await readFile(graphFile, "utf8"));
    if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.nodes.length === 0) continue;
    industries.push({
      id: item.id,
      name: item.name,
      status: item.status,
      node_count: graph.nodes.length,
      edge_count: graph.edges.length
    });
    graphs[item.id] = {
      industry_id: item.id,
      nodes: graph.nodes.map((node) => ({ ...node, industry_id: node.industry_id ?? item.id })),
      edges: graph.edges.map((edge) => ({ ...edge, relation_layer: edge.relation_layer ?? "main" }))
    };
    const currentGraphFingerprint = graphFingerprint(graphFile);
    const currentNodeIds = new Set(graph.nodes.map((node) => node.id));
    const currentL2NodeIds = new Set(graph.nodes.filter((node) => node.level === 2).map((node) => node.id));
    try {
      const payload = JSON.parse(await readFile(path.join(root, "data", "industries", item.id, "company_attachments.json"), "utf8"));
      if (
        payload.industry_id === item.id
        && payload.graph_fingerprint === currentGraphFingerprint
        && Array.isArray(payload.companies)
        && Array.isArray(payload.attachments)
      ) {
        const companyIds = new Set(payload.companies.map((company) => company.company_id));
        companyAttachments[item.id] = {
          schema_version: payload.schema_version,
          graph_fingerprint: payload.graph_fingerprint,
          companies: payload.companies,
          attachments: payload.attachments.filter((attachment) =>
            companyIds.has(attachment.company_id) && currentNodeIds.has(attachment.node_id)
          )
        };
      } else {
        console.warn(`已跳过 ${item.id} 的公司附件：格式无效或与当前 graph.json 不匹配。`);
      }
    } catch {
      // Company attachment is optional: industries without it remain viewable.
    }
    try {
      const payload = JSON.parse(await readFile(path.join(root, "data", "industries", item.id, "l2_flow_relations.json"), "utf8"));
      if (
        payload.industry_id === item.id
        && payload.schema_version === "industry_l2_flow_relations_v0.2_pairwise"
        && payload.graph_fingerprint === currentGraphFingerprint
        && Array.isArray(payload.edges)
      ) {
        l2FlowRelations[item.id] = {
          schema_version: payload.schema_version,
          graph_fingerprint: payload.graph_fingerprint,
          edges: payload.edges.filter((edge) =>
            edge.relation_type === "upstream_downstream"
            && currentL2NodeIds.has(edge.source)
            && currentL2NodeIds.has(edge.target)
          ).map((edge) => ({ ...edge, relation_layer: "l2_flow" }))
        };
      } else {
        console.warn(
          `已跳过 ${item.id} 的 L2 关系附件：schema=${payload.schema_version ?? "missing"}，`
          + `附件指纹=${payload.graph_fingerprint ?? "missing"}，当前图谱指纹=${currentGraphFingerprint}。`
        );
      }
    } catch {
      // L2 flow relations are optional: the main classification graph remains viewable.
    }
  } catch (error) {
    if (item.status !== "pending") throw new Error(`无法读取 ${item.id} 的正式图谱：${error.message}`);
  }
}

if (industries.length === 0) throw new Error("没有找到可导出的正式图谱。");

const snapshot = {
  schema_version: "industry_graph_offline_snapshot_v0.2",
  generated_at: new Date().toISOString(),
  industries,
  graphs,
  company_attachments: companyAttachments,
  l2_flow_relations: l2FlowRelations
};
await rm(temporaryDirectory, { recursive: true, force: true });
await mkdir(temporaryDirectory, { recursive: true });

const jsFile = path.join(temporaryDirectory, "viewer.js");
await build({
  entryPoints: [path.join(frontend, "src", "offline-main.tsx")],
  outfile: jsFile,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: true,
  sourcemap: false,
  logLevel: "info",
  define: { "import.meta.env.VITE_API_BASE": '""' },
  banner: { js: `window.__INDUSTRY_GRAPH_OFFLINE_SNAPSHOT__=${JSON.stringify(snapshot)};` }
});

const [script, style] = await Promise.all([
  readFile(jsFile, "utf8"),
  readFile(path.join(temporaryDirectory, "viewer.css"), "utf8")
]);
const safeScript = script.replace(/<\/script/gi, "<\\/script");
const safeStyle = style.replace(/<\/style/gi, "<\\/style");
const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="产业链图谱离线只读展示版" />
  <title>产业链图谱离线展示</title>
  <style>${safeStyle}</style>
</head>
<body>
  <div id="root"></div>
  <script>${safeScript}</script>
</body>
</html>`;
await writeFile(outputFile, html, "utf8");
await rm(temporaryDirectory, { recursive: true, force: true });
console.log(`已生成离线展示包：${outputFile}`);
console.log(`已内置 ${industries.length} 个行业、${industries.reduce((total, item) => total + item.node_count, 0)} 个节点。`);
console.log(`已内置 ${Object.keys(companyAttachments).length} 个行业的公司挂载结果。`);
console.log(`已内置 ${Object.keys(l2FlowRelations).length} 个行业的 L2 上下游关系结果。`);
