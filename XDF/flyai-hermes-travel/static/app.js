const loginPanel = document.querySelector("#loginPanel");
const loginForm = document.querySelector("#loginForm");
const passwordInput = document.querySelector("#passwordInput");
const workspace = document.querySelector("#workspace");
const queryForm = document.querySelector("#queryForm");
const queryInput = document.querySelector("#queryInput");
const queryButton = document.querySelector("#queryButton");
const results = document.querySelector("#results");
const resultMeta = document.querySelector("#resultMeta");
const historyList = document.querySelector("#historyList");
const historySearch = document.querySelector("#historySearch");
const refreshHistory = document.querySelector("#refreshHistory");
const healthStatus = document.querySelector("#healthStatus");
const quotaStatus = document.querySelector("#quotaStatus");
const adminLink = document.querySelector("#adminLink");
const logoutButton = document.querySelector("#logoutButton");
const promptChips = document.querySelector("#promptChips");
const queryCount = document.querySelector("#queryCount");
const clearQueryButton = document.querySelector("#clearQueryButton");
const cancelQueryButton = document.querySelector("#cancelQueryButton");
let activeStreamLog = null;
let activeStreamText = "";
let activeQueryController = null;
let currentUser = null;
let historyItems = [];
let activeConversation = null;
let activeConfirmation = null;
let pendingSearchQuery = "";
let activeSearchConversationId = null;

const promptTemplates = [
  {
    label: "查往返机票",
    hint: "航班号、时间、价格",
    query: "北京东京往返机票，停留 5 晚，上午北京出发，下午或晚上东京返回，不转机，给我最低价方案，要航班号和价格。",
  },
  {
    label: "找假期目的地",
    hint: "安全、直飞、预算友好",
    query: "上海出发，暑假夫妻两个人出国游 7 天，价差和非暑假差距不要太大，安全、发达、直飞，不要重要转机。",
  },
  {
    label: "筛酒店",
    hint: "位置、评分、预算",
    query: "杭州西湖附近 5 月 20 到 22 号酒店，预算每晚 800 以内，优先评分高、交通方便。",
  },
  {
    label: "做行程",
    hint: "每天安排和住宿区域",
    query: "成都 4 天 3 晚亲子旅行攻略，节奏不要太赶，给每天安排和适合住的区域。",
  },
];

async function request(path, options = {}) {
  const response = await fetch(path.replace(/^\//, ""), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  return response.json();
}

function setAuthenticated(value) {
  loginPanel.classList.toggle("is-hidden", value);
  workspace.classList.toggle("is-hidden", !value);
  logoutButton.classList.toggle("is-hidden", !value);
  if (!value) {
    adminLink.classList.add("is-hidden");
    quotaStatus.textContent = "";
  }
}

async function boot() {
  renderPromptChips();
  updateQueryCount();
  await loadHealth();
  try {
    await loadMe();
    setAuthenticated(true);
    await loadHistory();
    renderEmptyState();
  } catch {
    setAuthenticated(false);
  }
}

async function loadHealth() {
  try {
    const health = await request("/api/health", { headers: {} });
    healthStatus.textContent = health.ok ? "环境可用" : "环境未就绪";
    healthStatus.classList.toggle("ok", health.ok);
    healthStatus.classList.toggle("warn", !health.ok);
    if (!health.ok) {
      healthStatus.title = [
        health.message || "服务暂时不可用",
      ]
        .filter(Boolean)
        .join("；");
    }
  } catch {
    healthStatus.textContent = "健康检查失败";
    healthStatus.classList.add("warn");
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: passwordInput.value }),
    });
    passwordInput.value = "";
    await loadMe();
    setAuthenticated(true);
    await loadHistory();
    renderEmptyState();
  } catch (error) {
    passwordInput.focus();
    passwordInput.value = "";
    passwordInput.placeholder = error.message || "口令不正确";
  }
});

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = queryInput.value.trim();
  if (!message) return;

  activeQueryController = new AbortController();
  setQueryBusy(true);
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = "正在整理你的旅行需求...";

  try {
    await ensureConversation();
    appendChatMessage({ role: "user", content: message, message_type: "user_text" });
    queryInput.value = "";
    updateQueryCount();
    await sendConversationMessage(message, activeQueryController.signal);
    await loadMe();
  } catch (error) {
    const wasAbort = error?.name === "AbortError";
    appendResultGroup([
      {
        type: "notice",
        severity: wasAbort ? "warning" : "error",
        title: wasAbort ? "已停止等待" : "请求失败",
        items: [wasAbort ? "你已停止本次查询。可以调整条件后重新发起。" : error.message || "服务端没有返回可用结果。"],
      },
    ], "error", 0, new Date().toISOString());
  } finally {
    activeQueryController = null;
    setQueryBusy(false);
    if (pendingSearchQuery) {
      const queryToRun = pendingSearchQuery;
      pendingSearchQuery = "";
      startConversationSearch(queryToRun);
    }
  }
});

