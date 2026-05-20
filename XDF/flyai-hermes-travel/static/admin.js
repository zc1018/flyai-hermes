const adminStatus = document.querySelector("#adminStatus");
const runtimeStats = document.querySelector("#runtimeStats");
const userList = document.querySelector("#userList");
const createUserForm = document.querySelector("#createUserForm");
const adminLogoutButton = document.querySelector("#adminLogoutButton");
const accessNotice = document.querySelector("#accessNotice");
const accessNoticeText = document.querySelector("#accessNoticeText");
const adminOnlySections = document.querySelectorAll(".admin-only");
const xhsConfigForm = document.querySelector("#xhsConfigForm");
const xhsCookieState = document.querySelector("#xhsCookieState");
const clearXhsCookieButton = document.querySelector("#clearXhsCookieButton");

async function request(path, options = {}) {
  const response = await fetch(path.replace(/^\//, ""), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) throw new Error(await responseErrorText(response));
  return response.json();
}

async function bootAdmin() {
  try {
    const me = await request("/api/me");
    if (me.role !== "owner") {
      showAccessNotice(`当前登录的是「${me.label || "普通用户"}」，没有后台权限。退出后可输入管理员口令。`);
      return;
    }
    await loadAdmin();
  } catch (error) {
    showAccessNotice(error.message || "请先返回查询页登录。");
  }
}

adminLogoutButton.addEventListener("click", logout);

async function logout() {
  await request("/api/logout", { method: "POST" }).catch(() => {});
  window.location.href = "./";
}

function showAccessNotice(message) {
  adminOnlySections.forEach((section) => section.classList.add("is-hidden"));
  accessNotice.classList.remove("is-hidden");
  accessNoticeText.textContent = message;
  adminStatus.textContent = "无后台权限";
  adminStatus.classList.add("warn");
  adminStatus.classList.remove("ok");
}

async function loadAdmin() {
  const [usage, xhsConfig] = await Promise.all([request("/api/admin/usage"), request("/api/admin/xhs-config")]);
  const runtime = usage.runtime || {};
  const xhs = usage.xhs || {};
  const xhsCache = xhs.cache || {};
  runtimeStats.innerHTML = [
    tag(`全站并发 ${runtime.active || 0}/${runtime.global_concurrency || 0}`),
    tag(`排队 ${runtime.queued || 0}`),
    tag(`小红书 ${xhs.enabled ? "已开启" : "未开启"}`),
    tag(`小红书运行 ${xhs.running || 0}`),
    tag(`小红书排队 ${xhs.queued || 0}`),
    tag(`今日小红书 ${xhs.today_calls || 0}`),
    tag(`缓存 ${xhsCache.entries || 0}`),
  ].join("");
  renderXhsConfig(xhsConfig);
  renderUsers(usage.users || []);
  adminStatus.textContent = "后台可用";
  adminStatus.classList.add("ok");
  adminStatus.classList.remove("warn");
  accessNotice.classList.add("is-hidden");
  adminOnlySections.forEach((section) => section.classList.remove("is-hidden"));
}

function renderXhsConfig(config) {
  if (!xhsConfigForm) return;
  xhsConfigForm.enabled.checked = Boolean(config.enabled);
  xhsConfigForm.timeout_seconds.value = config.timeout_seconds || 45;
  xhsConfigForm.max_results.value = config.max_results || 6;
  xhsConfigForm.max_daily_per_user.value = config.max_daily_per_user ?? 10;
  xhsConfigForm.cookies.value = "";
  const readyText = config.mediacrawler_ready ? "MediaCrawler 已就绪" : "MediaCrawler 未就绪";
  let cookieText = config.cookie_configured ? "cookie 已配置" : "cookie 未配置";
  if (config.cookie_configured && config.required_cookie_ok === false) {
    cookieText = `cookie 缺少 ${config.required_cookie_name || "必需字段"}`;
  }
  xhsCookieState.textContent = `${readyText} · ${cookieText} · ${config.enabled ? "已开启" : "未开启"}`;
}

function renderUsers(users) {
  userList.innerHTML = "";
  for (const user of users) {
    const row = document.createElement("article");
    row.className = "user-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(user.label)}</strong>
        <span class="small">${escapeHtml(user.role)} · 今日 ${user.used_today}/${user.daily_limit < 0 ? "不限" : user.daily_limit} · ${user.enabled ? "启用" : "停用"}</span>
        <span class="small">最近登录 ${formatDate(user.last_login_at)} · 最近查询 ${formatDate(user.last_query_at)}</span>
      </div>
      <div class="user-actions">
        ${
          user.role === "owner"
            ? ""
            : `
          <input data-field="daily_limit" data-id="${user.id}" type="number" min="0" max="1000" value="${user.daily_limit}" title="每日次数" />
          <input data-field="max_concurrent" data-id="${user.id}" type="number" min="1" max="10" value="${user.max_concurrent}" title="单用户并发" />
          <input data-field="timeout_seconds" data-id="${user.id}" type="number" min="30" max="900" value="${user.timeout_seconds}" title="超时秒数" />
          <label class="check-row compact"><input data-field="can_view_history" data-id="${user.id}" type="checkbox" ${user.can_view_history ? "checked" : ""} /> 历史</label>
          <button data-action="save" data-id="${user.id}">保存</button>
          <button data-action="toggle" data-id="${user.id}" data-enabled="${user.enabled ? "0" : "1"}">${user.enabled ? "停用" : "启用"}</button>
          <button data-action="reset" data-id="${user.id}">重置口令</button>
        `
        }
      </div>
    `;
    userList.appendChild(row);
  }
}

createUserForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(createUserForm);
  try {
    await request("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        label: form.get("label"),
        password: form.get("password"),
        daily_limit: Number(form.get("daily_limit") || 10),
        max_concurrent: 1,
        timeout_seconds: Number(form.get("timeout_seconds") || 300),
        can_view_history: Boolean(form.get("can_view_history")),
        enabled: true,
      }),
    });
    createUserForm.reset();
    createUserForm.daily_limit.value = "10";
    createUserForm.timeout_seconds.value = "300";
    createUserForm.can_view_history.checked = true;
    await loadAdmin();
  } catch (error) {
    adminStatus.textContent = error.message;
    adminStatus.classList.add("warn");
  }
});

xhsConfigForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(xhsConfigForm);
  try {
    const config = await request("/api/admin/xhs-config", {
      method: "POST",
      body: JSON.stringify({
        enabled: Boolean(form.get("enabled")),
        cookies: String(form.get("cookies") || "").trim() || null,
        timeout_seconds: Number(form.get("timeout_seconds") || 45),
        max_results: Number(form.get("max_results") || 6),
        max_daily_per_user: Number(form.get("max_daily_per_user") || 10),
      }),
    });
    renderXhsConfig(config);
    await loadAdmin();
  } catch (error) {
    adminStatus.textContent = error.message;
    adminStatus.classList.add("warn");
  }
});

clearXhsCookieButton?.addEventListener("click", async () => {
  if (!confirm("确定清除小红书 cookie 并关闭小红书补充吗？")) return;
  try {
    const config = await request("/api/admin/xhs-config", {
      method: "POST",
      body: JSON.stringify({
        enabled: false,
        clear_cookies: true,
        timeout_seconds: Number(xhsConfigForm.timeout_seconds.value || 45),
        max_results: Number(xhsConfigForm.max_results.value || 6),
        max_daily_per_user: Number(xhsConfigForm.max_daily_per_user.value || 10),
      }),
    });
    renderXhsConfig(config);
    await loadAdmin();
  } catch (error) {
    adminStatus.textContent = error.message;
    adminStatus.classList.add("warn");
  }
});

userList.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const userId = button.dataset.id;
  try {
    if (button.dataset.action === "toggle") {
      await request(`/api/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: button.dataset.enabled === "1" }),
      });
    }
    if (button.dataset.action === "save") {
      const row = button.closest(".user-row");
      await request(`/api/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({
          daily_limit: Number(row.querySelector('[data-field="daily_limit"]').value || 10),
          max_concurrent: Number(row.querySelector('[data-field="max_concurrent"]').value || 1),
          timeout_seconds: Number(row.querySelector('[data-field="timeout_seconds"]').value || 300),
          can_view_history: row.querySelector('[data-field="can_view_history"]').checked,
        }),
      });
    }
    if (button.dataset.action === "reset") {
      const password = prompt("输入新的访问口令（至少 6 位）");
      if (!password) return;
      await request(`/api/admin/users/${userId}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password }),
      });
    }
    await loadAdmin();
  } catch (error) {
    adminStatus.textContent = error.message;
    adminStatus.classList.add("warn");
  }
});

function tag(text) {
  return `<span class="tag">${escapeHtml(text)}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "暂无";
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

async function responseErrorText(response) {
  const text = await response.text();
  try {
    const data = JSON.parse(text);
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
  } catch {
    // Use raw text.
  }
  return text || response.statusText || "请求失败";
}

bootAdmin();
