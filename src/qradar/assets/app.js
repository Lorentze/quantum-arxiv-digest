"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  };
  const strings = (value) => Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
  const safeUrl = (value) => {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
    } catch (_) { return ""; }
  };
  const externalLink = (text, url) => {
    const link = node("a", text);
    const valid = safeUrl(url);
    if (valid) { link.href = valid; link.target = "_blank"; link.rel = "noopener noreferrer"; }
    return link;
  };
  const articlePath = (value) => /^articles\/(?:[0-9]{4}\.[0-9]{4,5}|paper-[a-f0-9]{16})\.html$/.test(value || "") ? "./" + value : "";
  const date = (value, detailed = false) => {
    const parsed = new Date(value);
    if (!value || Number.isNaN(parsed.getTime())) return "";
    const options = {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit"};
    if (detailed) Object.assign(options, {hour: "2-digit", minute: "2-digit", hour12: false});
    return new Intl.DateTimeFormat("zh-CN", options).format(parsed);
  };
  const state = {data: null, topic: "", source: "", query: "", eventsBound: false, localEventsBound: false, local: null, busy: false};
  const sourceName = (source) => state.data.source_labels[source] || source;
  const paperSources = (paper) => strings(paper.sources).length ? strings(paper.sources) : String(paper.id).startsWith("doi:") ? [] : ["arxiv"];
  const arxivId = (paper) => paper.arxiv_id || (String(paper.id).startsWith("doi:") ? "" : paper.id);
  const publicationDate = (paper) => paper.publication && paper.publication.published || paper.published;
  const feedPath = () => state.source ? state.data.source_feeds[state.source] || "feed.xml" : state.topic ? state.data.topic_feeds[state.topic] || "feed.xml" : "feed.xml";

  async function localRequest(path, payload) {
    const options = {cache: "no-store", credentials: "same-origin"};
    if (payload !== undefined) {
      options.method = "POST";
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(path, options); }
    catch (_) { throw new Error("无法连接本地服务，请确认启动窗口仍在运行。"); }
    let value;
    try { value = await response.json(); }
    catch (_) { throw new Error("本地服务返回了无法读取的响应，请确认服务仍在运行。"); }
    if (!value || typeof value !== "object") throw new Error("本地服务返回了无法读取的响应。");
    if (!response.ok || value.ok === false) throw new Error(value.error || "本地操作未完成，请稍后重试。");
    return value;
  }

  function localMessage(text, error = false) {
    $("local-status").textContent = text;
    $("local-status").classList.toggle("error", error);
  }

  function localControls() {
    for (const id of ["collect-papers", "import-analysis", "analysis-file"]) $(id).disabled = state.busy;
    $("export-request").disabled = state.busy || !state.local || state.local.pending === 0;
    $("local-workbench").setAttribute("aria-busy", String(state.busy));
  }

  async function refreshLocalStatus() {
    const status = await localRequest("/api/status");
    if (!status || status.local !== true || !Number.isInteger(status.pending) || status.pending < 0) throw new Error("无法确认本地档案状态。");
    state.local = status;
    $("pending-count").textContent = status.pending;
    localControls();
    return status;
  }

  async function reloadArchive() {
    const response = await fetch("./papers.json", {cache: "no-store"});
    if (!response.ok) throw new Error("结果已保存，但阅读档案读取失败，请刷新页面。");
    init(await response.json());
    return refreshLocalStatus();
  }

  async function localAction(message, action) {
    if (state.busy) return;
    state.busy = true;
    localControls();
    localMessage(message);
    try { await action(); }
    catch (error) { localMessage(error instanceof TypeError ? "无法完成本地操作，请确认服务仍在运行，然后重试。" : String(error.message || error).slice(0, 650), true); }
    finally { state.busy = false; localControls(); }
  }

  async function setupLocal() {
    if (location.protocol !== "http:" || location.hostname !== "127.0.0.1") return;
    try { await refreshLocalStatus(); }
    catch (_) { return; } // Static previews have no local operations API.
    $("local-workbench").hidden = false;
    if (state.localEventsBound) return;
    state.localEventsBound = true;
    $("local-feed-link").href = "/feed.xml";
    $("local-feed-link").textContent = location.origin + "/feed.xml";
    localMessage(state.local.pending ? "已有 " + state.local.pending + " 篇候选待分析，可以先导出材料。" : "先收集候选，再导出材料交给 ChatGPT 分析。");
    $("collect-papers").addEventListener("click", () => localAction("正在从 arXiv 收集候选… 完成后会更新待分析数量。", async () => {
      const result = await localRequest("/api/collect", {});
      const status = await reloadArchive();
      const fetched = result.last_run && result.last_run.fetched;
      localMessage((Number.isInteger(fetched) ? "已抓取 " + fetched + " 篇论文；" : "候选收集完成；") + "当前 " + status.pending + " 篇待分析。收集不会自动生成技术摘要。");
    }));
    $("export-request").addEventListener("click", () => localAction("正在整理待分析材料…", async () => {
      const response = await fetch("/api/analysis-request", {cache: "no-store", credentials: "same-origin"});
      if (!response.ok) {
        let failure = {};
        try { failure = await response.json(); } catch (_) { /* Use the message below. */ }
        throw new Error(failure.error || "待分析材料导出失败。");
      }
      const content = await response.text();
      const blobUrl = URL.createObjectURL(new Blob([content], {type: "text/plain;charset=utf-8"}));
      const link = node("a");
      link.href = blobUrl;
      link.download = "qradar-analysis-request.txt";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      localMessage("材料已导出。将文件交给 ChatGPT，完成分析后下载返回的 JSON，再在这里导入。");
    }));
    $("import-analysis").addEventListener("click", () => $("analysis-file").click());
    $("analysis-file").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      localAction("正在检查并导入分析结果…", async () => {
        if (file.size > 8 * 1024 * 1024) throw new Error("分析文件超过 8 MB，请分成较小批次后导入。");
        let batch;
        try { batch = JSON.parse(await file.text()); }
        catch (_) { throw new Error("文件不是有效的 JSON。请导入 ChatGPT 返回的完整 JSON 文件。"); }
        if (!batch || !Array.isArray(batch.analyses) || !batch.analyses.length) throw new Error("JSON 需要包含非空的 analyses 数组。请让 ChatGPT 按导出材料中的格式返回结果。");
        const result = await localRequest("/api/import-analysis", batch);
        const status = await reloadArchive();
        const imported = result.last_analysis_batch || {};
        localMessage("已导入 " + (imported.analyzed || 0) + " 篇分析，新增 " + (imported.selected || 0) + " 篇精选；当前 " + status.pending + " 篇待分析。网页和 RSS 已更新。");
      }).finally(() => { $("analysis-file").value = ""; });
    });
  }

  function scopeLabel(analysis) {
    return analysis.evidence_level === "full_text_excerpt" ? "依据论文正文节选" : analysis.evidence_level === "abstract" ? "依据论文摘要" : "依据已标注来源";
  }

  function section(label, text) {
    const block = node("section", undefined, "report-section");
    block.append(node("h2", label));
    if (Array.isArray(text)) {
      const list = node("ul");
      for (const value of text) list.append(node("li", value));
      block.append(list);
    } else {
      for (const paragraph of String(text).split(/\n\s*\n/)) {
        if (paragraph.trim()) block.append(node("p", paragraph));
      }
    }
    return block;
  }

  function topicName(topic) { return state.data.topic_labels[topic] || topic; }

  function snapshotReport(paper) {
    const analysis = paper.analysis || {};
    const details = node("details", undefined, "snapshot-report");
    details.id = "analysis-" + paper.id;
    details.append(node("summary", "AI 中文技术阅读报告"));
    const content = node("div", undefined, "report");
    content.append(node("p", scopeLabel(analysis), "paper-meta"));
    for (const [key, label] of [
      ["summary", "这篇论文做了什么"], ["problem", "研究问题与动机"],
      ["setting", "模型、任务与评价指标"], ["main_results", "主要贡献与结果"],
      ["techniques", "核心技术路线"], ["assumptions", "结论成立的条件"],
      ["limitations", "局限与证据边界"], ["relevance", "相关研究与意义"],
      ["publication_significance", "为何适合该期刊（分析判断）"],
      ["follow_up", "进一步阅读与开放问题"],
    ]) {
      const value = Array.isArray(analysis[key]) ? strings(analysis[key]) : analysis[key];
      if (value && (!Array.isArray(value) || value.length)) {
        const block = section(label, value);
        if (key === "publication_significance") {
          block.insertBefore(node("p", "根据论文贡献与期刊定位的分析，不代表编辑或审稿人的实际决定。", "assessment-note"), block.children[1]);
          const review = paper.publication_review;
          if (review) {
            const provenance = node("p", "本节" + scopeLabel(review), "assessment-provenance");
            if (safeUrl(review.source_url)) provenance.append(" · ", externalLink("发表版本 ↗", review.source_url));
            if (review.analyzed_at) provenance.append(" · 分析时间 " + date(review.analyzed_at, true));
            block.append(provenance);
          }
        }
        content.append(block);
      }
    }
    details.append(content);
    return details;
  }

  function card(paper) {
    const article = node("article", undefined, "paper-card");
    article.id = "paper-" + paper.id;
    const title = node("h2", undefined, "paper-title");
    title.id = "title-" + paper.id;
    title.lang = "en";
    article.setAttribute("aria-labelledby", title.id);
    const report = window.QRADAR_SNAPSHOT ? snapshotReport(paper) : null;
    const reportLink = (text) => {
      const link = node("a", text);
      if (report) {
        link.href = "#" + encodeURIComponent(report.id);
        link.addEventListener("click", () => { report.open = true; });
      } else {
        const path = articlePath(paper.article_path);
        if (path) link.href = path;
      }
      return link;
    };
    title.append(reportLink(paper.title || "Untitled paper"));
    const sourceBadges = node("div", undefined, "source-badges");
    sourceBadges.lang = "en";
    for (const source of paperSources(paper)) sourceBadges.append(node("span", sourceName(source), "source-badge"));
    const dates = [];
    if (!String(paper.id).startsWith("doi:")) dates.push((String(paper.date_basis || "").includes("announcement") ? "arXiv announcement" : "First submitted") + " " + date(paper.published));
    if (paper.publication && paper.publication.published) dates.push("Journal publication " + date(paper.publication.published));
    else if (String(paper.id).startsWith("doi:")) dates.push("Journal publication " + date(paper.published));
    const metadata = node("p", dates.join(" · "), "paper-meta");
    metadata.lang = "en";
    const authors = strings(paper.authors);
    const authorText = authors.length > 4 ? authors.slice(0, 4).join(", ") + ", et al." : authors.join(", ");
    const authorLine = node("p", authorText, "authors");
    authorLine.lang = "en";
    authorLine.title = authors.join(", ");
    const abstract = node("p", paper.abstract || "The original abstract is not available in this entry.", "abstract");
    abstract.lang = "en";
    const abstractLabel = paper.abstract && paper.abstract_basis === "publisher_feed_excerpt" ? node("p", "Publisher feed excerpt (may be truncated; not the full original abstract).", "abstract-evidence") : null;
    if (abstractLabel) abstractLabel.lang = "en";
    const links = node("div", undefined, "paper-links");
    const id = arxivId(paper);
    const preprintUrl = String(paper.id).startsWith("doi:") ? paper.arxiv_url || id && "https://arxiv.org/abs/" + id : paper.url;
    if (id && safeUrl(preprintUrl)) links.append(externalLink("arXiv ↗", preprintUrl));
    const publication = paper.publication || {};
    const publisherUrl = publication.url || (String(paper.id).startsWith("doi:") ? paper.url : "");
    if (safeUrl(publisherUrl)) links.append(externalLink((publication.journal || "Publisher") + " ↗", publisherUrl));
    links.append(reportLink("阅读 AI 中文报告 →"));
    article.append(sourceBadges, metadata, title, authorLine);
    if (abstractLabel) article.append(abstractLabel);
    article.append(abstract, links);
    if (report) article.append(report);
    return article;
  }

  function searchable(paper) {
    const analysis = paper.analysis || {};
    return [paper.title, paper.abstract, ...strings(paper.authors), ...strings(paper.topics).map(topicName), ...paperSources(paper).map(sourceName), analysis.summary, analysis.problem, analysis.setting,
      ...["main_results", "techniques", "assumptions", "limitations", "relevance", "follow_up", "publication_significance"].flatMap((key) => strings(analysis[key]))].join(" ").toLocaleLowerCase();
  }

  function updateTopics() {
    const selector = $("topic-filter");
    selector.replaceChildren();
    for (const topic of ["", ...Object.keys(state.data.topic_feeds)]) {
      const option = node("option", topic ? topicName(topic) : "全部主题");
      option.value = topic;
      selector.append(option);
    }
    selector.value = state.topic;
    const sourceSelector = $("source-filter");
    sourceSelector.replaceChildren();
    for (const source of ["", ...Object.keys(state.data.source_labels)]) {
      const option = node("option", source ? sourceName(source) : "全部来源");
      option.value = source;
      sourceSelector.append(option);
    }
    sourceSelector.value = state.source;
    $("current-feed").href = "./" + feedPath();
    $("current-feed").textContent = state.source ? "来源 RSS" : state.topic ? "主题 RSS" : "RSS";
  }

  function render() {
    updateTopics();
    const papers = state.data.papers.filter((paper) => (!state.topic || strings(paper.topics).includes(state.topic)) && (!state.source || paperSources(paper).includes(state.source)) && (!state.query || searchable(paper).includes(state.query.toLocaleLowerCase())));
    papers.sort((left, right) => (Date.parse(publicationDate(right)) || 0) - (Date.parse(publicationDate(left)) || 0) || String(left.id).localeCompare(String(right.id)));
    $("result-caption").textContent = papers.length + " articles" + (state.source ? " · " + sourceName(state.source) : "") + (state.topic ? " · " + topicName(state.topic) : "");
    $("paper-list").replaceChildren(...papers.map(card));
    $("empty-state").hidden = papers.length > 0;
    $("reset-filters").hidden = state.data.papers.length === 0;
    $("empty-message").textContent = state.data.papers.length ? "尝试其他关键词、主题或来源。" : "完成分析的文章将在这里发布。";
  }

  function init(data) {
    if (!data || !Array.isArray(data.papers)) throw new Error("invalid data");
    state.data = data;
    data.topic_labels = data.topic_labels || {};
    data.topic_feeds = data.topic_feeds || {};
    data.source_labels = data.source_labels || {arxiv: "arXiv"};
    data.source_feeds = data.source_feeds || {};
    $("demo-notice").hidden = !data.is_demo;
    $("updated-at").textContent = data.last_analysis_run ? "更新于 " + date(data.last_analysis_run, true) + " 北京时间" : "";
    render();
    if (state.eventsBound) return;
    state.eventsBound = true;
    $("search").addEventListener("input", (event) => { state.query = event.target.value.trim(); render(); });
    $("topic-filter").addEventListener("change", (event) => { state.topic = event.target.value; render(); });
    $("source-filter").addEventListener("change", (event) => { state.source = event.target.value; render(); });
    $("filter-form").addEventListener("submit", (event) => event.preventDefault());
    $("reset-filters").addEventListener("click", () => {
      state.topic = ""; state.source = ""; state.query = ""; $("search").value = ""; render();
    });
    $("copy-feed").addEventListener("click", async () => {
      const path = feedPath();
      const url = new URL(path, state.local ? location.origin + "/" : data.site_url).href;
      try {
        await navigator.clipboard.writeText(url);
        $("copy-status").textContent = "RSS 链接已复制";
      } catch (_) { $("copy-status").textContent = url; }
    });
    if (location.hash.startsWith("#paper-")) {
      try {
        const selected = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        if (selected) selected.scrollIntoView({block: "start"});
      } catch (_) { /* Ignore malformed fragments. */ }
    }
    setupLocal();
  }

  Promise.resolve(window.QRADAR_DATA || fetch("./papers.json", {cache: "no-cache"}).then((response) => {
    if (!response.ok) throw new Error("data unavailable");
    return response.json();
  })).then(init).catch(() => {
    $("result-caption").textContent = "读取失败，请稍后刷新。";
    $("empty-state").hidden = false;
    $("empty-message").textContent = location.protocol === "file:" ? "请通过本地服务打开，或使用 snapshot.html 便携预览。" : "RSS 仍可打开已发布的中文报告。";
    $("reset-filters").hidden = true;
  });
})();