queryInput.addEventListener("input", updateQueryCount);
clearQueryButton.addEventListener("click", () => {
  queryInput.value = "";
  updateQueryCount();
  queryInput.focus();
});
refreshHistory.addEventListener("click", loadHistory);
historySearch.addEventListener("input", () => renderHistory(historyItems));
logoutButton.addEventListener("click", logout);
results.addEventListener("click", (event) => {
  const searchButton = event.target.closest("[data-start-search]");
  if (searchButton) {
    if (searchButton.disabled) return;
    const query = searchButton.dataset.query || activeConfirmation?.search_query || "";
    startConversationSearch(query);
    return;
  }
  const button = event.target.closest("[data-template-index]");
  if (!button) return;
  fillPromptTemplate(Number(button.dataset.templateIndex || 0));
});
cancelQueryButton.addEventListener("click", () => {
  if (!activeQueryController) return;
  activeQueryController.abort();
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = "已停止等待，本次页面不会继续接收结果。";
});

async function logout() {
  if (activeQueryController) activeQueryController.abort();
  await request("/api/logout", { method: "POST" }).catch(() => {});
  currentUser = null;
  activeStreamLog = null;
  activeStreamText = "";
  activeQueryController = null;
  activeConversation = null;
  activeConfirmation = null;
  pendingSearchQuery = "";
  activeSearchConversationId = null;
  queryInput.value = "";
  historyList.innerHTML = "";
  results.innerHTML = "";
  resultMeta.classList.remove("is-visible");
  setAuthenticated(false);
  updateQueryCount();
  passwordInput.focus();
}

async function loadHistory() {
  historyItems = await request("/api/conversations");
  renderHistory(historyItems);
}

function renderHistory(items) {
  historyList.innerHTML = "";
  const keyword = historySearch.value.trim().toLowerCase();
  const visible = keyword
    ? items.filter((item) => (item.title || "").toLowerCase().includes(keyword))
    : items;
  if (!visible.length) {
    historyList.innerHTML = `<p class="small">${items.length ? "没有匹配的会话" : "暂无旅行会话"}</p>`;
    return;
  }

  for (const item of visible) {
    const button = document.createElement("button");
    button.className = "history-item";
    button.innerHTML = `
      <strong>${escapeHtml(item.title || "新的旅行计划")}</strong>
      <span>${formatDate(item.updated_at || item.created_at)} · ${conversationStatusText(item.status)}</span>
    `;
    button.addEventListener("click", () => loadConversation(item.id));
    historyList.appendChild(button);
  }
}

async function loadMe() {
  currentUser = await request("/api/me");
  renderUserState();
  return currentUser;
}

function renderUserState() {
  if (!currentUser) return;
  const quota = currentUser.quota || {};
  const userLabel = currentUser.label || "用户";
  const quotaText = quota.unlimited ? "不限次数" : `今日剩余 ${quota.remaining_today}/${quota.daily_limit}`;
  quotaStatus.textContent = `${userLabel} · ${quotaText}`;
  quotaStatus.title = `单用户并发 ${quota.max_concurrent || 1} · 超时 ${quota.timeout_seconds || 300}s`;
  quotaStatus.classList.toggle("warn", !quota.unlimited && quota.remaining_today <= 1);
  adminLink.classList.toggle("is-hidden", currentUser.role !== "owner");
}

async function ensureConversation() {
  if (activeConversation?.id) return activeConversation;
  activeConversation = await request("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新的旅行计划" }),
  });
  renderConversation(activeConversation);
  await loadHistory().catch(() => {});
  return activeConversation;
}

