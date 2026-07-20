/**
 * Generates a self-contained, double-clickable offline viewer.
 * The output deliberately contains only formal graph snapshots, never API keys,
 * Agent artefacts, or server-side company data.
 */
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

const manifest = JSON.parse(await readFile(path.join(root, "data", "industries", "manifest.json"), "utf8"));
const industries = [];
const graphs = {};
const companyAttachments = {};

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
      edges: graph.edges
    };
    try {
      const payload = JSON.parse(await readFile(path.join(root, "data", "industries", item.id, "company_attachments.json"), "utf8"));
      if (payload.industry_id === item.id && Array.isArray(payload.companies) && Array.isArray(payload.attachments)) {
        companyAttachments[item.id] = { companies: payload.companies, attachments: payload.attachments };
      }
    } catch {
      // Company attachment is optional: industries without it remain viewable.
    }
  } catch (error) {
    if (item.status !== "pending") throw new Error(`无法读取 ${item.id} 的正式图谱：${error.message}`);
  }
}

if (industries.length === 0) throw new Error("没有找到可导出的正式图谱。");

const snapshot = { generated_at: new Date().toISOString(), industries, graphs, company_attachments: companyAttachments };
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
