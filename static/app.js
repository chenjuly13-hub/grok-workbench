const state = {
  config: null,
  models: [],
  efforts: [],
  sessions: [],
  activeId: null,
  session: null,
  view: "chat",
  running: false,
  fileRel: "",
  selectedFile: "",
  termHistory: [],
  termCursor: -1,
  poll: null,
  pending: [],
};

const TOOLS = {
  read_file: "读取文件",
  search_replace: "编辑文件",
  write: "写入文件",
  edit: "编辑文件",
  grep: "搜索内容",
  grep_search: "搜索内容",
  list_dir: "列出目录",
  bash: "运行命令",
  run_terminal_cmd: "运行命令",
  run_terminal_command: "运行命令",
  web_search: "网页搜索",
  web_fetch: "打开网页",
  open_page: "打开网页",
  todo_write: "更新任务",
  task: "子任务",
  spawn_subagent: "子任务",
  kill_task: "停止任务",
  get_task_output: "查看任务",
  search_tool: "查找工具",
  use_tool: "调用工具",
  lsp: "代码分析",
};

const QUICK = [
  ["这个目录是做什么的", "看看当前目录，用几句话说明它是做什么的。"],
  ["审查问题", "检查当前目录里的代码和最近改动，列出真正的问题。"],
  ["检索一个主题", "请检索网页并总结："],
];

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch { data = { error: text }; }
  }
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 2400);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function renderMarkdown(source) {
  const fences = [];
  const stripped = source.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, _lang, code) => {
    const index = fences.length;
    fences.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000${index}\u0000`;
  });
  let html = escapeHtml(stripped);
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");
  html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
  html = html.replace(/(?:<li>.*<\/li>\n?)+/g, (block) => `<ul>${block}</ul>`);
  html = html.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.split(/\n{2,}/).map((block) => {
    const trimmed = block.trim();
    if (!trimmed) return "";
    if (/^\u0000\d+\u0000$/.test(trimmed)) return trimmed;
    if (/^<(h\d|ul|pre)/.test(trimmed) || trimmed.includes("\u0000")) return trimmed;
    return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
  }).join("");
  return html.replace(/\u0000(\d+)\u0000/g, (_, index) => fences[Number(index)] || "");
}

function toolLabel(name) {
  return TOOLS[name] || name || "工具";
}

function statusLabel(status) {
  if (status === "completed") return "完成";
  if (status === "failed" || status === "error") return "失败";
  if (status === "in_progress" || status === "running") return "进行中";
  return status || "进行中";
}

function folderName(path) {
  if (!path) return "未选择目录";
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function when(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const delta = (Date.now() - then.getTime()) / 1000;
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  return iso.slice(5, 10);
}

function setStatus(text, kind) {
  const node = $("status");
  node.className = "status" + (kind ? ` ${kind}` : "");
  node.querySelector("span").textContent = text;
}

function currentCwd() {
  return state.session?.cwd || state.config?.workspace || "";
}

function renderChrome() {
  const full = state.config?.permission !== "read";
  $("modeBadge").textContent = full ? "完整能力" : "只读";
  const cwd = currentCwd();
  const button = $("cwdBtn");
  button.textContent = cwd || "未选择目录";
  button.title = cwd ? `${cwd}\n点击更换目录` : "选择目录";
  $("termCwd").textContent = cwd;
  const model = state.models.find((item) => item.id === state.config?.model);
  const label = model ? model.label : (state.config?.model || "Grok");
  if (!state.running) setStatus(`${label} · 就绪`);
  const banner = $("banner");
  if (state.config && state.grokOk === false) {
    banner.hidden = false;
    banner.textContent = "没有找到 Grok。请在设置里填写 grok.exe 的路径。";
  } else {
    banner.hidden = true;
  }
}

function renderSessions() {
  const list = $("sessionList");
  list.innerHTML = "";
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-side";
    empty.textContent = "还没有对话";
    list.appendChild(empty);
    return;
  }
  state.sessions.forEach((session) => {
    const row = document.createElement("div");
    const live = session.running ? " live" : "";
    row.className = "session" + (session.id === state.activeId ? " active" : "") + live;
    const main = document.createElement("button");
    main.type = "button";
    main.className = "session-open";
    const title = session.title || "新对话";
    const folder = session.cwd || "";
    const time = when(session.updated);
    main.innerHTML = `<strong></strong><em></em>`;
    main.querySelector("strong").textContent = title;
    main.querySelector("em").textContent = [folder, time].filter(Boolean).join(" · ");
    main.title = [title, session.cwd, time].filter(Boolean).join("\n");
    main.addEventListener("click", () => openSession(session.id));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "x";
    remove.setAttribute("aria-label", "删除对话");
    remove.textContent = "×";
    remove.title = "删除";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(session.id);
    });
    row.appendChild(main);
    row.appendChild(remove);
    list.appendChild(row);
  });
}

function renderWelcome() {
  const wrap = document.createElement("div");
  wrap.className = "welcome";
  wrap.innerHTML = `
    <h1>今天做点什么</h1>
    <p>直接说任务。可以粘贴图片，或点「上传文件」。这个窗口里的 Grok 能读改文件、运行命令、搜索代码和网页，也可以把大任务拆开做。</p>
    <div class="caps">
      <span>读改文件</span><span>运行命令</span><span>搜索代码</span>
      <span>网页检索</span><span>子任务</span><span>记住这次对话</span>
    </div>`;
  return wrap;
}

function thoughtBlock(text, open) {
  const details = document.createElement("details");
  details.className = "thought";
  details.open = open;
  const summary = document.createElement("summary");
  summary.textContent = "思考";
  const pre = document.createElement("div");
  pre.textContent = text || "";
  details.append(summary, pre);
  return details;
}

function renderMessage(message, live, showThought) {
  const article = document.createElement("article");
  article.className = "msg " + (message.role === "user" ? "user" : "assistant");
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = message.role === "user" ? "你" : "Grok";
  article.appendChild(who);
  if (message.role === "assistant" && message.thought) {
    article.appendChild(thoughtBlock(message.thought, Boolean(showThought || live)));
  }
  if (message.role === "user" && message.attachments?.length) {
    article.appendChild(renderSavedFiles(message.attachments));
  }
  const body = document.createElement("div");
  body.className = "body" + (live && !message.text ? " caret" : "");
  if (message.role === "user") body.textContent = message.text || "";
  else body.innerHTML = renderMarkdown(message.text || (live ? "" : "（没有回复）"));
  if (message.role !== "user" || message.text) article.appendChild(body);
  if (message.usage && (message.usage.input_tokens || message.usage.output_tokens)) {
    const usage = document.createElement("div");
    usage.className = "usage";
    usage.textContent = `输入 ${message.usage.input_tokens ?? "—"} · 输出 ${message.usage.output_tokens ?? "—"}`;
    article.appendChild(usage);
  }
  return article;
}

function renderTranscript() {
  const box = $("transcript");
  box.innerHTML = "";
  const messages = state.session?.messages || [];
  if (!messages.length) {
    box.appendChild(renderWelcome());
    $("quick").hidden = false;
  } else {
    messages.forEach((message, index) => {
      const last = index === messages.length - 1;
      const live = Boolean(state.running && last && message.role === "assistant");
      box.appendChild(renderMessage(message, live, last));
    });
    $("quick").hidden = true;
  }
  box.scrollTop = box.scrollHeight;
  renderActivity(messages.length ? messages[messages.length - 1] : null);
  $("chatTitle").textContent = state.session?.title || "新对话";
}

function renderActivity(message) {
  const activity = $("activity");
  const plan = $("plan");
  activity.innerHTML = "";
  const tools = message && message.role === "assistant" ? (message.tools || []) : [];
  const entries = message && message.role === "assistant" ? (message.plan || []) : [];
  if (!tools.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "工具调用会显示在这里。";
    activity.appendChild(empty);
  }
  tools.forEach((tool) => {
    const card = document.createElement("div");
    card.className = "act";
    const top = document.createElement("div");
    top.className = "act-top";
    const name = document.createElement("span");
    name.textContent = toolLabel(tool.name);
    const st = document.createElement("em");
    st.textContent = statusLabel(tool.status);
    top.append(name, st);
    card.appendChild(top);
    const detail = tool.input || tool.output || "";
    if (detail) {
      const paragraph = document.createElement("p");
      paragraph.textContent = detail;
      card.appendChild(paragraph);
    }
    activity.appendChild(card);
  });
  if (entries.length) {
    plan.hidden = false;
    plan.innerHTML = "";
    const list = document.createElement("ol");
    entries.forEach((entry) => {
      const item = document.createElement("li");
      if (typeof entry === "string") item.textContent = entry;
      else item.textContent = entry.content || entry.text || entry.title || JSON.stringify(entry);
      list.appendChild(item);
    });
    plan.appendChild(list);
  } else {
    plan.hidden = true;
    plan.innerHTML = "";
  }
}

function showView(view) {
  state.view = view;
  document.body.dataset.view = view;
  $("viewChat").hidden = view !== "chat";
  $("viewFiles").hidden = view !== "files";
  $("viewTerm").hidden = view !== "term";
  document.querySelectorAll(".modes button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (view === "files") loadFiles(state.fileRel);
  if (view === "chat") $("input").focus();
}

async function openSession(id) {
  stopPolling();
  const session = await api(`/api/sessions/${id}`);
  state.activeId = id;
  state.session = session;
  state.fileRel = "";
  state.pending = [];
  state.running = Boolean(session.running);
  renderPending();
  renderSessions();
  renderChrome();
  renderTranscript();
  showView("chat");
  syncRunningControls();
  if (session.running) startPolling();
}

async function newChat() {
  if (state.session && !(state.session.messages || []).length && !state.running) {
    showView("chat");
    $("input").focus();
    return;
  }
  const session = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ cwd: state.config.workspace }),
  });
  state.sessions.unshift(session);
  state.activeId = session.id;
  state.session = session;
  state.running = false;
  state.fileRel = "";
  state.pending = [];
  renderPending();
  renderSessions();
  renderChrome();
  renderTranscript();
  showView("chat");
  syncRunningControls();
  $("input").focus();
}

async function deleteSession(id) {
  if (!confirm("删除这个对话？")) return;
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  state.sessions = state.sessions.filter((item) => item.id !== id);
  if (state.activeId === id) {
    state.activeId = null;
    state.session = null;
    if (state.sessions[0]) await openSession(state.sessions[0].id);
    else await newChat();
    return;
  }
  renderSessions();
}

async function renameSession() {
  const current = state.session?.title || "";
  const title = prompt("对话名称", current);
  if (!title || !title.trim() || title.trim() === current) return;
  const updated = await api(`/api/sessions/${state.session.id}`, {
    method: "PATCH",
    body: JSON.stringify({ title: title.trim() }),
  });
  state.session = updated;
  const found = state.sessions.find((item) => item.id === updated.id);
  if (found) found.title = updated.title;
  renderSessions();
  $("chatTitle").textContent = updated.title;
}

function openCwdDialog() {
  $("cwdInput").value = currentCwd();
  $("cwdError").textContent = "";
  $("cwdDialog").hidden = false;
  $("cwdInput").focus();
  $("cwdInput").select();
}

async function applyCwd(path, makeDefault) {
  const folder = path.trim();
  if (!folder) throw new Error("请填写目录");
  if (!state.session) await newChat();
  const updated = await api(`/api/sessions/${state.session.id}`, {
    method: "PATCH",
    body: JSON.stringify({ cwd: folder }),
  });
  state.session = updated;
  const found = state.sessions.find((item) => item.id === updated.id);
  if (found) found.cwd = updated.cwd;
  if (makeDefault) {
    const saved = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ workspace: folder }),
    });
    state.config = saved.config;
  }
  state.fileRel = "";
  renderChrome();
  renderSessions();
  if (state.view === "files") loadFiles("");
}

async function changeCwd(event) {
  event.preventDefault();
  $("cwdError").textContent = "";
  try {
    await applyCwd($("cwdInput").value, $("cwdDefault").checked);
    $("cwdDialog").hidden = true;
    toast("已更换目录");
  } catch (error) {
    $("cwdError").textContent = error.message;
  }
}

function growInput() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

function syncRunningControls() {
  $("sendBtn").disabled = state.running;
  $("stopBtn").hidden = !state.running;
  $("hint").textContent = state.running ? "正在处理，可以点停止" : "Enter 发送 · 可粘贴图片";
  if (state.running) setStatus("正在处理", "busy");
}

function upsertSessionSummary(session) {
  const brief = {
    id: session.id,
    title: session.title,
    cwd: session.cwd,
    updated: session.updated,
    running: session.running,
    model: session.model,
  };
  const index = state.sessions.findIndex((item) => item.id === session.id);
  if (index >= 0) state.sessions.splice(index, 1);
  state.sessions.unshift(brief);
  renderSessions();
}

function ensureLiveArticle() {
  const box = $("transcript");
  let article = box.querySelector(".msg.assistant.live");
  if (article) return article;
  const welcome = box.querySelector(".welcome");
  if (welcome) welcome.remove();
  $("quick").hidden = true;
  article = document.createElement("article");
  article.className = "msg assistant live";
  article.innerHTML = '<div class="who">Grok</div><div class="body caret"></div>';
  box.appendChild(article);
  return article;
}

function updateLive(article) {
  const message = state.live;
  const body = article.querySelector(".body");
  body.classList.toggle("caret", state.running && !message.text);
  body.innerHTML = renderMarkdown(message.text || "");
  let thought = article.querySelector(".thought");
  if (message.thought) {
    if (!thought) {
      thought = thoughtBlock(message.thought, true);
      article.insertBefore(thought, body);
    }
    const pre = thought.querySelector("div");
    pre.textContent = message.thought;
    thought.open = true;
    pre.scrollTop = pre.scrollHeight;
  }
  renderActivity(message);
  const box = $("transcript");
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  if (near) box.scrollTop = box.scrollHeight;
}

function attachmentUrl(sessionId, item) {
  return `/api/attachment/${sessionId}/${item.id}`;
}

function renderSavedFiles(attachments) {
  const sessionId = state.session?.id || "";
  const row = document.createElement("div");
  row.className = "msg-files";
  attachments.forEach((item) => {
    if (item.kind === "image") {
      const image = document.createElement("img");
      image.alt = item.name || "图片";
      image.src = attachmentUrl(sessionId, item);
      row.appendChild(image);
    } else {
      const pill = document.createElement("span");
      pill.className = "file-pill";
      pill.textContent = item.name || "文件";
      row.appendChild(pill);
    }
  });
  return row;
}

function renderPending() {
  const row = $("attachRow");
  if (!row) return;
  row.innerHTML = "";
  if (!state.pending.length) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  state.pending.forEach((item, index) => {
    const chip = document.createElement("div");
    chip.className = "attach";
    if (item.kind === "image") {
      const image = document.createElement("img");
      image.alt = item.name || "图片";
      image.src = attachmentUrl(item.sessionId || state.session.id, item);
      chip.appendChild(image);
    } else {
      const mark = document.createElement("div");
      mark.className = "file-mark";
      const ext = (item.name || "").split(".").pop() || "文件";
      mark.textContent = ext.slice(0, 4).toUpperCase();
      chip.appendChild(mark);
    }
    const name = document.createElement("span");
    name.textContent = item.name || "附件";
    name.title = item.name || "";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", "移除附件");
    remove.addEventListener("click", () => {
      state.pending.splice(index, 1);
      renderPending();
    });
    chip.append(name, remove);
    row.appendChild(chip);
  });
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("无法读取文件"));
    reader.readAsDataURL(file);
  });
}

async function addFiles(fileList) {
  const files = [...fileList].filter(Boolean);
  if (!files.length) return;
  if (!state.session) await newChat();
  const room = 8 - state.pending.length;
  if (room <= 0) {
    toast("一次最多 8 个附件");
    return;
  }
  const chosen = files.slice(0, room);
  if (files.length > room) toast("一次最多 8 个附件，多出来的没有加入");
  for (const file of chosen) {
    if (!file.size) {
      toast(`${file.name || "文件"} 是空的`);
      continue;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast(`${file.name || "文件"} 超过 20 MB`);
      continue;
    }
    const data = await readFileAsBase64(file);
    const saved = await api("/api/upload", {
      method: "POST",
      body: JSON.stringify({
        sessionId: state.session.id,
        name: file.name || "图片.png",
        mime: file.type || "application/octet-stream",
        data,
      }),
    });
    state.pending.push(saved);
    renderPending();
  }
}

async function sendMessage(text) {
  const attachments = state.pending.slice();
  if ((!text && !attachments.length) || state.running) return;
  if (!state.session) await newChat();
  state.running = true;
  syncRunningControls();
  setStatus("正在启动 Grok", "busy");
  const label = text || attachments[0].name || "附件";
  if (!state.session.title || state.session.title === "新对话") {
    state.session.title = label.split("\n")[0].slice(0, 24);
    $("chatTitle").textContent = state.session.title;
    const found = state.sessions.find((item) => item.id === state.session.id);
    if (found) found.title = state.session.title;
    renderSessions();
  }
  const box = $("transcript");
  const welcome = box.querySelector(".welcome");
  if (welcome) welcome.remove();
  $("quick").hidden = true;
  box.appendChild(renderMessage({ role: "user", text, attachments }, false));
  state.live = { role: "assistant", text: "", thought: "", tools: [], plan: [], usage: null };
  const article = ensureLiveArticle();
  box.scrollTop = box.scrollHeight;
  $("input").value = "";
  state.pending = [];
  renderPending();
  growInput();
  let response;
  const restoreDraft = () => {
    $("input").value = text;
    state.pending = attachments;
    renderPending();
    growInput();
  };
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sessionId: state.session.id,
        text,
        attachmentIds: attachments.map((item) => item.id),
      }),
    });
  } catch (error) {
    state.running = false;
    syncRunningControls();
    setStatus("没有连上", "bad");
    toast(error.message || "没有连上");
    restoreDraft();
    return;
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    state.running = false;
    syncRunningControls();
    setStatus("未能开始", "bad");
    toast(data.error || "未能开始");
    restoreDraft();
    return;
  }
  await readStream(response, article);
}

async function readStream(response, article) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const tools = new Map();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let event;
        try { event = JSON.parse(trimmed); } catch { continue; }
        applyEvent(event, tools);
        updateLive(article);
      }
    }
  } catch (error) {
    toast("连接中断，正在取回结果");
    await refreshActive();
  }
  article.classList.remove("live");
  state.running = false;
  syncRunningControls();
  await refreshActive();
  renderChrome();
}

function applyEvent(event, tools) {
  const live = state.live;
  if (event.type === "started" && event.sessionId) {
    state.session.id = event.sessionId;
    state.activeId = event.sessionId;
    setStatus("正在处理", "busy");
  } else if (event.type === "text") {
    live.text += event.data || "";
    setStatus("正在回复", "busy");
  } else if (event.type === "thought") {
    live.thought += event.data || "";
  } else if (event.type === "tool") {
    const tool = {
      id: event.id || String(tools.size),
      name: event.name,
      status: event.status || "in_progress",
      input: event.input || "",
      output: "",
    };
    tools.set(tool.id, tool);
    live.tools = [...tools.values()];
    setStatus(toolLabel(tool.name), "busy");
  } else if (event.type === "tool_update") {
    const tool = tools.get(event.id);
    if (tool) {
      if (event.status) tool.status = event.status;
      if (event.output) tool.output = event.output;
      live.tools = [...tools.values()];
    }
  } else if (event.type === "plan") {
    live.plan = event.entries || [];
  } else if (event.type === "meta") {
    if (event.usage) live.usage = event.usage;
  } else if (event.type === "error") {
    if (event.message && event.message !== "已停止" && !live.text.includes(event.message)) {
      live.text += (live.text ? "\n\n" : "") + event.message;
    }
    if (event.message === "已停止" && !live.text) live.text = "已停止。";
    setStatus(event.message === "已停止" ? "已停止" : "出错了", event.message === "已停止" ? "" : "bad");
  } else if (event.type === "done") {
    if (event.usage) live.usage = event.usage;
  }
}

async function refreshActive() {
  if (!state.activeId) return;
  const session = await api(`/api/sessions/${state.activeId}`);
  state.session = session;
  state.running = Boolean(session.running);
  upsertSessionSummary(session);
  if (!state.running) {
    renderTranscript();
    renderChrome();
    syncRunningControls();
  }
}

function startPolling() {
  stopPolling();
  state.poll = setInterval(async () => {
    if (!state.activeId) return;
    const session = await api(`/api/sessions/${state.activeId}`);
    state.session = session;
    state.running = Boolean(session.running);
    renderTranscript();
    syncRunningControls();
    if (!session.running) {
      stopPolling();
      renderChrome();
    }
  }, 1000);
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function stopRun() {
  if (!state.session) return;
  await api("/api/stop", { method: "POST", body: JSON.stringify({ sessionId: state.session.id }) });
  setStatus("正在停止", "busy");
}

function fillSettings() {
  const model = $("setModel");
  const effort = $("setEffort");
  model.innerHTML = "";
  effort.innerHTML = "";
  state.models.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    model.appendChild(option);
  });
  state.efforts.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    effort.appendChild(option);
  });
  model.value = state.config.model;
  effort.value = state.config.effort;
  document.querySelectorAll('input[name="permission"]').forEach((input) => {
    input.checked = input.value === state.config.permission;
  });
  $("setWorkspace").value = state.config.workspace;
  $("setGrok").value = state.config.grokPath;
  $("grokState").textContent = state.grokOk ? "已找到 Grok，可以直接对话。" : "还没有找到 Grok。";
}

async function saveSettings(event) {
  event.preventDefault();
  const permission = document.querySelector('input[name="permission"]:checked')?.value || "full";
  const updated = await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      model: $("setModel").value,
      effort: $("setEffort").value,
      permission,
      workspace: $("setWorkspace").value,
      grokPath: $("setGrok").value,
    }),
  });
  state.config = updated.config;
  state.grokOk = Boolean(state.config.grokPath);
  const check = await api("/api/bootstrap");
  state.grokOk = check.grok.ok;
  state.config = check.config;
  renderChrome();
  $("settings").hidden = true;
  toast("已保存");
}

async function loadFiles(rel) {
  const cwd = currentCwd();
  if (!cwd) return;
  state.fileRel = rel || "";
  const data = await api(`/api/files?${new URLSearchParams({ cwd, path: state.fileRel })}`);
  const crumbs = $("crumbs");
  crumbs.innerHTML = "";
  const root = document.createElement("button");
  root.type = "button";
  root.textContent = cwd;
  root.addEventListener("click", () => loadFiles(""));
  crumbs.appendChild(root);
  if (data.rel) {
    const parts = data.rel.split(/[\\/]/);
    parts.forEach((part, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = " / " + part;
      const next = parts.slice(0, index + 1).join("\\");
      button.addEventListener("click", () => loadFiles(next));
      crumbs.appendChild(button);
    });
  }
  const list = $("fileList");
  list.innerHTML = "";
  if (data.parent !== null && data.parent !== undefined && data.rel) {
    const up = document.createElement("button");
    up.type = "button";
    up.className = "file-item";
    up.textContent = "返回上一级";
    up.addEventListener("click", () => loadFiles(data.parent || ""));
    list.appendChild(up);
  }
  data.entries.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "file-item";
    const name = document.createElement("span");
    name.textContent = (entry.dir ? "▸ " : "") + entry.name;
    const meta = document.createElement("small");
    meta.textContent = entry.dir ? "" : formatSize(entry.size);
    button.append(name, meta);
    const child = data.rel ? `${data.rel}\\${entry.name}` : entry.name;
    button.addEventListener("click", () => {
      if (entry.dir) loadFiles(child);
      else previewFile(child, entry.name);
    });
    list.appendChild(button);
  });
  if (data.truncated) {
    const note = document.createElement("div");
    note.className = "muted";
    note.style.padding = "8px 12px";
    note.textContent = "只显示了前 400 项。";
    list.appendChild(note);
  }
}

function formatSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.ceil(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

async function previewFile(rel, name) {
  state.selectedFile = rel;
  const cwd = currentCwd();
  const data = await api(`/api/file?${new URLSearchParams({ cwd, path: rel })}`);
  const preview = $("preview");
  preview.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = data.name || name;
  preview.appendChild(title);
  if (data.blocked) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "这个文件可能包含密钥，预览已隐藏。";
    preview.appendChild(note);
    return;
  }
  if (data.image) {
    const image = document.createElement("img");
    image.alt = data.name || name;
    image.src = `/api/raw?${new URLSearchParams({ cwd, path: rel })}`;
    preview.appendChild(image);
  } else if (data.binary) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "这个文件不能作为文本预览。";
    preview.appendChild(note);
  } else {
    const pre = document.createElement("pre");
    pre.textContent = data.text || "";
    preview.appendChild(pre);
    if (data.truncated) {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "只显示了开头一部分。";
      preview.appendChild(note);
    }
  }
  const insert = document.createElement("button");
  insert.type = "button";
  insert.className = "insert";
  insert.textContent = "插入到对话";
  insert.addEventListener("click", () => {
    const input = $("input");
    const prefix = input.value && !input.value.endsWith("\n") ? "\n" : "";
    input.value += `${prefix}${cwd}\\${rel}`;
    showView("chat");
    growInput();
    input.focus();
  });
  preview.appendChild(insert);
}

async function runTerminal(command) {
  const out = $("termOut");
  out.textContent += `\nPS> ${command}\n`;
  out.scrollTop = out.scrollHeight;
  try {
    const result = await api("/api/terminal", {
      method: "POST",
      body: JSON.stringify({ cwd: currentCwd(), command }),
    });
    out.textContent += result.output || (result.code === 0 ? "" : `退出代码 ${result.code}\n`);
  } catch (error) {
    out.textContent += `${error.message}\n`;
  }
  out.scrollTop = out.scrollHeight;
}

function bind() {
  $("newChat").addEventListener("click", () => newChat().catch((error) => toast(error.message)));
  document.querySelectorAll(".modes button").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage($("input").value.trim()).catch((error) => toast(error.message));
  });
  $("input").addEventListener("input", growInput);
  $("uploadBtn").addEventListener("click", () => $("filePick").click());
  $("filePick").addEventListener("change", () => {
    const files = [...($("filePick").files || [])];
    $("filePick").value = "";
    addFiles(files).catch((error) => toast(error.message));
  });
  $("composer").addEventListener("paste", (event) => {
    const items = [...(event.clipboardData?.items || [])];
    const files = items.filter((item) => item.kind === "file").map((item) => item.getAsFile()).filter(Boolean);
    if (!files.length) return;
    event.preventDefault();
    const pasted = event.clipboardData.getData("text/plain");
    if (pasted) {
      const input = $("input");
      const start = input.selectionStart ?? input.value.length;
      const end = input.selectionEnd ?? start;
      input.value = input.value.slice(0, start) + pasted + input.value.slice(end);
      growInput();
    }
    addFiles(files).catch((error) => toast(error.message));
  });
  $("composer").addEventListener("dragenter", (event) => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault();
    $("composer").classList.add("drag");
  });
  $("composer").addEventListener("dragover", (event) => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault();
    $("composer").classList.add("drag");
  });
  $("composer").addEventListener("dragleave", (event) => {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    $("composer").classList.remove("drag");
  });
  $("composer").addEventListener("drop", (event) => {
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    $("composer").classList.remove("drag");
    addFiles(files).catch((error) => toast(error.message));
  });
  $("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("stopBtn").addEventListener("click", () => stopRun().catch((error) => toast(error.message)));
  $("chatTitle").addEventListener("click", () => renameSession().catch((error) => toast(error.message)));
  $("cwdBtn").addEventListener("click", openCwdDialog);
  $("cwdForm").addEventListener("submit", (event) => changeCwd(event));
  $("closeCwd").addEventListener("click", () => { $("cwdDialog").hidden = true; });
  $("cwdDialog").addEventListener("click", (event) => {
    if (event.target === $("cwdDialog")) $("cwdDialog").hidden = true;
  });
  $("cwdBrowse").addEventListener("click", async () => {
    $("cwdError").textContent = "正在打开选择窗口，如果被挡住，请看任务栏。";
    try {
      const chosen = await api("/api/pick-folder", {
        method: "POST",
        body: JSON.stringify({ initial: $("cwdInput").value.trim() }),
      });
      if (chosen.path) {
        $("cwdInput").value = chosen.path;
        $("cwdError").textContent = "";
      } else {
        $("cwdError").textContent = "没有选中文件夹。";
      }
    } catch (error) {
      $("cwdError").textContent = error.message;
    }
  });
  $("openSettings").addEventListener("click", () => {
    fillSettings();
    $("settings").hidden = false;
  });
  $("closeSettings").addEventListener("click", () => { $("settings").hidden = true; });
  $("settings").addEventListener("click", (event) => {
    if (event.target === $("settings")) $("settings").hidden = true;
  });
  $("settingsForm").addEventListener("submit", (event) => saveSettings(event).catch((error) => toast(error.message)));
  $("pickWorkspace").addEventListener("click", async () => {
    try {
      const chosen = await api("/api/pick-folder", {
        method: "POST",
        body: JSON.stringify({ initial: $("setWorkspace").value.trim() }),
      });
      if (chosen.path) $("setWorkspace").value = chosen.path;
      else toast("没有选中文件夹");
    } catch (error) {
      toast(error.message);
    }
  });
  $("shutdownBtn").addEventListener("click", async () => {
    await api("/api/shutdown", { method: "POST", body: "{}" });
    document.body.innerHTML = "<p style='padding:40px;color:#f4efe6'>工作台已退出，可以关闭这个窗口。</p>";
  });
  $("termForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const command = $("termInput").value.trim();
    if (!command) return;
    state.termHistory.unshift(command);
    state.termCursor = -1;
    $("termInput").value = "";
    runTerminal(command);
  });
  $("termInput").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    if (event.key === "ArrowUp") state.termCursor = Math.min(state.termCursor + 1, state.termHistory.length - 1);
    else state.termCursor = Math.max(state.termCursor - 1, -1);
    $("termInput").value = state.termCursor >= 0 ? state.termHistory[state.termCursor] : "";
  });
  $("termClear").addEventListener("click", () => { $("termOut").textContent = ""; });
  document.addEventListener("keydown", (event) => {
    if (event.key === "n" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault();
      newChat().catch((error) => toast(error.message));
    }
  });
  const quick = $("quick");
  QUICK.forEach(([label, text]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => {
      $("input").value = text;
      growInput();
      $("input").focus();
    });
    quick.appendChild(button);
  });
}

async function boot() {
  const data = await api("/api/bootstrap");
  state.config = data.config;
  state.models = data.models;
  state.efforts = data.efforts;
  state.sessions = data.sessions;
  state.grokOk = data.grok.ok;
  bind();
  renderChrome();
  if (state.sessions[0]) await openSession(state.sessions[0].id);
  else await newChat();
}

boot().catch((error) => {
  document.body.innerHTML = `<p style="padding:40px">无法打开工作台：${escapeHtml(error.message)}</p>`;
});
