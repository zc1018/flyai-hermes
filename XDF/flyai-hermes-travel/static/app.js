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
let activeStreamLog = null;
let activeStreamText = "";
let currentUser = null;
let historyItems = [];

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
  const query = queryInput.value.trim();
  if (!query) return;

  queryButton.disabled = true;
  queryButton.textContent = "查询中";
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = "正在理解你的旅行需求...";
  renderStreamShell();

  try {
    await streamQuery(query);
    await loadMe();
  } catch (error) {
    renderBlocks([
      {
        type: "notice",
        severity: "error",
        title: "请求失败",
        items: [error.message || "服务端没有返回可用结果。"],
      },
    ]);
  } finally {
    queryButton.disabled = false;
    queryButton.textContent = "查询";
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

async function logout() {
  await request("/api/logout", { method: "POST" }).catch(() => {});
  currentUser = null;
  activeStreamLog = null;
  activeStreamText = "";
  queryInput.value = "";
  historyList.innerHTML = "";
  results.innerHTML = "";
  resultMeta.classList.remove("is-visible");
  setAuthenticated(false);
  updateQueryCount();
  passwordInput.focus();
}

async function loadHistory() {
  historyItems = await request("/api/history");
  renderHistory(historyItems);
}

function renderHistory(items) {
  historyList.innerHTML = "";
  const keyword = historySearch.value.trim().toLowerCase();
  const visible = keyword
    ? items.filter((item) => item.query.toLowerCase().includes(keyword))
    : items;
  if (!visible.length) {
    historyList.innerHTML = `<p class="small">${items.length ? "没有匹配的历史" : "暂无查询历史"}</p>`;
    return;
  }

  for (const item of visible) {
    const button = document.createElement("button");
    button.className = "history-item";
    button.innerHTML = `
      <strong>${escapeHtml(item.query)}</strong>
      <span>${formatDate(item.created_at)} · ${statusText(item.status)} · ${durationText(item.duration_ms)}</span>
    `;
    button.addEventListener("click", () => {
      queryInput.value = item.query;
      renderResult({ ...item, raw_output: "" });
    });
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

function renderResult(data) {
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = `${data.status === "success" ? "查询完成" : "查询异常"} · ${durationText(data.duration_ms)} · ${formatDate(data.created_at)}`;
  activeStreamLog = null;
  activeStreamText = "";
  renderBlocks(data.blocks || []);
}

async function streamQuery(query) {
  const response = await fetch("api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应。");
  }

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
        completed = handleStreamMessage(message) || completed;
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

function handleStreamMessage(message) {
  if (message.event === "progress") {
    const payload = message.data || {};
    const seconds = Math.max(0, Math.round((payload.elapsed_ms || 0) / 1000));
    if (payload.kind === "queued") {
      resultMeta.textContent = "正在排队，马上开始查询...";
      setProgressStep("queue");
    } else if (payload.kind === "heartbeat") {
      resultMeta.textContent = `正在等待实时结果 · ${seconds}s`;
      setProgressStep("search");
    } else {
      resultMeta.textContent = `正在查询实时旅行信息 · ${seconds}s`;
      setProgressStep("search");
    }
    if (payload.kind !== "heartbeat") appendStreamLog(payload.message || "");
    return false;
  }

  if (message.event === "result") {
    renderResult(message.data);
    loadHistory().catch(() => {});
    return true;
  }
  return false;
}

function renderStreamShell() {
  activeStreamText = "";
  results.innerHTML = "";
  const card = document.createElement("article");
  card.className = "card stream-card";
  card.innerHTML = `
    <div class="card-body">
      <div class="card-title">
        <h3>正在整理你的旅行方案</h3>
      </div>
      <p class="small">实时票价和酒店信息需要一点时间。页面保持打开即可，完成后会自动换成结果卡片。</p>
      <ol class="progress-steps" aria-label="查询进度">
        <li data-step="queue" class="is-active"><span></span>排队</li>
        <li data-step="search"><span></span>实时查询</li>
        <li data-step="shape"><span></span>整理卡片</li>
      </ol>
      <details class="stream-details">
        <summary>查看执行明细</summary>
        <pre class="stream-log" aria-live="polite"></pre>
      </details>
    </div>
  `;
  activeStreamLog = card.querySelector(".stream-log");
  results.appendChild(card);
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

function renderEmptyState() {
  if (results.children.length) return;
  results.innerHTML = `
    <article class="empty-state">
      <div class="empty-map" aria-hidden="true"><span></span><span></span><span></span></div>
      <div>
        <p class="section-label">准备查询</p>
        <h2>先从一个真实问题开始</h2>
        <p>比如最低价往返机票、某个区域的酒店、适合假期的目的地，或者每天怎么安排。往返机票请把去程和返程条件都写清楚。</p>
        <div class="guide-steps" aria-label="查询建议">
          <span>1. 说清目的地</span>
          <span>2. 写明时间</span>
          <span>3. 加上偏好</span>
        </div>
      </div>
    </article>
  `;
}

function setProgressStep(step) {
  const order = ["queue", "search", "shape"];
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
  const firstSegment = (block.segments || [])[0] || {};
  body.innerHTML = titleRow(block, block.type === "flight_card" ? "航班方案" : "火车方案");
  const meta = compactTags([
    block.duration || firstSegment.duration,
    block.carrier || firstSegment.carrier,
    block.number || firstSegment.number,
    block.seat || firstSegment.seat,
  ]);
  if (meta) body.insertAdjacentHTML("beforeend", meta);

  const timeline = document.createElement("div");
  timeline.className = "timeline";
  for (const segment of block.segments || []) {
    const depMain = segment.depCity || segment.depStation || "";
    const depSub = segment.depCity ? segment.depStation : "";
    const arrMain = segment.arrCity || segment.arrStation || "";
    const arrSub = segment.arrCity ? segment.arrStation : "";
    const transportNo = compactText([segment.carrier, segment.number, segment.seat, segment.price]);
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
  body.appendChild(timeline);
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

function noticeCard(block) {
  const { card, body } = baseCard(block, `notice ${block.severity === "error" ? "error" : ""}`);
  body.innerHTML = titleRow(block, "提示");
  appendItems(body, block.items);
  return card;
}

function appendItems(container, items = []) {
  if (!items.length) return;
  const list = document.createElement("ul");
  list.className = "guide-list";
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : JSON.stringify(item);
    list.appendChild(li);
  }
  container.appendChild(list);
}

function appendBooking(container, url) {
  if (!url) return;
  const link = document.createElement("a");
  link.className = "booking";
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = "打开预订链接";
  container.appendChild(link);
}

function renderPromptChips() {
  promptChips.innerHTML = "";
  promptTemplates.forEach((template) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "prompt-chip";
    button.innerHTML = `<strong>${escapeHtml(template.label)}</strong><span>${escapeHtml(template.hint)}</span>`;
    button.title = template.query;
    button.addEventListener("click", () => {
      queryInput.value = template.query;
      updateQueryCount();
      queryInput.focus();
    });
    promptChips.appendChild(button);
  });
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
