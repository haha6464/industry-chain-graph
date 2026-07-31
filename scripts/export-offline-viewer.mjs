/**
 * Generates delivery-ready, self-contained offline graph viewers.
 *
 * --industry-id <id> creates:
 *   deliverables/<行业名>图谱/<行业名>图谱.html + the industry's CSV files
 * --all (the default) additionally creates:
 *   deliverables/25行业产业链图谱.html
 * and a delivery directory for every industry that has a formal graph.
 */
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontend = path.join(root, "frontend");
const args = process.argv.slice(2);

function optionValue(name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

const industryId = optionValue("--industry-id");
const exportAll = args.includes("--all") || !industryId;
if (industryId && args.includes("--all")) throw new Error("--industry-id 与 --all 不能同时使用。");
if (args.some((arg) => arg.startsWith("--") && !["--all", "--industry-id"].includes(arg)) || (args.includes("--industry-id") && !industryId)) {
  throw new Error("用法：node scripts/export-offline-viewer.mjs [--industry-id <id> | --all]");
}

const outputDirectory = path.join(root, "deliverables");
const temporaryDirectory = path.join(outputDirectory, ".offline-viewer-build");
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

function safeFileName(value) {
  return String(value).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "未命名行业";
}

function deliveryDirectoryName(industry) {
  return `${safeFileName(industry.name)}图谱`;
}

function deliveryHtmlName(industry) {
  return `${safeFileName(industry.name)}图谱.html`;
}

function snapshotFor(entries, graphs, companyAttachments, l2FlowRelations) {
  return {
    schema_version: "industry_graph_offline_snapshot_v0.2",
    generated_at: new Date().toISOString(),
    industries: entries.map(({ item, graph }) => ({
      id: item.id,
      name: item.name,
      status: item.status,
      node_count: graph.nodes.length,
      edge_count: graph.edges.length
    })),
    graphs: Object.fromEntries(entries.map(({ item }) => [item.id, graphs[item.id]])),
    company_attachments: Object.fromEntries(entries.filter(({ item }) => companyAttachments[item.id]).map(({ item }) => [item.id, companyAttachments[item.id]])),
    l2_flow_relations: Object.fromEntries(entries.filter(({ item }) => l2FlowRelations[item.id]).map(({ item }) => [item.id, l2FlowRelations[item.id]]))
  };
}

function renderHtml(snapshot, title, script, style) {
  const safeSnapshot = JSON.stringify(snapshot).replace(/<\/script/gi, "<\\/script");
  const safeScript = script.replace(/<\/script/gi, "<\\/script");
  const safeStyle = style.replace(/<\/style/gi, "<\\/style");
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="description" content="产业链图谱离线只读展示版" />
  <title>${title}</title>
  <style>${safeStyle}</style>
</head>
<body>
  <div id="root"></div>
  <script>window.__INDUSTRY_GRAPH_OFFLINE_SNAPSHOT__=${safeSnapshot};</script>
  <script>${safeScript}</script>
</body>
</html>`;
}

function refreshIndustryCsv(industryId) {
  const script = "from tools.agent.export_csv import export_industry_csv; import sys; export_industry_csv(sys.argv[1])";
  try {
    execFileSync(process.env.PYTHON ?? "python", ["-c", script, industryId], {
      cwd: root,
      encoding: "utf8",
      stdio: "pipe"
    });
  } catch (error) {
    const detail = error.stderr?.toString().trim() || error.stdout?.toString().trim() || error.message;
    throw new Error(`刷新 ${industryId} 的 CSV 失败：${detail}`);
  }
}

const manifest = JSON.parse(await readFile(path.join(root, "data", "industries", "manifest.json"), "utf8"));
const requestedItems = industryId
  ? manifest.filter((item) => item.id === industryId && item.status !== "pending")
  : manifest.filter((item) => item.status !== "pending");
if (industryId && requestedItems.length === 0) throw new Error(`行业不存在，或尚未登记为正式图谱：${industryId}`);

const entries = [];
const graphs = {};
const companyAttachments = {};
const l2FlowRelations = {};

for (const item of requestedItems) {
  const graphFile = path.join(root, item.data_path);
  try {
    const graph = JSON.parse(await readFile(graphFile, "utf8"));
    if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.nodes.length === 0) continue;
    entries.push({ item, graph });
    graphs[item.id] = {
      industry_id: item.id,
      nodes: graph.nodes.map((node) => ({ ...node, industry_id: node.industry_id ?? item.id })),
      edges: graph.edges.map((edge) => ({ ...edge, relation_layer: edge.relation_layer ?? "main" }))
    };
    const currentGraphFingerprint = graphFingerprint(graphFile);
    const currentNodeIds = new Set(graph.nodes.map((node) => node.id));
    const currentL1NodeIds = new Set(graph.nodes.filter((node) => node.level === 1).map((node) => node.id));
    const currentL2NodeIds = new Set(graph.nodes.filter((node) => node.level === 2).map((node) => node.id));
    try {
      const payload = JSON.parse(await readFile(path.join(root, "data", "industries", item.id, "company_attachments.json"), "utf8"));
      if (payload.industry_id === item.id && payload.graph_fingerprint === currentGraphFingerprint && Array.isArray(payload.companies) && Array.isArray(payload.attachments)) {
        const companyIds = new Set(payload.companies.map((company) => company.company_id));
        companyAttachments[item.id] = {
          schema_version: payload.schema_version,
          graph_fingerprint: payload.graph_fingerprint,
          companies: payload.companies,
          attachments: payload.attachments.filter((attachment) => companyIds.has(attachment.company_id) && currentNodeIds.has(attachment.node_id))
        };
      } else {
        console.warn(`已跳过 ${item.id} 的公司附件：格式无效或与当前 graph.json 不匹配。`);
      }
    } catch {
      // Company attachment is optional: the main graph remains exportable.
    }
    try {
      const payload = JSON.parse(await readFile(path.join(root, "data", "industries", item.id, "l2_flow_relations.json"), "utf8"));
      if (payload.industry_id === item.id && payload.schema_version === "industry_l2_flow_relations_v0.2_pairwise" && payload.graph_fingerprint === currentGraphFingerprint && Array.isArray(payload.edges) && Array.isArray(payload.projected_edges)) {
        l2FlowRelations[item.id] = {
          schema_version: payload.schema_version,
          graph_fingerprint: payload.graph_fingerprint,
          edges: payload.edges.filter((edge) => edge.relation_type === "upstream_downstream" && currentL2NodeIds.has(edge.source) && currentL2NodeIds.has(edge.target)).map((edge) => ({ ...edge, relation_layer: "l2_flow" })),
          projected_edges: payload.projected_edges.filter((edge) => edge.relation_type === "upstream_downstream" && ((currentL1NodeIds.has(edge.source) && currentL2NodeIds.has(edge.target)) || (currentL2NodeIds.has(edge.source) && currentL1NodeIds.has(edge.target)))).map((edge) => ({ ...edge, relation_layer: "l1_l2_flow_projection" }))
        };
      } else {
        console.warn(`已跳过 ${item.id} 的 L2 关系附件：格式无效或与当前 graph.json 不匹配。`);
      }
    } catch {
      // L2 flow relations are optional: the main graph remains exportable.
    }
  } catch (error) {
    if (industryId || item.status !== "pending") throw new Error(`无法读取 ${item.id} 的正式图谱：${error.message}`);
  }
}

const deliveryEntries = entries.filter((entry) => Boolean(companyAttachments[entry.item.id]));
if (industryId && deliveryEntries.length === 0) {
  throw new Error(`行业 ${industryId} 尚未满足交付条件：需要与当前 graph.json 指纹一致的公司挂载附件。`);
}
if (deliveryEntries.length === 0) throw new Error("没有找到同时具备正式图谱和有效公司挂载附件的行业。");

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
  define: { "import.meta.env.VITE_API_BASE": '""' }
});
const [script, style] = await Promise.all([
  readFile(jsFile, "utf8"),
  readFile(path.join(temporaryDirectory, "viewer.css"), "utf8")
]);

async function copyCsvDeliverables(entry) {
  const targetDirectory = path.join(outputDirectory, deliveryDirectoryName(entry.item));
  await rm(targetDirectory, { recursive: true, force: true });
  await mkdir(targetDirectory, { recursive: true });
  const snapshot = snapshotFor([entry], graphs, companyAttachments, l2FlowRelations);
  const htmlPath = path.join(targetDirectory, deliveryHtmlName(entry.item));
  await writeFile(htmlPath, renderHtml(snapshot, `${entry.item.name}图谱`, script, style), "utf8");

  const sourceDirectory = path.join(root, "data", "industries", entry.item.id, "exports");
  const csvSuffixes = [
    "industry_node.csv",
    "industrynode_edge.csv",
    "industrynode_industry_edge.csv",
    "industrynode_node.csv",
    ...(companyAttachments[entry.item.id] ? ["company_node.csv", "company_industrynode_edge_node.csv"] : [])
  ];
  const availableCsvNames = await readdir(sourceDirectory);
  const csvNames = csvSuffixes.map((suffix) => availableCsvNames.find((filename) => filename.endsWith(`_${suffix}`))).filter(Boolean);
  if (csvNames.length !== csvSuffixes.length) {
    const missing = csvSuffixes.filter((suffix) => !csvNames.some((filename) => filename.endsWith(`_${suffix}`)));
    throw new Error(`缺少 ${entry.item.id} 的交付 CSV：${missing.join("、")}`);
  }
  for (const filename of csvNames) {
    const source = path.join(sourceDirectory, filename);
    try {
      await copyFile(source, path.join(targetDirectory, filename));
    } catch (error) {
      throw new Error(`复制 ${entry.item.id} 的交付 CSV 失败（${filename}）：${error.message}`);
    }
  }
  console.log(`已生成行业交付目录：${targetDirectory}（HTML + ${csvNames.length} 个 CSV）`);
}

await mkdir(outputDirectory, { recursive: true });
if (exportAll) {
  const eligibleIds = new Set(deliveryEntries.map((entry) => entry.item.id));
  for (const item of manifest) {
    if (!eligibleIds.has(item.id)) {
      await rm(path.join(outputDirectory, deliveryDirectoryName(item)), { recursive: true, force: true });
    }
  }
  for (const obsoleteFile of [
    "产业链图谱离线展示.html",
    "产业链图谱-参考申万分类.html",
    "产业链图谱-未参考申万分类.html",
    "offline-viewer-check.png"
  ]) {
    await rm(path.join(outputDirectory, obsoleteFile), { force: true });
  }
}
for (const entry of deliveryEntries) refreshIndustryCsv(entry.item.id);
for (const entry of deliveryEntries) await copyCsvDeliverables(entry);
if (exportAll) {
  const rootHtml = path.join(outputDirectory, "25行业产业链图谱.html");
  await writeFile(rootHtml, renderHtml(snapshotFor(deliveryEntries, graphs, companyAttachments, l2FlowRelations), "25行业产业链图谱", script, style), "utf8");
  console.log(`已生成全部行业离线图谱：${rootHtml}`);
}
await rm(temporaryDirectory, { recursive: true, force: true });
console.log(`已导出 ${deliveryEntries.length} 个具备公司挂载的行业，共 ${deliveryEntries.reduce((total, entry) => total + entry.graph.nodes.length, 0)} 个产业链节点。`);
