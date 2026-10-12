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
  const externalLink = (text, url, className) => {
    const link = node("a", text, className);
    const valid = safeUrl(url);
    if (valid) { link.href = valid; link.target = "_blank"; link.rel = "noopener noreferrer"; }
    return link;
  };
  const date = (value, detailed = false) => {
    const parsed = new Date(value);
    if (!value || Number.isNaN(parsed.getTime())) return "未记录";
    const options = {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit"};
    if (detailed) Object.assign(options, {hour: "2-digit", minute: "2-digit", hour12: false});
    return new Intl.DateTimeFormat("zh-CN", options).format(parsed);
  };
  const state = {data: null, topic: "", query: "", days: "all", sort: "recent", eventsBound: false, local: null, busy: false};

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
    $("mode-label").textContent = "本机阅读档案";
    $("subscription-description").textContent = "本机阅读器可订阅全部精选或单个主题。订阅时需保持本地服务运行。";
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
    if (analysis.evidence_level === "full_text_excerpt") return "基于全文节选";
    if (analysis.evidence_level === "abstract") return "基于摘要";
    return "分析范围未注明";
  }

  function section(label, text, className = "") {
    const block = node("section", undefined, "technical-section " + className);
    block.append(node("h4", label, "mini-heading"));
    if (Array.isArray(text)) {
      const list = node("ul");
      for (const value of text) list.append(node("li", value));
      block.append(list);
    } else block.append(node("p", text));
    return block;
  }

  function topicName(topic) { return state.data.topic_labels[topic] || topic; }

  function card(paper) {
    const analysis = paper.analysis || {};
    const article = node("article", undefined, "paper-card");
    article.id = "paper-" + paper.id;
    const titleId = "title-" + paper.id;
    article.setAttribute("aria-labelledby", titleId);
    const body = node("div", undefined, "card-inner");
    const top = node("div", undefined, "card-top");
    const meta = node("div", undefined, "paper-meta");
    meta.append(node("span", "arXiv:" + paper.id, "arxiv-id"), node("span", "·", "meta-separator"));
    const announcement = String(paper.date_basis || "").includes("announcement");
    const published = node("time", date(paper.published) + (announcement ? " 公告" : " 提交"));
    if (paper.published) published.dateTime = paper.published;
    meta.append(published);
    top.append(meta, node("span", "相关性 " + (analysis.relevance_score || 0), "score"));
    const title = node("h3", undefined, "paper-title");
    title.id = titleId;
    title.append(externalLink(paper.title || "未命名论文", paper.url));
    const authors = strings(paper.authors);
    const authorText = authors.length > 5 ? authors.slice(0, 5).join(", ") + " 等 " + authors.length + " 位作者" : authors.join(", ");
    const authorLine = node("p", authorText, "authors");
    authorLine.title = authors.join(", ");
    body.append(top, title, authorLine);
    const tags = node("div", undefined, "paper-tags");
    for (const topic of strings(paper.topics)) {
      const tag = node("button", topicName(topic), "paper-tag");
      tag.type = "button";
      tag.setAttribute("aria-label", "筛选主题：" + topicName(topic));
      tag.addEventListener("click", () => { state.topic = topic; render(); });
      tags.append(tag);
    }
    tags.append(node("span", scopeLabel(analysis), "scope-tag " + (analysis.evidence_level === "full_text_excerpt" ? "fulltext" : "")));
    if (state.data.is_demo) tags.append(node("span", "阅读预览", "scope-tag"));
    body.append(tags);
    const summary = analysis.summary || paper.abstract || "本条目尚无技术摘要。";
    body.append(node("p", summary, "summary"));
    const results = strings(analysis.main_results);
    if (results.length) {
      const block = node("div", undefined, "results-block");
      block.append(node("h4", "主要结果", "mini-heading"));
      const list = node("ul");
      for (const result of results.slice(0, 3)) list.append(node("li", result));
      block.append(list); body.append(block);
    }
    const relevance = strings(analysis.relevance);
    if (relevance.length) {
      const block = node("div", undefined, "relevance-block");
      block.append(node("h4", "与你研究的联系", "mini-heading"), node("p", relevance[0]));
      body.append(block);
    }
    article.append(body);
    const details = node("details", undefined, "technical-details");
    details.append(node("summary", "展开技术摘要、假设与后续问题"));
    const content = node("div", undefined, "technical-content");
    if (analysis.problem) content.append(section("研究问题", analysis.problem));
    if (analysis.setting) content.append(section("任务与访问模型", analysis.setting));
    if (results.length > 3) content.append(section("完整主要结果", results));
    for (const [key, label, className] of [
      ["techniques", "方法与技术", ""], ["assumptions", "必要假设", ""],
      ["limitations", "局限与未确认细节", ""], ["relevance", "具体研究联系", ""],
      ["follow_up", "后续问题 · 读者建议", "follow-up"],
    ]) {
      const values = strings(analysis[key]);
      if (values.length) content.append(section(label, values, className));
    }
    if (paper.abstract) content.append(section("原文 abstract", paper.abstract, "abstract"));
    const provenance = node("div", undefined, "provenance");
    provenance.append(node("span", "版本 v" + paper.version));
    if (paper.updated && paper.updated !== paper.published) provenance.append(node("span", "论文更新 " + date(paper.updated)));
    provenance.append(node("span", scopeLabel(analysis) + " · 作者主张的阅读摘要"));
    if (analysis.model) provenance.append(node("span", "分析模型 " + analysis.model));
    if (analysis.analyzed_at) provenance.append(node("span", "分析于 " + date(analysis.analyzed_at, true) + " 北京时间"));
    if (safeUrl(analysis.source_url)) provenance.append(externalLink("分析来源 ↗", analysis.source_url));
    content.append(provenance); details.append(content); article.append(details);
    const footer = node("div", undefined, "card-footer");
    const links = node("div", undefined, "paper-links");
    if (safeUrl(paper.url)) links.append(externalLink("arXiv 原文 ↗", paper.url));
    if (safeUrl(paper.pdf_url)) links.append(externalLink("PDF ↗", paper.pdf_url));
    const permalink = node("a", "条目链接");
    permalink.href = "#paper-" + encodeURIComponent(paper.id);
    links.append(permalink);
    footer.append(links, node("span", strings(paper.categories).join(" / "), "category-text"));
    article.append(footer);
    return article;
  }

  function searchable(paper) {
    const analysis = paper.analysis || {};
    return [paper.title, paper.abstract, ...strings(paper.authors), ...strings(paper.topics).map(topicName), analysis.summary, analysis.problem, analysis.setting,
      ...["main_results", "techniques", "assumptions", "limitations", "relevance", "follow_up"].flatMap((key) => strings(analysis[key]))].join(" ").toLocaleLowerCase();
  }

  function updateTopics() {
    const container = $("topic-filters");
    container.replaceChildren();
    const topics = Object.keys(state.data.topic_feeds);
    $("topic-count").textContent = topics.length + " 个主题";
    for (const topic of ["", ...topics]) {
      const count = topic ? state.data.papers.filter((paper) => strings(paper.topics).includes(topic)).length : state.data.papers.length;
      const button = node("button", undefined, "topic-button" + (topic === state.topic ? " active" : ""));
      button.type = "button";
      button.setAttribute("aria-pressed", String(topic === state.topic));
      button.append(node("span", topic ? topicName(topic) : "全部主题"), node("span", count, "topic-number"));
      button.addEventListener("click", () => { state.topic = topic; render(); });
      container.append(button);
    }
    const path = state.topic ? state.data.topic_feeds[state.topic] : "feed.xml";
    if (state.data.is_demo) {
      $("current-feed").removeAttribute("href");
      $("current-feed").setAttribute("aria-disabled", "true");
      $("current-feed").textContent = (state.topic ? topicName(state.topic) : "全部精选") + " RSS · 本地服务中启用";
    } else {
      $("current-feed").href = "./" + path;
      $("current-feed").textContent = (state.topic ? topicName(state.topic) : "全部精选") + " RSS ↗";
    }
    $("copy-status").textContent = "";
  }

  function render() {
    updateTopics();
    let papers = state.data.papers.filter((paper) => {
      if (state.topic && !strings(paper.topics).includes(state.topic)) return false;
      if (state.query && !searchable(paper).includes(state.query.toLocaleLowerCase())) return false;
      if (state.days !== "all") {
        const timestamp = Date.parse(paper.published);
        const elapsed = Date.now() - timestamp;
        if (!Number.isFinite(timestamp) || elapsed > Number(state.days) * 86400000 || elapsed < -86400000) return false;
      }
      return true;
    });
    papers.sort((left, right) => {
      if (state.sort === "relevance") {
        const difference = (right.analysis.relevance_score || 0) - (left.analysis.relevance_score || 0);
        if (difference) return difference;
      }
      return (Date.parse(right.published) || 0) - (Date.parse(left.published) || 0) || String(left.id).localeCompare(String(right.id));
    });
    $("results-count").textContent = papers.length;
    const basis = state.data.is_demo && state.data.papers.some((p) => String(p.date_basis || "").includes("announcement")) ? "演示条目使用 arXiv 公告日期" : "日期筛选使用 arXiv 首次提交时间";
    $("result-caption").textContent = "显示 " + papers.length + " / " + state.data.papers.length + " 篇" + (state.topic ? " · " + topicName(state.topic) : "") + " · " + basis;
    $("paper-list").replaceChildren(...papers.map(card));
    $("empty-state").hidden = papers.length > 0;
    $("reset-filters").hidden = state.data.papers.length === 0;
    if (state.data.papers.length === 0) {
      $("empty-message").textContent = "尚无完成 AI 分析的精选论文。完成候选抓取与 AI 分析后，研究精选将出现在这里；待分析候选不会作为技术摘要发布。";
    } else $("empty-message").textContent = "尝试其他关键词、主题或时间范围。";
  }

  function init(data) {
    if (!data || !Array.isArray(data.papers)) throw new Error("invalid data");
    state.data = data;
    data.topic_labels = data.topic_labels || {};
    data.topic_feeds = data.topic_feeds || {};
    $("profile-title").textContent = data.profile_title || "量子信息理论";
    $("total-count").textContent = String(data.papers.length).padStart(2, "0");
    $("mode-label").textContent = data.is_demo ? "阅读预览" : state.local ? "本机阅读档案" : "研究阅读档案";
    $("demo-notice").hidden = !data.is_demo;
    $("update-status").textContent = data.is_demo ? "真实论文阅读预览 · 非每日自动更新" : "候选抓取：" + (data.last_collection_run ? date(data.last_collection_run, true) + " 北京时间" : "尚未完成") + "\nAI 分析：" + (data.last_analysis_run ? date(data.last_analysis_run, true) + " 北京时间" : "尚未完成");
    const run = data.last_run || {};
    $("run-summary").textContent = data.is_demo ? "预览摘要依据真实论文来源整理；启动本地服务后可更新档案并订阅本机 RSS。" : Number.isFinite(run.fetched) ? "最近一轮：抓取 " + run.fetched + " · 关键词候选 " + (run.keyword_candidates || 0) + " · 本轮 AI 分析 " + (run.analyzed || 0) + " · 新增精选 " + (run.selected || 0) + " · 待 AI 分析 " + (run.pending || 0) : "候选抓取与 AI 分析的完成时间分别记录。";
    if (data.is_demo) {
      for (const link of document.querySelectorAll("a[href$='feed.xml']")) {
        link.removeAttribute("href"); link.setAttribute("aria-disabled", "true");
        if (link.classList.contains("subscribe-button")) link.replaceChildren(node("span", "本地服务中可订阅 RSS"));
        else link.textContent = "RSS · 本地服务中启用";
      }
      const navLink = document.querySelector("nav a[href='#subscriptions']");
      if (navLink) { navLink.href = "#demo-notice"; navLink.textContent = "阅读预览"; }
      $("copy-feed").disabled = true;
      $("copy-feed").textContent = "本地服务中可订阅";
    }
    render();
    if (state.eventsBound) return;
    state.eventsBound = true;
    $("search").addEventListener("input", (event) => { state.query = event.target.value.trim(); render(); });
    $("date-filter").addEventListener("change", (event) => { state.days = event.target.value; render(); });
    $("sort").addEventListener("change", (event) => { state.sort = event.target.value; render(); });
    $("filter-form").addEventListener("submit", (event) => event.preventDefault());
    $("reset-filters").addEventListener("click", () => {
      state.topic = ""; state.query = ""; state.days = "all"; state.sort = "recent";
      $("search").value = ""; $("date-filter").value = "all"; $("sort").value = "recent"; render();
    });
    $("copy-feed").addEventListener("click", async () => {
      const path = state.topic ? state.data.topic_feeds[state.topic] : "feed.xml";
      const url = new URL(path, state.local ? location.origin + "/" : state.data.site_url).href;
      try {
        await navigator.clipboard.writeText(url);
        $("copy-status").textContent = "订阅链接已复制";
      } catch (_) { $("copy-status").textContent = "请复制此链接：" + url; }
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
    $("mode-label").textContent = "读取失败";
    $("update-status").textContent = "无法读取阅读档案";
    $("result-caption").textContent = "读取失败；已有 RSS 仍可直接打开。";
    $("empty-state").hidden = false;
    $("empty-message").textContent = location.protocol === "file:" ? "请用项目提供的本地 HTTP server 打开网站，或打开 snapshot.html 便携预览。" : "请稍后刷新，或从 RSS 阅读器查看已发布内容。";
    $("reset-filters").hidden = true;
  });
})();