async function loadConversation(id) {
  activeConversation = await request(`/api/conversations/${id}`);
  renderConversation(activeConversation);
}

function renderConversation(conversation) {
  activeConversation = conversation;
  activeConfirmation = null;
  results.innerHTML = "";
  const messages = conversation.messages || [];
  if (!messages.length) {
    renderEmptyState();
    return;
  }
  const thread = ensureThread();
  for (const message of messages) {
    renderConversationMessage(message, thread);
  }
  renderProfileSummary(conversation.profile || {});
}

async function sendConversationMessage(message, signal) {
  const conversation = await ensureConversation();
  const response = await fetch(`api/conversations/${conversation.id}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    signal,
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  await readSse(response, (message) => handleConversationStreamMessage(message));
}

function handleConversationStreamMessage(message) {
  if (message.event !== "message") return false;
  const payload = message.data || {};
  if (payload.conversation) {
    activeConversation = payload.conversation;
    renderProfileSummary(activeConversation.profile || {});
  }
  if (payload.message) {
    renderConversationMessage(payload.message);
  }
  if (payload.action === "search_requested") {
    const query = payload.message?.data?.search_query || activeConfirmation?.search_query || "";
    if (query) pendingSearchQuery = query;
  }
  loadHistory().catch(() => {});
  return true;
}

function renderConversationMessage(message, targetThread = null) {
  const thread = targetThread || ensureThread();
  if (message.message_type === "search_result") {
    const data = message.data || {};
    appendResultGroup(data.blocks || [], data.status, data.duration_ms, data.created_at);
    return;
  }
  if (message.message_type === "xhs_posts") {
    appendBlocks(message.data?.blocks || []);
    return;
  }
  if (message.message_type === "search_confirmation") {
    appendChatMessage(message, thread);
    renderConfirmationCard(message.data?.confirmation || null, thread);
    return;
  }
  appendChatMessage(message, thread);
}

function ensureThread() {
  let thread = results.querySelector(".conversation-thread");
  if (!thread) {
    if (results.querySelector(".empty-state")) results.innerHTML = "";
    thread = document.createElement("div");
    thread.className = "conversation-thread";
    results.appendChild(thread);
  }
  return thread;
}

function appendChatMessage(message, targetThread = null) {
  const thread = targetThread || ensureThread();
  const bubble = document.createElement("article");
  const role = message.role === "user" ? "user" : "assistant";
  bubble.className = `chat-message ${role}`;
  bubble.innerHTML = `
    <div class="chat-avatar">${role === "user" ? "你" : "助"}</div>
    <div class="chat-bubble">
      <p>${escapeHtml(message.content || "")}</p>
    </div>
  `;
  thread.appendChild(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "end" });
}

function renderConfirmationCard(confirmation, targetThread = null) {
  if (!confirmation) return;
  activeConfirmation = confirmation;
  const thread = targetThread || ensureThread();
  expirePreviousSearchButtons(thread);
  const card = document.createElement("article");
  card.className = "card confirmation-card";
  const facts = (confirmation.facts || []).filter((item) => item.value);
  const searchState = confirmationSearchState();
  card.innerHTML = `
    <div class="card-body">
      <div class="card-title">
        <h3>${escapeHtml(confirmation.title || "确认查询条件")}</h3>
      </div>
      ${confirmation.summary ? `<p class="small">${escapeHtml(confirmation.summary)}</p>` : ""}
      <div class="confirm-grid">
        ${facts.map((item) => `<div><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`).join("")}
      </div>
      ${confirmation.preferences?.length ? compactTags(confirmation.preferences) : ""}
      ${confirmation.avoid?.length ? `<p class="small">避开：${escapeHtml(confirmation.avoid.join("、"))}</p>` : ""}
      <div class="confirm-actions">
        <span>确认后会消耗一次实时查询额度。</span>
        <button type="button" data-start-search data-current-confirmation="true" data-query="${escapeHtml(confirmation.search_query || "")}" ${searchState.disabled ? "disabled" : ""}>${escapeHtml(searchState.label)}</button>
      </div>
    </div>
  `;
  thread.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "end" });
}

function expirePreviousSearchButtons(scope = document) {
  scope.querySelectorAll("[data-start-search]").forEach((button) => {
    button.dataset.currentConfirmation = "false";
    button.disabled = true;
    if (button.textContent === "开始查询") {
      button.textContent = "已更新";
    }
  });
}

function confirmationSearchState() {
  if (activeSearchConversationId && activeConversation?.id === activeSearchConversationId) {
    return { disabled: true, label: "查询中" };
  }
  switch (activeConversation?.status) {
    case "ready":
      return { disabled: false, label: "开始查询" };
    case "running":
      return { disabled: true, label: "查询中" };
    case "result":
      return { disabled: true, label: "已查询" };
    case "error":
      return { disabled: true, label: "已结束" };
    default:
      return { disabled: true, label: "继续补充" };
  }
}

function renderProfileSummary(profile) {
  const summary = profile.summary || "继续补充目的地、时间、预算和偏好";
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = activeConversation ? `${activeConversation.title || "旅行计划"} · ${summary}` : summary;
}

async function startConversationSearch(query) {
  if (!activeConversation?.id || activeQueryController) return;
  activeSearchConversationId = activeConversation.id;
  activeQueryController = new AbortController();
  setQueryBusy(true, "查询中");
  setSearchButtonsBusy(true, "查询中");
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = "确认收到，正在调用实时旅行数据源...";
  renderStreamShell(conversationSearchSummary(query));
  try {
    await streamQuery(query, activeQueryController.signal, activeConversation.id);
    await loadMe();
    await loadConversation(activeConversation.id);
  } catch (error) {
    const wasAbort = error?.name === "AbortError";
    appendResultGroup([
      {
        type: "notice",
        severity: wasAbort ? "warning" : "error",
        title: wasAbort ? "已停止等待" : "查询失败",
        items: [wasAbort ? "你已停止本次查询。可以继续改条件后重新发起。" : error.message || "服务端没有返回可用结果。"],
      },
    ], "error", 0, new Date().toISOString());
  } finally {
    activeQueryController = null;
    activeSearchConversationId = null;
    setQueryBusy(false);
    setSearchButtonsBusy(false);
  }
}

function setSearchButtonsBusy(value, label = "查询中") {
  document.querySelectorAll("[data-start-search]").forEach((button) => {
    if (button.dataset.currentConfirmation !== "true") {
      button.disabled = true;
      if (button.textContent === "开始查询") button.textContent = "已更新";
      return;
    }
    const state = confirmationSearchState();
    button.disabled = value || state.disabled;
    button.textContent = value ? label : state.label;
  });
}

function conversationSearchSummary(query) {
  const profile = activeConversation?.profile || {};
  return profile.summary || activeConfirmation?.summary || summarizeInternalQuery(query) || "已确认的旅行需求";
}

function summarizeInternalQuery(query) {
  const text = String(query || "").trim();
  if (!text) return "";
  if (text.startsWith("请基于以下多轮旅行需求") || text.startsWith("你是旅行查询 agent")) {
    const match = text.match(/旅行概要[：:]\s*(.+)/);
    return match ? match[1].trim() : "";
  }
  return text.length > 120 ? `${text.slice(0, 120)}...` : text;
}

function renderResult(data) {
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = `${data.status === "success" ? "查询完成" : "查询异常"} · ${durationText(data.duration_ms)} · ${formatDate(data.created_at)}`;
  activeStreamLog = null;
  activeStreamText = "";
  renderBlocks(data.blocks || []);
}

async function streamQuery(query, signal, conversationId = null) {
  const path = conversationId ? `api/conversations/${conversationId}/search/stream` : "api/query/stream";
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    signal,
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应。");
  }
  return readSse(response, (message) => handleStreamMessage(message, { conversationMode: Boolean(conversationId) }));
}

async function readSse(response, onMessage) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let completed = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replaceAll("\r\n", "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const message = parseSseFrame(frame);
      if (message) {
        completed = onMessage(message) || completed;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }

  if (!completed) {
    throw new Error("流式查询结束，但没有收到最终结果。");
  }
}

function parseSseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}

function handleStreamMessage(message, options = {}) {
  if (message.event === "progress") {
    const payload = message.data || {};
    const seconds = Math.max(0, Math.round((payload.elapsed_ms || 0) / 1000));
    if (payload.kind === "queued") {
      resultMeta.textContent = "正在排队，马上开始查询...";
      setProgressStep("queue");
    } else if (payload.kind === "heartbeat") {
      resultMeta.textContent = `还在等实时结果 · ${seconds}s`;
      setProgressStep("search");
    } else if (payload.kind === "xhs") {
      resultMeta.textContent = "实时查询进行中，也在补充小红书灵感...";
      setProgressStep("xhs");
    } else {
      resultMeta.textContent = `正在查询实时旅行信息 · ${seconds}s`;
      setProgressStep("search");
    }
    if (!["heartbeat", "chunk"].includes(payload.kind)) appendStreamLog(payload.message || "");
    return false;
  }

  if (message.event === "result") {
    setProgressStep("shape");
    if (options.conversationMode) {
      appendResultGroup(message.data?.blocks || [], message.data?.status, message.data?.duration_ms, message.data?.created_at);
      resultMeta.classList.add("is-visible");
      resultMeta.textContent = `${message.data?.status === "success" ? "查询完成" : "查询异常"} · ${durationText(message.data?.duration_ms)} · ${formatDate(message.data?.created_at)}`;
    } else {
      renderResult(message.data);
    }
    loadHistory().catch(() => {});
    return true;
  }
  if (message.event === "supplement") {
    const payload = message.data || {};
    appendBlocks(payload.blocks || []);
    resultMeta.classList.add("is-visible");
    resultMeta.textContent = payload.message || "已补充更多旅行灵感。";
    loadHistory().catch(() => {});
    return true;
  }
  return false;
}

function renderStreamShell(query) {
  activeStreamText = "";
  const card = document.createElement("article");
  card.className = "card stream-card";
  card.innerHTML = `
    <div class="card-body">
      <div class="card-title">
        <h3>正在整理你的旅行方案</h3>
      </div>
      <p class="stream-query">${escapeHtml(query)}</p>
      <p class="small">实时票价和酒店信息需要一点时间。页面保持打开即可，完成后会自动换成结果卡片。</p>
      <ol class="progress-steps" aria-label="查询进度">
        <li data-step="queue" class="is-active"><span></span>排队</li>
        <li data-step="search"><span></span>实时查询</li>
        <li data-step="xhs"><span></span>社区灵感</li>
        <li data-step="shape"><span></span>整理卡片</li>
      </ol>
      <div class="progress-hints" aria-label="等待提示">
        <span>优先展示价格、班次号和时间</span>
        <span>往返尽量拆成去程和返程</span>
        <span>启用后会补充小红书高互动笔记</span>
      </div>
      <details class="stream-details">
        <summary>查看执行明细</summary>
        <pre class="stream-log" aria-live="polite"></pre>
      </details>
    </div>
  `;
  activeStreamLog = card.querySelector(".stream-log");
  results.appendChild(card);
}

function setQueryBusy(value, busyText = "发送中") {
  queryButton.disabled = value;
  queryButton.textContent = value ? busyText : "发送";
  clearQueryButton.disabled = value;
  cancelQueryButton.classList.toggle("is-hidden", !value);
}

async function responseErrorText(response) {
  const text = await response.text();
  try {
    const data = JSON.parse(text);
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
  } catch {
    // Use the raw text below.
  }
  return text || response.statusText || "请求失败";
}

function appendStreamLog(text) {
  if (!activeStreamLog || !text) return;
  activeStreamText += text;
  if (activeStreamText.length > 8000) {
    activeStreamText = `...\n${activeStreamText.slice(-8000)}`;
  }
  activeStreamLog.textContent = activeStreamText.trimStart();
  activeStreamLog.scrollTop = activeStreamLog.scrollHeight;
}

function renderBlocks(blocks) {
  results.innerHTML = "";
  if (!blocks.length) {
    blocks = [{ type: "notice", title: "没有结果", items: ["没有可展示的内容。"] }];
  }
  for (const block of blocks) {
    results.appendChild(renderBlock(block));
  }
}

function appendResultGroup(blocks, status = "success", durationMs = 0, createdAt = "") {
  if (results.querySelector(".empty-state")) results.innerHTML = "";
  const section = document.createElement("section");
  section.className = "result-group";
  section.innerHTML = `
    <div class="result-group-head">
      <strong>${status === "success" ? "查询结果" : "查询异常"}</strong>
      <span>${[durationText(durationMs), formatDate(createdAt)].filter(Boolean).join(" · ")}</span>
    </div>
  `;
  results.appendChild(section);
  if (!blocks.length) {
    blocks = [{ type: "notice", title: "没有结果", items: ["没有可展示的内容。"] }];
  }
  for (const block of blocks) {
    section.appendChild(renderBlock(block));
  }
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

function appendBlocks(blocks) {
  if (!blocks.length) return;
  if (results.querySelector(".empty-state")) {
    results.innerHTML = "";
  }
  for (const block of blocks) {
    results.appendChild(renderBlock(block));
  }
}

function renderEmptyState() {
  if (results.children.length) return;
  results.innerHTML = `
    <article class="empty-state">
      <div class="empty-map" aria-hidden="true"><span></span><span></span><span></span></div>
      <div class="empty-content">
        <p class="section-label">准备查询</p>
        <h2>先从一个真实问题开始</h2>
        <p>把城市、日期、人数、预算和偏好放进一句话里。往返机票请同时写清去程和返程，结果会更完整。</p>
        <div class="empty-actions" aria-label="示例查询">
          <button type="button" data-template-index="0">查往返机票</button>
          <button type="button" data-template-index="1">找假期目的地</button>
          <button type="button" data-template-index="2">筛酒店</button>
        </div>
      </div>
    </article>
  `;
}

function setProgressStep(step) {
  const order = ["queue", "search", "xhs", "shape"];
  const activeIndex = Math.max(0, order.indexOf(step));
  document.querySelectorAll(".progress-steps li").forEach((item) => {
    const index = order.indexOf(item.dataset.step);
    item.classList.toggle("is-active", index === activeIndex);
    item.classList.toggle("is-complete", index >= 0 && index < activeIndex);
  });
}

function renderBlock(block) {
  switch (block.type) {
    case "flight_card":
    case "train_card":
      return transportCard(block);
    case "hotel_card":
      return placeCard(block, "酒店");
    case "poi_card":
      return placeCard(block, "景点");
    case "destination_card":
      return placeCard(block, "目的地");
    case "comparison_table":
      return tableCard(block);
    case "booking_link":
      return bookingCard(block);
    case "xhs_post_card":
      return xhsPostCard(block);
    case "guide_section":
      return guideCard(block);
    case "notice":
    default:
      return noticeCard(block);
  }
}

function baseCard(block, extraClass = "") {
  const card = document.createElement("article");
  const typeClass = block.type ? block.type.replaceAll("_", "-") : "";
  card.className = `card ${typeClass} ${extraClass}`.trim();
  if (block.imageUrl) {
    const image = document.createElement("img");
    image.className = "card-image";
    image.src = block.imageUrl;
    image.alt = block.title || "旅行图片";
    image.loading = "lazy";
    card.appendChild(image);
  }
  const body = document.createElement("div");
  body.className = "card-body";
  card.appendChild(body);
  return { card, body };
}

function titleRow(block, fallback) {
  return `
    <div class="card-title">
      <h3>${escapeHtml(block.title || block.name || fallback)}</h3>
      ${block.price ? `<div class="price">${escapeHtml(block.price)}</div>` : ""}
    </div>
    ${block.subtitle ? `<p class="small">${escapeHtml(block.subtitle)}</p>` : ""}
  `;
}

function transportCard(block) {
  const { card, body } = baseCard(block);
  const segments = (block.segments || []).filter((segment) => segment && typeof segment === "object");
  const firstSegment = segments[0] || {};
  if (segments.length > 1) card.classList.add("multi-segment");
  body.innerHTML = titleRow(block, block.type === "flight_card" ? "航班方案" : "火车方案");
  const meta = compactTags([
    block.duration || firstSegment.duration,
    block.carrier || firstSegment.carrier,
    block.number || firstSegment.number,
    block.seat || firstSegment.seat,
  ]);
  if (meta) body.insertAdjacentHTML("beforeend", meta);
  const overview = routeOverviewHtml(block, segments);
  if (overview) body.insertAdjacentHTML("beforeend", overview);

  const timeline = document.createElement("div");
  timeline.className = "timeline";
  for (const segment of segments) {
    const depMain = segment.depCity || segment.depStation || "";
    const depSub = segment.depCity ? segment.depStation : "";
    const arrMain = segment.arrCity || segment.arrStation || "";
    const arrSub = segment.arrCity ? segment.arrStation : "";
    const transportNo = compactText([segment.carrier, segment.number, segment.seat]);
    timeline.insertAdjacentHTML(
      "beforeend",
      `
      <div class="segment">
        <div class="station">
          <strong>${escapeHtml(depMain)}</strong>
          ${depSub ? `<span class="small">${escapeHtml(depSub)}</span>` : ""}
          <span>${escapeHtml(segment.depTime || "")}</span>
        </div>
        <div class="route-meta">
          ${segment.label ? `<span class="segment-label">${escapeHtml(segment.label)}</span>` : ""}
          <div class="route-line"></div>
          <span class="small">${escapeHtml(segment.duration || "")}</span>
          ${transportNo ? `<span class="small transport-no">${escapeHtml(transportNo)}</span>` : ""}
          ${segment.price ? `<span class="segment-price">${escapeHtml(segment.price)}</span>` : ""}
        </div>
        <div class="station">
          <strong>${escapeHtml(arrMain)}</strong>
          ${arrSub ? `<span class="small">${escapeHtml(arrSub)}</span>` : ""}
          <span>${escapeHtml(segment.arrTime || "")}</span>
        </div>
      </div>
      `
    );
  }
  if (!segments.length) {
    timeline.innerHTML = `<p class="small">本次结果没有返回可结构化展示的班次明细。</p>`;
  }
  body.appendChild(timeline);
  appendItems(body, block.items);
  appendBooking(body, block.bookingUrl);
  return card;
}

function placeCard(block, fallback) {
  const { card, body } = baseCard(block);
  const meta = block.meta || {};
  body.innerHTML = titleRow(block, fallback);
  body.insertAdjacentHTML(
    "beforeend",
    compactTags([
      block.address || meta.address,
      block.scoreDesc || meta.scoreDesc || block.score || meta.score,
      block.star || meta.star,
      block.ticketName || meta.ticketName,
    ])
  );
  appendItems(body, block.items);
  appendBooking(body, block.bookingUrl);
  return card;
}

function guideCard(block) {
  const { card, body } = baseCard(block);
  body.innerHTML = titleRow(block, "旅行攻略");
  appendItems(body, block.items);
  if (!block.items?.length && block.markdown) {
    const pre = document.createElement("p");
    pre.textContent = block.markdown;
    body.appendChild(pre);
  }
  appendBooking(body, block.bookingUrl);
  return card;
}

function tableCard(block) {
  const { card, body } = baseCard(block);
  body.innerHTML = titleRow(block, "方案对比");
  const columns = block.columns || [];
  const rows = block.rows || [];
  const table = document.createElement("table");
  table.className = "comparison";
  table.innerHTML = `
    <thead><tr>${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join("")}</tr></thead>
    <tbody>
      ${rows
        .map((row) => `<tr>${columns.map((col, index) => `<td>${escapeHtml(tableCell(row, col, index))}</td>`).join("")}</tr>`)
        .join("")}
    </tbody>
  `;
  body.appendChild(table);
  return card;
}

function bookingCard(block) {
  const { card, body } = baseCard(block);
  body.innerHTML = titleRow(block, "预订链接");
  appendBooking(body, block.bookingUrl);
  return card;
}

function xhsPostCard(block) {
  const { card, body } = baseCard(block, "xhs-post");
  body.innerHTML = titleRow(block, "小红书笔记");
  const stats = compactTags([
    block.source || "小红书",
    block.author ? `作者 ${block.author}` : "",
    statText("赞", block.likedCount),
    statText("藏", block.collectedCount),
    statText("评", block.commentCount),
  ]);
  if (stats) body.insertAdjacentHTML("beforeend", stats);
  if (block.summary) {
    const summary = document.createElement("p");
    summary.className = "xhs-summary";
    summary.textContent = block.summary;
    body.appendChild(summary);
  }
  appendBooking(body, block.postUrl || block.bookingUrl, "打开小红书笔记");
  return card;
}

function noticeCard(block) {
  const severityClass = block.severity === "error" ? "error" : block.severity === "warning" ? "warning" : "";
  const { card, body } = baseCard(block, `notice ${severityClass}`);
  body.innerHTML = titleRow(block, "提示");
  appendItems(body, block.items);
  return card;
}

function appendItems(container, items = []) {
  if (!items.length) return;
  const list = document.createElement("ul");
  list.className = "guide-list";
  for (const item of items) {
    const text = cleanGuideItem(item);
    if (!text) continue;
    const li = document.createElement("li");
    li.textContent = text;
    list.appendChild(li);
  }
  if (list.children.length) container.appendChild(list);
}

function appendBooking(container, url, label = "打开预订链接") {
  if (!url) return;
  const link = document.createElement("a");
  link.className = "booking";
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = label;
  container.appendChild(link);
}

function renderPromptChips() {
  promptChips.innerHTML = "";
  promptTemplates.forEach((template, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "prompt-chip";
    button.innerHTML = `<strong>${escapeHtml(template.label)}</strong><span>${escapeHtml(template.hint)}</span>`;
    button.title = template.query;
    button.addEventListener("click", () => fillPromptTemplate(index));
    promptChips.appendChild(button);
  });
}

function fillPromptTemplate(index) {
  const template = promptTemplates[index] || promptTemplates[0];
  queryInput.value = template.query;
  updateQueryCount();
  queryInput.focus();
  queryInput.scrollIntoView({ behavior: "smooth", block: "center" });
}

function updateQueryCount() {
  const length = queryInput.value.length;
  queryCount.textContent = `${length}/2000`;
  queryCount.classList.toggle("warn", length > 500 && currentUser?.role !== "owner");
}

function compactTags(values) {
  const tags = values.filter(Boolean);
  if (!tags.length) return "";
  return `<div class="meta-grid">${tags.map((value) => `<span class="tag">${escapeHtml(value)}</span>`).join("")}</div>`;
}

function compactText(values) {
  return values.filter(Boolean).join(" · ");
}

function statText(label, value) {
  const number = Number(value || 0);
  if (!number) return "";
  return `${label} ${formatCount(number)}`;
}

function formatCount(value) {
  if (value >= 10000) return `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}万`;
  return String(value);
}

function routeOverviewHtml(block, segments) {
  if (!segments.length) return "";
  const first = segments[0];
  const last = segments[segments.length - 1];
  const origin = endpointLabel(first, "dep");
  const firstDestination = endpointLabel(first, "arr");
  const finalDestination = endpointLabel(last, "arr");
  const routeText = origin && firstDestination && segments.length > 1 && sameText(origin, finalDestination)
    ? `${origin} 往返 ${firstDestination}`
    : compactText([origin, finalDestination]).replace(" · ", " 到 ");
  if (!routeText) return "";
  const facts = [
    segments.length > 1 ? `${segments.length} 段` : "单程",
    block.price ? `总价 ${block.price}` : "",
    block.duration || "",
  ].filter(Boolean);
  return `
    <div class="route-overview">
      <strong>${escapeHtml(routeText)}</strong>
      ${facts.length ? `<span>${facts.map(escapeHtml).join(" · ")}</span>` : ""}
    </div>
  `;
}

function endpointLabel(segment, side) {
  const city = segment[`${side}City`];
  const station = segment[`${side}Station`];
  return city || station || "";
}

function sameText(left, right) {
  if (!left || !right) return false;
  return left === right || left.includes(right) || right.includes(left);
}

function cleanGuideItem(item) {
  let text = typeof item === "string" ? item : JSON.stringify(item);
  text = text.trim().replace(/^[-*]\s*/, "");
  if (!text) return "";
  if (/^```/.test(text)) return "";
  if (/^\|?\s*:?-{2,}/.test(text)) return "";
  if (/^\|?\s*(航段|日期|航班号|航班|出发|到达|价格|票价|时长)\s*\|/.test(text)) return "";
  if (/^(flightcard|hotelcard|traincard|poicard|destinationcard)$/i.test(text.replace(/\s+/g, ""))) return "";
  if (/^(type|title|price|number|segments|items)\s*[:：=]/i.test(text)) return "";
  if (/^当前为体验模式/.test(text)) return "";
  return text.replace(/<[^>]+>/g, "").trim();
}

function tableCell(row, column, index) {
  if (Array.isArray(row)) return row[index] ?? "";
  return row?.[column] ?? "";
}

function formatDate(value) {
  if (!value) return "";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function durationText(value) {
  const ms = Number(value || 0);
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m${rest ? `${rest}s` : ""}`;
}

function statusText(value) {
  return value === "success" ? "完成" : "异常";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot();
