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
const refreshHistory = document.querySelector("#refreshHistory");
const healthStatus = document.querySelector("#healthStatus");
let activeStreamLog = null;
let activeStreamText = "";

async function request(path, options = {}) {
  const response = await fetch(path.replace(/^\//, ""), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

function setAuthenticated(value) {
  loginPanel.classList.toggle("is-hidden", value);
  workspace.classList.toggle("is-hidden", !value);
}

async function boot() {
  await loadHealth();
  try {
    await request("/api/me");
    setAuthenticated(true);
    await loadHistory();
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
        health.hermes_bin?.ok ? "" : "Hermes 不可用",
        health.flyai_cli?.ok ? "" : "flyai CLI 不可用",
        health.database?.ok ? "" : "SQLite 不可用",
        health.app_password_configured ? "" : "APP_PASSWORD 未配置",
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
    setAuthenticated(true);
    await loadHistory();
  } catch {
    passwordInput.focus();
    passwordInput.value = "";
    passwordInput.placeholder = "口令不正确";
  }
});

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;

  queryButton.disabled = true;
  queryButton.textContent = "查询中";
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = "Hermes 正在调用 flyai skill...";
  renderStreamShell();

  try {
    await streamQuery(query);
  } catch (error) {
    renderBlocks([
      {
        type: "notice",
        severity: "error",
        title: "请求失败",
        items: ["服务端没有返回可用结果。", error.message],
      },
    ]);
  } finally {
    queryButton.disabled = false;
    queryButton.textContent = "查询";
  }
});

refreshHistory.addEventListener("click", loadHistory);

async function loadHistory() {
  const items = await request("/api/history");
  historyList.innerHTML = "";
  if (!items.length) {
    historyList.innerHTML = `<p class="small">暂无查询历史</p>`;
    return;
  }

  for (const item of items) {
    const button = document.createElement("button");
    button.className = "history-item";
    button.innerHTML = `
      <strong>${escapeHtml(item.query)}</strong>
      <span>${formatDate(item.created_at)} · ${item.status} · ${item.duration_ms}ms</span>
    `;
    button.addEventListener("click", () => {
      queryInput.value = item.query;
      renderResult({ ...item, raw_output: "" });
    });
    historyList.appendChild(button);
  }
}

function renderResult(data) {
  resultMeta.classList.add("is-visible");
  resultMeta.textContent = `${data.status === "success" ? "查询完成" : "查询异常"} · ${data.duration_ms}ms · ${formatDate(data.created_at)}`;
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
    resultMeta.textContent = payload.kind === "heartbeat" ? `Hermes 仍在运行 · ${seconds}s` : `Hermes 实时执行中 · ${seconds}s`;
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
        <h3>Hermes 实时反馈</h3>
      </div>
      <pre class="stream-log" aria-live="polite"></pre>
    </div>
  `;
  activeStreamLog = card.querySelector(".stream-log");
  results.appendChild(card);
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot();
