const state = {
  status: null,
  currentRows: [],
  currentName: "",
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setLog(text) {
  $("log").textContent = text || "";
}

function renderFileList(node, files) {
  node.innerHTML = "";
  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "file-row";
    empty.textContent = "暂无文件";
    node.appendChild(empty);
    return;
  }
  for (const file of files) {
    const row = document.createElement("button");
    row.className = "file-row";
    row.type = "button";
    row.innerHTML = `<span>${escapeHtml(file.name)}</span><small>${escapeHtml(file.count)} 篇</small><small>${escapeHtml(file.size_kb)} KB</small>`;
    row.addEventListener("click", () => previewFile(file.path));
    node.appendChild(row);
  }
}

async function loadStatus() {
  const response = await fetch("/api/status");
  state.status = await response.json();
  $("rootPath").textContent = state.status.root;
  $("rawTotal").textContent = state.status.raw_total;
  $("groupTotal").textContent = state.status.grouped_total;
  $("kbTotal").textContent = state.status.kb_metadata_count;
  $("pdfTotal").textContent = state.status.pdf_count;
  renderFileList($("rawList"), state.status.raw_files);
  renderFileList($("groupedList"), state.status.grouped_files);
}

async function previewFile(path) {
  const response = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
  const data = await response.json();
  state.currentRows = Array.isArray(data) ? data : [];
  state.currentName = path;
  $("tableSearch").value = "";
  renderPaperTable();
}

function cellText(value) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(", ");
  }
  if (value === null || value === undefined || value === "") {
    return "null";
  }
  return String(value);
}

