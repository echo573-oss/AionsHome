/* 《河》前端：章节阅读、写作、版本与 Markdown 手稿导入。 */
(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const base = "/api/river";
  let chapters = [];
  let current = null;
  let editingId = null;
  let editVersion = null;
  let editorDirty = false;
  let chapterRequest = 0;
  let toastTimer = 0;
  let searchTimer = 0;
  let finderTimer = 0;

  function toast(message) {
    const node = byId("riverToast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), 3300);
  }

  async function request(method, path, payload) {
    const options = {method, credentials: "same-origin", headers: {"Accept": "application/json"}};
    if (payload !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(base + path, options);
    let data;
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) {
      const detail = data.detail;
      let message = typeof detail === "string" ? detail : "操作失败（HTTP " + response.status + "）";
      if (Array.isArray(detail)) message = detail.map(item => item.msg || "字段不正确").join("；");
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function dateText(seconds) {
    if (!seconds) return "";
    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString("zh-CN", {year:"numeric", month:"2-digit", day:"2-digit"});
  }

  function cleanList(text, splitLinesOnly) {
    const values = splitLinesOnly ? text.split(/\r?\n/) : text.split(/[\s,，;；]+/);
    return [...new Set(values.map(s => s.trim()).filter(Boolean))];
  }

  function element(tag, className, content) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (content !== undefined && content !== null) el.textContent = content;
    return el;
  }

  function sidebarEntry(chapter, i) {
    const button = element("button", "river-chapter" + (current && current.id === chapter.id ? " active" : ""));
    button.type = "button";
    button.dataset.id = chapter.id;
    button.setAttribute("aria-current", current && current.id === chapter.id ? "true" : "false");
    button.append(element("span", "river-chapter-num", String(i + 1).padStart(2, "0")));
    const text = element("span");
    text.append(element("span", "river-chapter-name", chapter.title));
    if (chapter.period_label || chapter.status === "archived") {
      text.append(element("small", "river-chapter-period",
        (chapter.period_label || "") + (chapter.status === "archived" ? " · 已归档" : "")));
    }
    button.append(text);
    button.addEventListener("click", () => openChapter(chapter.id));
    return button;
  }

  function drawSidebar() {
    const list = byId("chapterList");
    list.replaceChildren();
    chapters.forEach((chapter, i) => list.append(sidebarEntry(chapter, i)));
    byId("chapterCount").textContent = chapters.length ? "当前显示 " + chapters.length + " 篇" : "还没有章节";
  }

  async function refreshChapters(preferId) {
    const search = byId("chapterSearch").value.trim();
    const archived = byId("showArchived").checked;
    const query = "?include_archived=" + archived + "&q=" + encodeURIComponent(search);
    let offset = 0;
    const all = [];
    let total = 0;
    do {
      const response = await request("GET", "/chapters" + query + "&limit=300&offset=" + offset);
      all.push(...response.items);
      total = response.total;
      offset = all.length;
    } while (offset < total && offset < 3000);
    chapters = all;
    if (offset < total) toast("目录较长，当前展示前 3000 篇");
    drawSidebar();
    const id = preferId || (current && current.id) ||
      new URLSearchParams(location.search).get("chapter") || (chapters[0] && chapters[0].id);
    const present = chapters.some(c => c.id === id);
    if (present) await openChapter(id);
    else if (chapters[0]) await openChapter(chapters[0].id);
    else showEmpty();
  }

  function showEmpty() {
    current = null;
    byId("readerArticle").hidden = true;
    byId("readerEmpty").hidden = false;
    drawSidebar();
  }

  function drawLinkedMemories(items) {
    const host = byId("linkedMemories");
    host.replaceChildren();
    if (!items.length) {
      host.append(element("p", "", "还没有关联原子记忆。"));
      return;
    }
    items.forEach(memory => {
      const entry = element("div", "river-memory-line");
      entry.append(element("small", "", memory.id + (memory.archive_state === "cold" ? " · 已归档" : "")));
      entry.append(element("div", "", memory.missing ? "原记忆已不存在或还未导入" : memory.content));
      host.append(entry);
    });
  }

  function drawChapter(chapter) {
    current = chapter;
    byId("readerEmpty").hidden = true;
    byId("readerArticle").hidden = false;
    const index = chapters.findIndex(item => item.id === chapter.id);
    byId("readerKicker").textContent =
      (index >= 0 ? "第 " + String(index + 1).padStart(2, "0") + " 篇" : "《河》") +
      (chapter.period_label ? " · " + chapter.period_label : "");
    byId("readerTitle").textContent = chapter.title;
    byId("readerSubtitle").textContent = chapter.subtitle || " ";
    byId("readerBody").textContent = chapter.content;
    const hasReflection = Boolean(chapter.reflection.trim());
    byId("readerReflection").hidden = !hasReflection;
    byId("reflectionText").textContent = chapter.reflection;
    byId("archiveButton").textContent = chapter.status === "archived" ? "取消归档" : "归档";
    byId("readerFooter").textContent = "版本 " + chapter.version +
      "  ·  " + dateText(chapter.updated_at) + " 更新" +
      (chapter.source_key ? "  ·  " + chapter.source_key : "");
    const sourceRefs = byId("sourceRefs");
    sourceRefs.replaceChildren();
    (chapter.source_refs || []).forEach(item => sourceRefs.append(element("div", "river-source-line", item)));
    if (!(chapter.source_refs || []).length) sourceRefs.append(element("p", "", "暂未记录原文线索。"));
    drawLinkedMemories(chapter.linked_memories || []);
    byId("sourceCount").textContent =
      "(" + ((chapter.source_refs || []).length + (chapter.memory_ids || []).length) + ")";
    drawSidebar();
  }

  async function openChapter(id) {
    const ticket = ++chapterRequest;
    try {
      const item = await request("GET", "/chapters/" + encodeURIComponent(id));
      if (ticket !== chapterRequest) return;
      drawChapter(item);
      byId("riverReader").scrollTop = 0;
      if (location.pathname === "/river") {
        const url = new URL(location.href);
        url.searchParams.set("chapter", item.id);
        history.replaceState(null, "", url.pathname + url.search);
      }
    } catch (error) {
      if (ticket === chapterRequest) toast(error.message);
    }
  }

  function formValues() {
    return {
      title: byId("editTitle").value.trim(),
      source_key: byId("editSourceKey").value.trim(),
      subtitle: byId("editSubtitle").value.trim(),
      period_label: byId("editPeriod").value.trim(),
      content: byId("editContent").value.trim(),
      reflection: byId("editReflection").value.trim(),
      source_refs: cleanList(byId("editSources").value, true),
      memory_ids: cleanList(byId("editMemoryIds").value, false),
      status: byId("editStatus").value,
      ...(editingId ? {expected_version: editVersion} : {})
    };
  }

  function showEditor(chapter) {
    editingId = chapter ? chapter.id : null;
    editVersion = chapter ? chapter.version : null;
    byId("editorHeading").textContent = chapter ? "编辑 · " + chapter.title : "写一篇《河》";
    byId("editTitle").value = chapter ? chapter.title : "";
    byId("editSourceKey").value = chapter ? chapter.source_key : "";
    byId("editPeriod").value = chapter ? chapter.period_label : "";
    byId("editSubtitle").value = chapter ? chapter.subtitle : "";
    byId("editContent").value = chapter ? chapter.content : "";
    byId("editReflection").value = chapter ? chapter.reflection : "";
    byId("editSources").value = chapter ? (chapter.source_refs || []).join("\n") : "";
    byId("editMemoryIds").value = chapter ? (chapter.memory_ids || []).join("\n") : "";
    byId("editStatus").value = chapter ? chapter.status : "draft";
    byId("memorySearch").value = "";
    byId("memorySearchResults").replaceChildren();
    byId("editorMessage").textContent = "";
    editorDirty = false;
    byId("editorOverlay").hidden = false;
    byId("editTitle").focus();
  }

  function closeEditor(force) {
    if (!force && editorDirty && !confirm("这次修改还没有保存，确定离开吗？")) return;
    byId("editorOverlay").hidden = true;
    editingId = null;
    editVersion = null;
    editorDirty = false;
  }

  async function submitEditor(event) {
    event.preventDefault();
    const btn = byId("saveChapter");
    const values = formValues();
    if (!values.title || !values.content) {
      byId("editorMessage").textContent = "标题与正文都要填写。";
      return;
    }
    btn.disabled = true;
    byId("editorMessage").textContent = "";
    try {
      const saved = editingId
        ? await request("PUT", "/chapters/" + encodeURIComponent(editingId), values)
        : await request("POST", "/chapters", values);
      closeEditor(true);
      await refreshChapters(saved.id);
      toast("这一篇已经保存。");
    } catch (error) {
      byId("editorMessage").textContent = error.message;
      if (error.status === 409) byId("editorMessage").textContent += "（草稿还在编辑框中，请保留内容）";
    } finally {
      btn.disabled = false;
    }
  }

  async function setArchive() {
    if (!current) return;
    const next = current.status === "archived" ? "draft" : "archived";
    if (next === "archived" && !confirm("将这一篇归档？正文与历史版本都会保留。")) return;
    const values = {...current, status: next, expected_version: current.version};
    delete values.id;
    delete values.linked_memories;
    delete values.created_at;
    delete values.updated_at;
    delete values.version;
    delete values.sort_order;
    try {
      await request("PUT", "/chapters/" + encodeURIComponent(current.id), values);
      await refreshChapters();
      toast(next === "archived" ? "已经归档。可以勾选「显示归档章节」找回。" : "已恢复为草稿。");
    } catch (error) { toast(error.message); }
  }

  async function openHistory() {
    if (!current) return;
    try {
      const data = await request("GET", "/chapters/" + encodeURIComponent(current.id) + "/revisions");
      const host = byId("historyItems");
      host.replaceChildren();
      if (!data.items.length) host.append(element("p", "", "这篇还没有旧版本。"));
      data.items.forEach(entry => {
        const row = element("div", "river-history-item");
        row.append(element("span", "", "第 " + entry.version + " 版 · " + dateText(entry.saved_at) + "\n" + entry.title));
        const button = element("button", "", "恢复这一版");
        button.type = "button";
        button.addEventListener("click", () => restore(entry.version));
        row.append(button);
        host.append(row);
      });
      byId("historyOverlay").hidden = false;
    } catch (error) { toast(error.message); }
  }

  async function restore(version) {
    if (!current || !confirm("恢复第 " + version + " 版？当前版本会先保留到修改历史中。")) return;
    const id = current.id;
    try {
      await request("POST", "/chapters/" + encodeURIComponent(id) + "/restore", {
        version, expected_version: current.version
      });
      byId("historyOverlay").hidden = true;
      await refreshChapters(id);
      toast("已恢复旧版本，之前的内容仍然保留。");
    } catch (error) { toast(error.message); }
  }

  async function findMemories() {
    const query = byId("memorySearch").value.trim();
    const host = byId("memorySearchResults");
    host.replaceChildren();
    if (query.length < 2) return;
    try {
      const results = await request("GET", "/memory-search?q=" + encodeURIComponent(query));
      if (byId("memorySearch").value.trim() !== query) return;
      if (!results.items.length) host.append(element("p", "", "没有找到对应记忆。"));
      results.items.forEach(mem => {
        const btn = element("button", "river-find-result");
        btn.type = "button";
        btn.append(element("strong", "", mem.id + (mem.archive_state === "cold" ? " · 已归档" : "")));
        btn.append(element("span", "", mem.content));
        btn.addEventListener("click", () => {
          const ids = cleanList(byId("editMemoryIds").value, false);
          if (!ids.includes(mem.id)) ids.push(mem.id);
          byId("editMemoryIds").value = ids.join("\n");
          editorDirty = true;
          toast("已经关联这条记忆。");
        });
        host.append(btn);
      });
    } catch (error) { host.append(element("p", "", error.message)); }
  }

  function parseManuscript(markdown) {
    // Matches the chapters produced under Lan Memory/搬家施工/河.
    const pattern = /^##\s+([a-z0-9_-]+-RIVER-\d+)\s*[｜|]\s*(.+?)\s*$/gim;
    const matches = Array.from(markdown.matchAll(pattern));
    return matches.map((match, index) => {
      const start = match.index + match[0].length;
      const end = index + 1 < matches.length ? matches[index + 1].index : markdown.length;
      const fragment = markdown.slice(start, end).trim();
      const sourceLine = fragment.match(/^\*\*原文来源\*\*[：:]\s*(.+)$/m);
      const memoryLine = fragment.match(/^\*\*关联记忆\*\*[：:]\s*(.+)$/m);
      const sourceRefs = [];
      if (sourceLine) sourceRefs.push(sourceLine[1].trim());
      // RAW-...-AM ids are manuscript references, not yet AionsHome mem_* ids.
      if (memoryLine) sourceRefs.push("原子记忆卡片：" + memoryLine[1].trim());
      const firstFooter = fragment.search(/^\*\*(?:原文来源|关联记忆|后续线索)\*\*[：:]/m);
      const body = (firstFooter >= 0 ? fragment.slice(0, firstFooter) : fragment).trim();
      return {
        source_key: match[1], title: match[2].trim(), subtitle: "",
        period_label: "", content: body, reflection: "",
        source_refs: sourceRefs, memory_ids: [], status: "draft"
      };
    }).filter(item => item.title && item.content);
  }

  async function importFile(file) {
    const text = await file.text();
    const imported = parseManuscript(text);
    if (!imported.length) {
      toast("没有找到符合格式的篇章标题：## RAW-0005-RIVER-0001｜标题");
      return;
    }
    if (!confirm("发现 " + imported.length + " 篇散文。导入为《河》的草稿？已有相同手稿编号会跳过。")) return;
    let saved = 0, skipped = 0, failed = 0, lastId = null;
    byId("importButton").disabled = true;
    try {
      for (const chapter of imported) {
        try {
          const result = await request("POST", "/chapters", chapter);
          saved += 1;
          lastId = result.id;
        } catch (error) {
          if (error.status === 409) skipped += 1;
          else failed += 1;
        }
      }
      await refreshChapters(lastId);
      toast("导入完成：新增 " + saved + " 篇，跳过重复 " + skipped + " 篇" +
        (failed ? "，失败 " + failed + " 篇" : "") + "。");
    } finally {
      byId("importButton").disabled = false;
    }
  }

  byId("chapterSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refreshChapters().catch(e => toast(e.message)), 220);
  });
  byId("showArchived").addEventListener("change", () => refreshChapters().catch(e => toast(e.message)));
  byId("newButton").addEventListener("click", () => showEditor());
  byId("emptyNewButton").addEventListener("click", () => showEditor());
  byId("editButton").addEventListener("click", () => current && showEditor(current));
  byId("closeEditor").addEventListener("click", () => closeEditor(false));
  byId("cancelEdit").addEventListener("click", () => closeEditor(false));
  byId("editorForm").addEventListener("submit", submitEditor);
  byId("editorForm").addEventListener("input", () => { editorDirty = true; });
  byId("archiveButton").addEventListener("click", setArchive);
  byId("historyButton").addEventListener("click", openHistory);
  byId("closeHistory").addEventListener("click", () => { byId("historyOverlay").hidden = true; });
  byId("memorySearch").addEventListener("input", () => {
    clearTimeout(finderTimer);
    finderTimer = setTimeout(findMemories, 260);
  });
  byId("importButton").addEventListener("click", () => byId("manuscriptFile").click());
  byId("manuscriptFile").addEventListener("change", async event => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (file) {
      try { await importFile(file); } catch (error) { toast("导入中断：" + error.message); }
    }
  });
  byId("editorOverlay").addEventListener("click", e => {
    if (e.target === byId("editorOverlay")) closeEditor(false);
  });
  byId("historyOverlay").addEventListener("click", e => {
    if (e.target === byId("historyOverlay")) byId("historyOverlay").hidden = true;
  });
  window.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (!byId("historyOverlay").hidden) byId("historyOverlay").hidden = true;
    else if (!byId("editorOverlay").hidden) closeEditor(false);
  });
  window.addEventListener("beforeunload", event => {
    if (editorDirty && !byId("editorOverlay").hidden) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  refreshChapters().catch(error => toast("读取《河》失败：" + error.message));
})();