function matchesQuery(row, query) {
  if (!query) {
    return true;
  }
  const haystack = [
    row.id,
    row.publish_venue,
    row.title,
    row.publish_year,
    row.research_area,
    cellText(row.key_words),
    row.Author,
    row.abstract,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function renderPaperTable() {
  const query = $("tableSearch").value.trim();
  const rows = state.currentRows.filter((row) => matchesQuery(row, query));
  const visibleRows = rows.slice(0, 300);
  $("tableSummary").textContent = state.currentName
    ? `Total records: ${state.currentRows.length}. Showing: ${visibleRows.length}. Source: ${state.currentName}`
    : "选择一个 JSON 文件查看表格。";

  const body = $("paperTableBody");
  body.innerHTML = "";
  if (!visibleRows.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="9" class="empty-cell">没有匹配的数据。</td>`;
    body.appendChild(row);
    return;
  }

  visibleRows.forEach((paper, index) => {
    const row = document.createElement("tr");
    const pdf = cellText(paper.pdf_url);
    const safePdf = escapeHtml(pdf);
    row.innerHTML = `
      <td>${index + 1}</td>
      <td>${escapeHtml(cellText(paper.id))}</td>
      <td>${escapeHtml(cellText(paper.publish_venue))}</td>
      <td class="title-cell">${escapeHtml(cellText(paper.title))}</td>
      <td>${escapeHtml(cellText(paper.publish_year))}</td>
      <td>${escapeHtml(cellText(paper.research_area))}</td>
      <td class="keywords-cell">${escapeHtml(cellText(paper.key_words))}</td>
      <td class="author-cell">${escapeHtml(cellText(paper.Author))}</td>
      <td class="url-cell">${pdf.startsWith("http") ? `<a href="${safePdf}" target="_blank" rel="noreferrer">PDF</a>` : safePdf}</td>
    `;
    body.appendChild(row);
  });
}

async function runAction(url, payload, label) {
  setLog(`${label}运行中，请稍等...`);
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const result = await response.json();
  setLog(
    [
      `命令：${result.command || ""}`,
      `状态：${result.ok ? "成功" : "失败"} ${result.returncode ?? ""}`,
      result.result_path ? `结果文件：${result.result_path}` : "",
      "",
      result.stdout || "",
      result.stderr || result.error || "",
    ].join("\n")
  );
  if (result.result) {
    renderInnovationResult(result.result);
  }
  await loadStatus();
}

function parseList(value) {
  return String(value || "")
    .split(/[\n,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseConstraints() {
  const raw = $("innovationConstraints").value.trim();
  if (!raw) {
    return {};
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`约束 JSON 格式错误：${error.message}`);
  }
}

function renderInnovationResult(data) {
  const node = $("innovationResult");
  const proposals = data.innovations || data.refined_proposals || [];
  if (!proposals.length) {
    node.innerHTML = `<div class="empty-result">暂无创新点结果。</div>`;
    return;
  }

  const trendText = (data.research_trends || [])
    .slice(0, 5)
    .map((item) => item.name)
    .filter(Boolean)
    .join(" / ");
  const cards = proposals
    .map((item) => {
      const scores = item.scores || {};
      const evidence = (item.evidence || [])
        .slice(0, 2)
        .map((ref) => `${ref.year || ""} ${ref.title || ref.id || ""}`.trim())
        .join("；");
      return `
        <article class="proposal-card">
          <div class="proposal-head">
            <span class="rank">#${escapeHtml(item.rank || "")}</span>
            <h3>${escapeHtml(item.title)}</h3>
          </div>
          <p>${escapeHtml(item.summary || item.description || "")}</p>
          <div class="score-row">
            <span>总分 ${escapeHtml(item.overall_score ?? "")}</span>
            <span>新颖 ${escapeHtml(scores.novelty ?? "")}</span>
            <span>可行 ${escapeHtml(scores.feasibility ?? "")}</span>
            <span>影响 ${escapeHtml(scores.impact ?? "")}</span>
            <span>风险 ${escapeHtml(scores.risk ?? "")}</span>
          </div>
          <dl>
            <dt>研究问题</dt><dd>${escapeHtml(item.research_question || "")}</dd>
            <dt>验证方案</dt><dd>${escapeHtml(item.validation_plan || "")}</dd>
            <dt>wengao topic</dt><dd>${escapeHtml(item.downstream_wengao_inputs?.topic || item.title || "")}</dd>
            <dt>证据</dt><dd>${escapeHtml(evidence || "已生成 evidence_refs，可在 JSON 中查看完整证据。")}</dd>
          </dl>
        </article>
      `;
    })
    .join("");

  node.innerHTML = `
    <div class="result-summary">
      <strong>${escapeHtml(data.research_domain || "")}</strong>
      <span>${escapeHtml(data.metadata?.document_count ?? 0)} 篇文献</span>
      <span>${escapeHtml(data.metadata?.proposal_count ?? proposals.length)} 个精炼创新点</span>
      <span>${escapeHtml(trendText || "暂无趋势摘要")}</span>
    </div>
    <div class="proposal-list">${cards}</div>
  `;
}

async function runInnovationAgent() {
  try {
    const payload = {
      research_domain: $("innovationDomain").value.trim(),
      keywords: parseList($("innovationKeywords").value),
      seed_ideas: parseList($("innovationSeeds").value),
      mode: $("innovationMode").value,
      top_k: Number($("innovationTopK").value || 5),
      constraints: parseConstraints(),
      additional_context: $("innovationContext").value.trim(),
    };
    if (!payload.research_domain) {
      throw new Error("请先填写研究领域。");
    }
    await runAction("/api/run/innovation-agent", payload, "生成创新点");
  } catch (error) {
    setLog(String(error.message || error));
  }
}

$("refreshBtn").addEventListener("click", loadStatus);
$("dailyTaskBtn").addEventListener("click", () => runAction("/api/run/daily", {}, "运行每日任务"));
$("crawlBtn").addEventListener("click", () => runAction("/api/run/crawl", {}, "爬取 Papers with Code"));
$("splitBtn").addEventListener("click", () => runAction("/api/run/split", {}, "按发布 venue/年份划分"));
$("organizeBtn").addEventListener("click", () => runAction("/api/run/organize", {}, "生成 knowledge_base"));
$("downloadBtn").addEventListener("click", () => runAction("/api/run/download", {}, "下载 PDF"));
$("innovationBtn").addEventListener("click", runInnovationAgent);
$("importPostgresBtn").addEventListener("click", () =>
  runAction(
    "/api/run/import-postgres",
    {
      host: $("pgHost").value.trim(),
      port: $("pgPort").value.trim(),
      database: $("pgDatabase").value.trim(),
      user: $("pgUser").value.trim(),
      password: $("pgPassword").value,
      table: "papers",
      es_url: $("esUrl").value.trim(),
      es_index: $("esIndex").value.trim(),
      es_user: $("esUser").value.trim(),
      es_password: $("esPassword").value,
    },
    "入库并同步 ES"
  )
);
$("refreshMetadataBtn").addEventListener("click", () =>
  runAction(
    "/api/run/update-metadata",
    {
      host: $("pgHost").value.trim(),
      port: $("pgPort").value.trim(),
      database: $("pgDatabase").value.trim(),
      user: $("pgUser").value.trim(),
      password: $("pgPassword").value,
      table: "papers",
    },
    "刷新元数据时间戳"
  )
);
$("tableSearch").addEventListener("input", renderPaperTable);

loadStatus().catch((error) => setLog(String(error)));
