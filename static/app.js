/* AI 招生助手前端：原生 JS，调用 /api/*，按角色渲染导航与视图。 */

const $ = (id) => document.getElementById(id);

const CATEGORY_LABELS = {
  policy: "招生政策",
  major: "专业相关",
  score: "分数与位次",
  other: "其他",
};

const STATUS_LABELS = {
  pending: "待处理",
  answered: "已解答",
  closed: "已关闭",
};

const NAV = {
  user: [
    ["chat", "智能咨询"],
    ["majors", "专业查询"],
    ["plan-diff", "计划变动"],
    ["questions", "我的提问"],
    ["announcements", "公告"],
    ["profile", "个人资料"],
  ],
  admin: [
    ["admin-stats", "数据概览"],
    ["admin-questions", "提问受理"],
    ["admin-announcements", "公告管理"],
    ["announcements", "公告预览"],
    ["majors", "专业查询"],
    ["plan-diff", "计划变动"],
  ],
};

const state = {
  token: localStorage.getItem("token") || "",
  username: localStorage.getItem("username") || "",
  role: localStorage.getItem("role") || "user",
  conversationId: null,
  conversations: [],
  authMode: "login",
  questionStatus: "",
  editingAnnouncementId: null,
  chatPending: false,
  profile: null,
};

/* ---------- 基础工具 ---------- */

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);

const fmtTime = (value) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
};

let toastTimer = null;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), 2600);
}

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }

  if (!response.ok || (payload && payload.code !== 0)) {
    if (response.status === 401) logout();
    throw new Error((payload && payload.message) || `请求失败（${response.status}）`);
  }
  return payload ? payload.data : null;
}

/* ---------- 登录 / 注册 ---------- */

function setAuthMode(mode) {
  state.authMode = mode;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  $("auth-submit").textContent = mode === "login" ? "登录" : "注册";
  $("auth-error").textContent = "";
}

async function handleAuth(event) {
  event.preventDefault();
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  const errorBox = $("auth-error");
  errorBox.textContent = "";

  if (username.length < 3 || username.length > 50) {
    errorBox.textContent = "用户名需为 3~50 位";
    return;
  }
  if (password.length < 6 || password.length > 64) {
    errorBox.textContent = "密码需为 6~64 位";
    return;
  }

  const submit = $("auth-submit");
  submit.disabled = true;
  try {
    if (state.authMode === "register") {
      await api("/api/auth/register", { method: "POST", body: { username, password } });
    }
    const data = await api("/api/auth/login", { method: "POST", body: { username, password } });
    state.token = data.access_token;
    state.username = data.username;
    state.role = data.role || "user";
    localStorage.setItem("token", state.token);
    localStorage.setItem("username", state.username);
    localStorage.setItem("role", state.role);
    // 快捷问题、专业查询默认值都要用个人资料
    state.profile = await api("/api/profile").catch(() => null);
    enterApp();
  } catch (error) {
    errorBox.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  localStorage.removeItem("role");
  state.token = "";
  state.username = "";
  state.role = "user";
  state.conversationId = null;
  // 清掉上一位用户的画像，避免换账号后快捷问题仍用旧分数
  state.profile = null;
  $("conversation-list").innerHTML = "";
  resetChatBox();
  $("chat-meta").textContent = "";
  $("app").classList.add("hidden");
  $("auth").classList.remove("hidden");
  $("auth-password").value = "";
}

/* ---------- 应用骨架 ---------- */

function enterApp() {
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("who").textContent = state.username;
  const isAdmin = state.role === "admin";
  $("role-badge").textContent = isAdmin ? "管理员" : "普通用户";

  const nav = $("nav");
  nav.innerHTML = "";
  (NAV[isAdmin ? "admin" : "user"]).forEach(([key, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-item";
    button.dataset.view = key;
    button.textContent = label;
    button.addEventListener("click", () => switchView(key));
    nav.appendChild(button);
  });

  switchView(isAdmin ? "admin-stats" : "chat");
  showLoginAnnouncements();
  // 资料加载完成后，快捷问题的省份 / 科类要跟着变
  if ($("chat-empty")) resetChatBox();
}

function switchView(key) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === key);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("hidden", view.id !== `view-${key}`);
  });

  const loaders = {
    chat: () => {
      $("chat-input").focus();
      return loadConversations();
    },
    majors: () => loadMajors(),
    "plan-diff": () => loadPlanDiff(),
    questions: loadMyQuestions,
    announcements: loadAnnouncements,
    profile: loadProfile,
    "admin-stats": () => Promise.all([loadStats(), loadLlmSettings()]),
    "admin-questions": loadAdminQuestions,
    "admin-announcements": loadAdminAnnouncements,
  };
  const loader = loaders[key];
  if (loader) {
    Promise.resolve(loader()).catch((error) => toast(error.message));
  }
}

/* ---------- 智能咨询 ---------- */

// 模型偶尔会输出 markdown 符号，直接显示会很难看：转成加粗/圆点并去掉多余符号
function formatAnswer(content) {
  return esc(content || "")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^\s*[-*•]\s+/gm, "• ")
    .replace(/^\s*#{1,6}\s*/gm, "")
    .replace(/\n{3,}/g, "\n\n");
}

// 参考资料只保留文件名与标题，去掉路径、扩展名和 #
function formatRef(ref) {
  return String(ref || "")
    .replace(/^.*[\\/]/, "")
    .replace(/\.(md|txt|json)$/i, "")
    .replace(/#/g, " · ");
}

function appendMessage(role, content, refs = [], links = []) {
  const empty = $("chat-empty");
  if (empty) empty.remove();

  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role === "user" ? "user" : "bot"}`;
  if (role !== "user") {
    const avatar = document.createElement("span");
    avatar.className = "msg-avatar";
    avatar.textContent = "东";
    wrapper.appendChild(avatar);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const text = document.createElement("div");
  if (role === "user") {
    text.textContent = content;
  } else {
    text.innerHTML = formatAnswer(content);
  }
  bubble.appendChild(text);

  if (refs.length) {
    const details = document.createElement("details");
    details.className = "msg-refs";
    const summary = document.createElement("summary");
    summary.textContent = `参考来源（${refs.length}）`;
    details.appendChild(summary);
    const refBox = document.createElement("div");
    refBox.className = "refs";
    refs.forEach((ref) => {
      const chip = document.createElement("span");
      chip.className = "ref";
      chip.textContent = formatRef(ref);
      refBox.appendChild(chip);
    });
    details.appendChild(refBox);
    bubble.appendChild(details);
  }

  if (links.length) {
    const linkBox = document.createElement("div");
    linkBox.className = "msg-links";
    links.forEach((link) => {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = `${link.name}官网 ↗`;
      linkBox.appendChild(anchor);
    });
    bubble.appendChild(linkBox);
  }

  if (role !== "user") {
    const tools = document.createElement("div");
    tools.className = "msg-tools";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "复制";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(content);
        toast("已复制");
      } catch (error) {
        toast("复制失败，请手动选择文本");
      }
    });
    tools.appendChild(copy);
    bubble.appendChild(tools);
  }

  wrapper.appendChild(bubble);
  $("chat-box").appendChild(wrapper);
  $("chat-box").scrollTop = $("chat-box").scrollHeight;
  return bubble;
}

function appendTyping() {
  const wrapper = document.createElement("div");
  wrapper.className = "msg bot";
  wrapper.id = "typing";
  const bubble = document.createElement("div");
  bubble.className = "bubble typing";
  bubble.setAttribute("aria-label", "正在生成回答");
  bubble.innerHTML =
    '<span class="typing-dots"><span></span><span></span><span></span></span>' +
    '<span class="typing-text">正在检索招生资料</span>';
  wrapper.appendChild(bubble);
  $("chat-box").appendChild(wrapper);
  $("chat-box").scrollTop = $("chat-box").scrollHeight;
}

async function sendChat(text) {
  const message = (text ?? $("chat-input").value).trim();
  if (!message || state.chatPending) return;

  state.chatPending = true;
  $("chat-send").disabled = true;
  $("chat-send").textContent = "发送中";
  $("chat-input").value = "";
  resizeChatInput();
  appendMessage("user", message);
  appendTyping();

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: { conversation_id: state.conversationId, message },
    });
    state.conversationId = data.conversation_id;
    const typing = $("typing");
    if (typing) typing.remove();
    appendMessage("bot", data.answer, data.refs || [], data.links || []);
    $("chat-meta").textContent = `意图：${data.intent} · 模型：${data.provider} · 回答仅供填报参考`;
    loadConversations();
  } catch (error) {
    const typing = $("typing");
    if (typing) typing.remove();
    appendMessage("bot", `抱歉，服务暂时不可用：${error.message}`);
    $("chat-meta").textContent = "";
  } finally {
    state.chatPending = false;
    $("chat-send").disabled = false;
    $("chat-send").textContent = "发送";
    $("chat-input").focus();
  }
}

/* ---------- 历史对话 ---------- */

function fmtConvTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (num) => String(num).padStart(2, "0");
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  return `${date.getMonth() + 1}-${date.getDate()}`;
}

// 快捷问题里的省份 / 科类优先取个人资料，资料为空时退回通用问法
function profileScope() {
  const profile = state.profile || {};
  const province = profile.province || "";
  const subject = profile.subject_combo || (province ? subjectOptions(province)[0] : "");
  return { province, subject, scope: `${province}${subject}` };
}

function chatEmptyHtml() {
  const { scope } = profileScope();
  const profile = state.profile || {};
  const score = profile.score;
  const rank = profile.rank_no;
  // 省份 / 科类 / 分数 / 位次 / 年份都优先用个人资料，资料缺失才退回通用问法
  const scoreQuestion = score
    ? `我考了${score}分，能报哪些专业？`
    : rank
      ? `我位次${rank}，能报哪些专业？`
      : "我大概能上哪些专业？";
  const planQuestion = scope ? `${scope}招生计划是多少人` : "招生计划是多少人";
  const scoreLineQuestion = scope
    ? `${scope}计算机科学与技术多少分`
    : "计算机科学与技术多少分";
  const year = profile.exam_year || new Date().getFullYear();
  const majorCatalogQuestion = `${year}年有哪些招生专业`;
  return `
    <div class="chat-empty" id="chat-empty">
      <div class="chat-greeting">
        <span class="chat-avatar lg">东</span>
        <h3>你好，我是东师招生助手</h3>
        <p>查分数、看计划、问政策，点一个开始，或直接输入问题</p>
      </div>
      <div class="cap-grid">
        <button class="cap-card" type="button" data-q="${esc(scoreQuestion)}">
          <span class="cap-icon">分</span>
          <span class="cap-title">分数匹配</span>
          <span class="cap-desc">按分数和位次筛专业</span>
        </button>
        <button class="cap-card" type="button" data-q="${esc(planQuestion)}">
          <span class="cap-icon">计</span>
          <span class="cap-title">招生计划</span>
          <span class="cap-desc">分省分专业招生人数</span>
        </button>
        <button class="cap-card" type="button" data-q="各专业学费标准是多少">
          <span class="cap-icon">费</span>
          <span class="cap-title">学费标准</span>
          <span class="cap-desc">收费与公费师范政策</span>
        </button>
        <button class="cap-card" type="button" data-q="${esc(majorCatalogQuestion)}">
          <span class="cap-icon">专</span>
          <span class="cap-title">专业目录</span>
          <span class="cap-desc">今年招哪些专业</span>
        </button>
        <button class="cap-card" type="button" data-q="转专业有什么条件">
          <span class="cap-icon">转</span>
          <span class="cap-title">转专业</span>
          <span class="cap-desc">条件、流程与时间</span>
        </button>
        <button class="cap-card" type="button" data-q="${esc(scoreLineQuestion)}">
          <span class="cap-icon">线</span>
          <span class="cap-title">历年分数</span>
          <span class="cap-desc">最低分与位次参考</span>
        </button>
      </div>
    </div>`;
}

function bindChatChips() {
  document.querySelectorAll(".chat-empty [data-q]").forEach((chip) => {
    chip.addEventListener("click", () => sendChat(chip.dataset.q));
  });
}

function resetChatBox() {
  $("chat-box").innerHTML = chatEmptyHtml();
  bindChatChips();
}

function resizeChatInput() {
  const input = $("chat-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}

function convGroupLabel(value) {
  if (!value) return "更早";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "更早";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "昨天";
  return "更早";
}

function convItemHtml(row) {
  return `
    <div class="conv-item ${row.id === state.conversationId ? "active" : ""}" data-id="${row.id}">
      <button class="conv-main" type="button" data-action="open">
        <span class="conv-title">${esc(row.title || "未命名对话")}</span>
        <span class="conv-summary">${esc(row.last_message || "暂无消息")}</span>
        <span class="conv-time">${esc(fmtConvTime(row.last_active_at || row.created_at))}${
          row.message_count ? ` · ${row.message_count} 条` : ""
        }</span>
      </button>
      <span class="conv-actions">
        <button type="button" data-action="rename" title="重命名" aria-label="重命名对话">✎</button>
        <button type="button" data-action="delete" title="删除" aria-label="删除对话">✕</button>
      </span>
    </div>`;
}

function renderConversationList(rows) {
  const list = $("conversation-list");
  const keyword = ($("conv-search").value || "").trim().toLowerCase();
  const filtered = keyword
    ? rows.filter((row) =>
        `${row.title || ""} ${row.last_message || ""}`.toLowerCase().includes(keyword)
      )
    : rows;
  if (!filtered.length) {
    list.innerHTML = `<p class="conv-empty">${keyword ? "没有匹配的对话" : "还没有历史对话"}</p>`;
    return;
  }
  let html = "";
  let currentGroup = "";
  filtered.forEach((row) => {
    const group = convGroupLabel(row.last_active_at || row.created_at);
    if (group !== currentGroup) {
      currentGroup = group;
      html += `<div class="conv-group">${group}</div>`;
    }
    html += convItemHtml(row);
  });
  list.innerHTML = html;
}

async function loadConversations() {
  try {
    const rows = (await api("/api/conversations")) || [];
    state.conversations = rows;
    renderConversationList(rows);
  } catch (error) {
    $("conversation-list").innerHTML = '<p class="conv-empty">历史对话加载失败</p>';
  }
}

async function openConversation(id) {
  try {
    const rows = (await api(`/api/conversations/${id}/messages`)) || [];
    state.conversationId = id;
    $("chat-meta").textContent = "";
    $("chat-box").innerHTML = "";
    rows.forEach((row) => {
      const refs = row.refs ? String(row.refs).split(",").filter(Boolean) : [];
      appendMessage(row.role, row.content, refs, row.links || []);
    });
    document.querySelectorAll(".conv-item").forEach((item) => {
      item.classList.toggle("active", Number(item.dataset.id) === id);
    });
    setHistoryOpen(false);
    $("chat-input").focus();
  } catch (error) {
    toast(error.message);
  }
}

function newConversation() {
  state.conversationId = null;
  $("chat-meta").textContent = "";
  setHistoryOpen(false);
  resetChatBox();
  document.querySelectorAll(".conv-item").forEach((item) => item.classList.remove("active"));
  $("chat-input").focus();
}

function setHistoryOpen(open) {
  $("chat-history").classList.toggle("open", open);
  $("chat-history-toggle").setAttribute("aria-expanded", String(open));
}

async function renameConversation(item) {
  const id = Number(item.dataset.id);
  const titleEl = item.querySelector(".conv-title");
  const original = titleEl.textContent;
  const input = document.createElement("input");
  input.className = "conv-rename";
  input.value = original;
  input.maxLength = 50;
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const value = input.value.trim();
    if (!save || !value || value === original) {
      loadConversations();
      return;
    }
    try {
      await api(`/api/conversations/${id}`, { method: "PATCH", body: { title: value } });
      toast("已重命名");
    } catch (error) {
      toast(error.message);
    }
    loadConversations();
  };

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
}

async function deleteConversation(id) {
  const item = document.querySelector(`.conv-item[data-id="${id}"]`);
  const title = item ? item.querySelector(".conv-title").textContent : "该对话";
  if (!window.confirm(`删除对话“${title}”？删除后无法恢复。`)) return;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    toast("已删除");
    if (state.conversationId === id) newConversation();
    loadConversations();
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- 专业查询 ---------- */

let majorOptionsCache = null;

function fillSelect(id, values, preferred) {
  const select = $(id);
  select.innerHTML = values
    .map((value) => `<option value="${esc(value)}">${esc(String(value))}</option>`)
    .join("");
  if (preferred && values.includes(preferred)) select.value = preferred;
}

async function loadMajorOptions() {
  if (majorOptionsCache) return majorOptionsCache;
  const data = await api("/api/majors/options");
  const profile = state.profile || {};
  fillSelect("major-year", data.years, data.years[0]);
  // 默认省份 / 科类取个人资料，资料缺省时才退回示例值
  fillSelect("major-province", data.provinces, profile.province || "吉林");
  fillSelect(
    "major-type",
    data.major_types,
    profile.subject_combo ||
      (data.major_types.includes("物理类") ? "物理类" : data.major_types[0])
  );
  majorOptionsCache = data;
  return data;
}

function renderScoreLine(row) {
  const score = row.score;
  if (!score) {
    return `<div class="score-line empty-score">${esc(row.score_note || "暂无录取数据")}</div>`;
  }
  const reference = score.is_reference ? '<span class="tag ref">参考</span>' : "";
  const rank = score.min_rank === null || score.min_rank === undefined ? "未公布" : score.min_rank;
  const note = score.note ? `<div class="score-note">${esc(score.note)}</div>` : "";
  return `
    <div class="score-line">
      <span class="score">最低分 <b>${score.min_score ?? "-"}</b></span>
      <span class="rank">最低位次 <b>${rank}</b></span>
      ${reference}
      <span class="score-meta">${score.year} 年 ${esc(score.province)} · ${esc(score.major_type)} · ${esc(score.plan_type || "")}</span>
      ${note}
    </div>`;
}

function renderScoreHistory(history) {
  if (!history || history.length < 2) return "";
  const parts = history.map((item) => {
    const rank =
      item.min_rank === null || item.min_rank === undefined ? "位次未公布" : `${item.min_rank} 位`;
    return `${item.year} 年 ${item.min_score} 分 / ${rank}`;
  });
  return `<div class="score-history">近三年：${parts.join(" · ")}</div>`;
}

/* 院校投档线：同一年可能有多条专业组，取本科批普通类里的最低分作为学校门槛 */
function pickSchoolLine(rows) {
  if (!rows || !rows.length) return null;
  const normal = rows.filter((row) => row.plan_type === "普通类" && row.batch === "本科批");
  const withNormalPlan = rows.filter((row) => row.plan_type === "普通类");
  const pool = normal.length ? normal : withNormalPlan.length ? withNormalPlan : rows;
  return pool.reduce((best, row) => {
    if (row.min_score === null || row.min_score === undefined) return best;
    if (!best || row.min_score < best.min_score) return row;
    return best;
  }, null);
}

function renderSchoolLine(rows, province, majorType) {
  const box = $("school-line");
  const years = [...new Set((rows || []).map((row) => row.year))].sort((a, b) => b - a);
  const lines = years
    .slice(0, 2)
    .map((year) => pickSchoolLine((rows || []).filter((row) => row.year === year)))
    .filter(Boolean);

  if (!lines.length) {
    box.innerHTML = '<span class="school-line-empty">暂无院校投档线数据</span>';
    return;
  }

  const latest = lines[0];
  const previous = lines[1];
  const delta = previous ? latest.min_score - previous.min_score : null;
  box.innerHTML = `
    <span class="school-line-title">院校投档线</span>
    <span class="school-line-main">
      ${latest.year} 年 ${esc(province)} · ${esc(majorType)} · ${esc(latest.batch || "本科批")} / ${esc(latest.plan_type || "普通类")}
      <b>${latest.min_score ?? "-"}</b> 分
      <em>位次 ${latest.min_rank ?? "-"}</em>
    </span>
    ${previous ? `<span class="school-line-prev">${previous.year} 年 ${previous.min_score ?? "-"} 分 / 位次 ${previous.min_rank ?? "-"}</span>` : ""}
    ${delta !== null ? `<span class="school-line-delta ${delta < 0 ? "down" : ""}">较 ${previous.year} 年 ${delta >= 0 ? "+" : ""}${delta} 分</span>` : ""}`;
}

async function loadMajors() {
  await loadMajorOptions();
  const params = new URLSearchParams();
  const keyword = $("major-keyword").value.trim();
  if (keyword) params.set("keyword", keyword);
  const year = $("major-year").value;
  const province = $("major-province").value;
  const majorType = $("major-type").value;
  if (year) params.set("year", year);
  if (province) params.set("province", province);
  if (majorType) params.set("major_type", majorType);

  // 专业列表与院校投档线并行请求；投档线失败不影响专业列表展示
  const schoolParams = new URLSearchParams();
  if (province) schoolParams.set("province", province);
  if (majorType) schoolParams.set("major_type", majorType);
  const schoolRequest =
    province && majorType
      ? api(`/api/school/scores?${schoolParams}`).catch(() => [])
      : Promise.resolve([]);

  const [rows, schoolRows] = await Promise.all([
    api(`/api/majors${params.toString() ? `?${params}` : ""}`),
    schoolRequest,
  ]);
  renderSchoolLine(schoolRows, province, majorType);

  const list = $("major-list");
  if (!rows.length) {
    list.innerHTML = '<p class="empty">没有找到匹配的专业，换个关键词试试。</p>';
    return;
  }

  list.innerHTML = rows
    .map((row) => `
      <article class="card">
        <div class="card-head">
          <div>
            <div class="card-title">${esc(row.display_name || row.name)}</div>
            <div class="card-sub">${esc(row.department || "东北师范大学")}</div>
          </div>
          <span class="tag">${esc(row.major_attr || "普通类")}</span>
        </div>
        ${renderScoreLine(row)}
        ${renderScoreHistory(row.score_history)}
        ${row.description ? `<div class="card-body">${esc(row.description)}</div>` : ""}
        ${row.subject_requirement ? `<div class="card-sub" style="margin-top:8px">选科要求：${esc(row.subject_requirement)}</div>` : ""}
      </article>`)
    .join("");
}

/* ---------- 计划变动 ---------- */

let planOptionsLoaded = false;

async function loadPlanOptions() {
  if (planOptionsLoaded) return;
  const data = await loadMajorOptions();
  const years = data.years || [];
  fillSelect("plan-province", data.provinces, "河南");
  fillSelect("plan-from-year", years, years.length > 1 ? years[1] : years[0]);
  fillSelect("plan-to-year", years, years[0]);
  planOptionsLoaded = true;
}

function renderPlanTable(containerId, countId, rows, headers, rowHtml) {
  $(countId).textContent = rows.length ? `共 ${rows.length} 项` : "";
  const container = $(containerId);
  if (!rows.length) {
    container.innerHTML = '<p class="empty">暂无相关专业。</p>';
    return;
  }
  container.innerHTML = `
    <table class="plan-table">
      <thead><tr>${headers.map((head) => `<th>${esc(head)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(rowHtml).join("")}</tbody>
    </table>`;
}

async function loadPlanDiff() {
  await loadPlanOptions();
  const province = $("plan-province").value;
  const fromYear = $("plan-from-year").value;
  const toYear = $("plan-to-year").value;
  if (!province || !fromYear || !toYear) return;

  const params = new URLSearchParams({ province, from_year: fromYear, to_year: toYear });
  const data = await api(`/api/school/plan-diff?${params}`);

  renderPlanTable("plan-added", "plan-added-count", data.added, ["专业", `${data.to_year} 年计划`], (row) => `
    <tr><td>${esc(row.major)}</td><td>${row.plan_count}</td></tr>`);
  renderPlanTable("plan-removed", "plan-removed-count", data.removed, ["专业", `${data.from_year} 年计划`], (row) => `
    <tr><td>${esc(row.major)}</td><td>${row.plan_count}</td></tr>`);
  renderPlanTable("plan-changed", "plan-changed-count", data.changed, ["专业", `${data.from_year} 年计划`, `${data.to_year} 年计划`, "变化"], (row) => `
    <tr>
      <td>${esc(row.major)}</td>
      <td>${row.from_count}</td>
      <td>${row.to_count}</td>
      <td class="${row.delta > 0 ? "delta-up" : "delta-down"}">${row.delta > 0 ? "+" : ""}${row.delta}</td>
    </tr>`);
}

/* ---------- 个人资料 ---------- */

const PROVINCE_OPTIONS = [
  "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江",
  "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆",
  "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
];
// 与后端 processor/score_query.py 保持一致
const COMPREHENSIVE_PROVINCES = ["上海", "浙江", "北京", "天津", "山东", "海南"];
const LEGACY_PROVINCES = ["新疆", "西藏"];
const SPECIAL_OPTIONS = ["无", "国家专项", "地方专项", "高校专项", "少数民族预科"];
const CAREER_OPTIONS = ["考研深造", "直接就业", "考公考编", "教师", "出国"];
const INTEREST_OPTIONS = ["师范", "理工", "计算机", "医学", "经管", "文史", "法学", "艺术", "体育", "外语"];

function subjectOptions(province) {
  if (COMPREHENSIVE_PROVINCES.includes(province)) return ["综合改革"];
  if (LEGACY_PROVINCES.includes(province)) return ["理工", "文史"];
  return ["物理类", "历史类"];
}

function renderSubjectSelect(province, selected) {
  const select = $("profile-subject");
  const options = subjectOptions(province);
  select.innerHTML = options
    .map((value) => `<option value="${esc(value)}">${esc(value)}</option>`)
    .join("");
  if (selected && options.includes(selected)) select.value = selected;
  // 综改省份不分物理/历史，科类固定为综合改革；保持可点击，并说明原因
  select.disabled = false;
  const hint = $("profile-subject-hint");
  if (hint) {
    hint.textContent = COMPREHENSIVE_PROVINCES.includes(province)
      ? "该省实行综合改革，不分物理类 / 历史类"
      : "";
  }
}

function renderInterestChips(selected) {
  const picked = new Set(
    (selected || "").split(/[,，]/).map((item) => item.trim()).filter(Boolean)
  );
  $("profile-interests").innerHTML = INTEREST_OPTIONS.map(
    (value) =>
      `<button type="button" class="chip${picked.has(value) ? " active" : ""}" data-interest="${esc(value)}">${esc(value)}</button>`
  ).join("");
}

function initProfileForm() {
  $("profile-province").innerHTML =
    '<option value="">请选择省份</option>' +
    PROVINCE_OPTIONS.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("");
  $("profile-special").innerHTML = SPECIAL_OPTIONS.map(
    (value) => `<option value="${esc(value)}">${esc(value)}</option>`
  ).join("");
  $("profile-career").innerHTML =
    '<option value="">未填写</option>' +
    CAREER_OPTIONS.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("");
  renderSubjectSelect("", "");
  renderInterestChips("");

  $("profile-province").addEventListener("change", () =>
    renderSubjectSelect($("profile-province").value, "")
  );
  $("profile-interests").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) chip.classList.toggle("active");
  });
  $("profile-form").addEventListener("submit", (event) =>
    saveProfile(event).catch((error) => toast(error.message))
  );
}

function renderProfile(data) {
  state.profile = data;
  const province = data.province || "";
  $("profile-province").value = province;
  renderSubjectSelect(province, data.subject_combo || "");
  $("profile-year").value = data.exam_year ?? "";
  $("profile-score").value = data.score ?? "";
  $("profile-rank").value = data.rank_no ?? "";
  $("profile-adjust").value =
    data.adjust_accepted === null || data.adjust_accepted === undefined
      ? ""
      : String(data.adjust_accepted);
  $("profile-special").value = data.special_qualification || "无";
  $("profile-career").value = data.career_intent || "";
  $("profile-regions").value = data.preferred_regions || "";
  renderInterestChips(data.interest_tags || "");

  const completeness = data.completeness ?? 0;
  $("profile-meter-fill").style.width = `${completeness}%`;
  $("profile-meter-text").textContent = `资料完整度 ${completeness}%`;
  const missing = data.missing || [];
  $("profile-missing").textContent = missing.length
    ? `还缺：${missing.join("、")}，补齐后分数匹配与专业推荐会更准确。`
    : "资料已齐全，分数问答与专业推荐会优先使用这份资料。";
  // 资料变化后，首页快捷问题的省份 / 科类要跟着变
  if ($("chat-empty")) resetChatBox();
}

async function loadProfile() {
  renderProfile(await api("/api/profile"));
}

function numberOrUndefined(id) {
  const value = $(id).value.trim();
  return value === "" ? undefined : Number(value);
}

async function saveProfile(event) {
  event.preventDefault();
  const interests = [...document.querySelectorAll("#profile-interests .chip.active")]
    .map((chip) => chip.dataset.interest)
    .join(",");
  const payload = {
    province: $("profile-province").value || undefined,
    subject_combo: $("profile-subject").value || undefined,
    exam_year: numberOrUndefined("profile-year"),
    score: numberOrUndefined("profile-score"),
    rank_no: numberOrUndefined("profile-rank"),
    adjust_accepted:
      $("profile-adjust").value === "" ? undefined : Number($("profile-adjust").value),
    special_qualification: $("profile-special").value || undefined,
    career_intent: $("profile-career").value || undefined,
    preferred_regions: $("profile-regions").value.trim() || undefined,
    interest_tags: interests || undefined,
  };
  renderProfile(await api("/api/profile", { method: "PUT", body: payload }));
  toast("资料已保存");
}

/* ---------- 我的提问 ---------- */

async function loadMyQuestions() {
  const rows = await api("/api/questions/my");
  const list = $("my-question-list");

  if (!rows.length) {
    list.innerHTML = '<p class="empty">还没有提交过提问。</p>';
    return;
  }

  list.innerHTML = rows
    .map((row) => `
      <article class="card">
        <div class="card-head">
          <div class="card-title">${esc(CATEGORY_LABELS[row.category] || row.category)}</div>
          <span class="tag ${esc(row.status)}">${esc(STATUS_LABELS[row.status] || row.status)}</span>
        </div>
        <div class="card-body">${esc(row.content)}</div>
        <div class="card-sub" style="margin-top:8px">提交于 ${esc(fmtTime(row.created_at))}</div>
        ${row.answer_content ? `<div class="answer-box"><strong>招生办答复：</strong>\n${esc(row.answer_content)}</div>` : ""}
      </article>`)
    .join("");
}

async function submitQuestion(event) {
  event.preventDefault();
  const content = $("question-content").value.trim();
  if (content.length < 10 || content.length > 500) {
    toast("问题内容需在 10~500 字之间");
    return;
  }
  try {
    const data = await api("/api/questions", {
      method: "POST",
      body: { category: $("question-category").value, content },
    });
    $("question-content").value = "";
    toast(data.duplicated ? "相同问题刚提交过，已为你保留原提问" : "提问已提交，招生办会尽快受理");
    await loadMyQuestions();
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- 公告（用户） ---------- */

// 每次登录（含 token 自动登录）弹出已发布公告，关闭后不影响下次登录再次展示
async function showLoginAnnouncements() {
  let rows = [];
  try {
    rows = (await api("/api/announcements")) || [];
  } catch (error) {
    return;
  }
  if (!rows.length) return;

  $("announcement-modal-list").innerHTML = rows
    .map((row) => `
      <article class="announcement-item">
        <div class="card-head">
          <div class="card-title">${esc(row.title)}</div>
          <span class="tag">${esc(CATEGORY_LABELS[row.category] || row.category)}</span>
        </div>
        <div class="card-sub" style="margin-top:6px">发布于 ${esc(fmtTime(row.published_at))}</div>
        <div class="card-body">${esc(row.content)}</div>
      </article>`)
    .join("");
  $("announcement-modal").classList.remove("hidden");
}

function closeAnnouncementModal() {
  $("announcement-modal").classList.add("hidden");
}

async function loadAnnouncements() {
  const rows = await api("/api/announcements");
  const list = $("announcement-list");

  if (!rows.length) {
    list.innerHTML = '<p class="empty">暂无已发布的公告。</p>';
    return;
  }

  list.innerHTML = rows
    .map((row) => `
      <article class="card">
        <div class="card-head">
          <div class="card-title">${esc(row.title)}</div>
          <span class="tag">${esc(CATEGORY_LABELS[row.category] || row.category)}</span>
        </div>
        <div class="card-sub" style="margin-top:6px">发布于 ${esc(fmtTime(row.published_at))} · 浏览 ${row.view_count} 次</div>
        <div class="card-body">${esc(row.content)}</div>
      </article>`)
    .join("");
}

/* ---------- 管理端：概览 ---------- */

function renderBars(container, rows, keyField, labelMap) {
  if (!rows.length) {
    container.innerHTML = '<p class="empty">暂无数据</p>';
    return;
  }
  const max = Math.max(...rows.map((row) => row.count), 1);
  container.innerHTML = rows
    .map((row) => {
      const label = labelMap ? labelMap[row[keyField]] || row[keyField] : row[keyField];
      const width = Math.round((row.count / max) * 100);
      return `
        <div class="bar-row">
          <div class="bar-label"><span>${esc(label)}</span><span>${row.count}</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
        </div>`;
    })
    .join("");
}

async function loadStats() {
  const data = await api("/api/admin/stats");
  const cards = [
    ["注册用户", data.users],
    ["管理员", data.admins],
    ["会话总数", data.conversations],
    ["提问总数", data.questions.pending + data.questions.answered + data.questions.closed],
    ["待处理提问", data.questions.pending],
    ["已发布公告", data.announcements.published],
  ];
  $("stat-cards").innerHTML = cards
    .map(([label, value]) => `<div class="stat-card"><span>${esc(label)}</span><strong>${value}</strong></div>`)
    .join("");

  renderBars($("stat-categories"), data.question_categories, "category", CATEGORY_LABELS);
  renderBars($("stat-intents"), data.intents, "intent", {
    score: "分数位次",
    major: "专业咨询",
    policy: "招生政策",
    other: "其他",
  });
}

/* ---------- 管理端：模型切换 ---------- */

let llmProviders = [];

function setLlmStatus(provider, model) {
  const item = llmProviders.find((entry) => entry.value === provider) || {};
  $("llm-status").textContent = `当前生效：${item.label || provider} / ${model}`;
}

function renderLlmModels(provider, selected) {
  const item = llmProviders.find((entry) => entry.value === provider);
  const models = item ? item.models : [];
  $("llm-model").innerHTML = models
    .map((model) => `<option value="${esc(model)}">${esc(model)}</option>`)
    .join("");
  if (selected && models.includes(selected)) $("llm-model").value = selected;

  // 离线兜底没有可添加的模型，禁用输入与按钮
  const canAdd = provider !== "mock";
  $("llm-new-model").disabled = !canAdd;
  $("llm-add-model").disabled = !canAdd;
}

function renderLlmProviders(selected) {
  $("llm-provider").innerHTML = llmProviders
    .map(
      (item) =>
        `<option value="${item.value}">${esc(item.label)}${item.available ? "" : "（不可用）"}</option>`
    )
    .join("");
  $("llm-provider").value = selected;
}

async function loadLlmSettings() {
  const data = await api("/api/admin/llm");
  llmProviders = data.providers;
  renderLlmProviders(data.provider);
  renderLlmModels(data.provider, data.model);
  setLlmStatus(data.provider, data.model);
}

async function saveLlmSettings() {
  const provider = $("llm-provider").value;
  const model = $("llm-model").value;
  const data = await api("/api/admin/llm", { method: "PUT", body: { provider, model } });
  setLlmStatus(data.provider, data.model);
  toast("模型已切换，后续问答立即生效");
}

async function addLlmModel() {
  const provider = $("llm-provider").value;
  const input = $("llm-new-model");
  const model = input.value.trim();
  if (!model) {
    toast("请填写模型名称");
    return;
  }

  try {
    const data = await api("/api/admin/llm/models", { method: "POST", body: { provider, model } });
    input.value = "";
    // 只刷新当前后端的模型列表，避免整页重载后回退到已保存的后端
    const item = llmProviders.find((entry) => entry.value === provider);
    if (item) item.models = data.models;
    $("llm-provider").value = provider;
    renderLlmModels(provider, model);
    toast(`模型 ${model} 已添加，点击“保存并生效”后启用`);
  } catch (error) {
    toast(error.message);
  }
}

async function addLlmProvider() {
  const payload = {
    name: $("llm-custom-name").value.trim(),
    base_url: $("llm-custom-url").value.trim(),
    api_key: $("llm-custom-key").value.trim(),
    model: $("llm-custom-model").value.trim(),
  };
  if (!payload.name || !payload.base_url || !payload.model) {
    toast("请填写后端名称、Base URL 和模型名");
    return;
  }

  try {
    const data = await api("/api/admin/llm/providers", { method: "POST", body: payload });
    const entry = {
      value: data.provider,
      label: data.label,
      models: data.models,
      available: true,
      base_url: data.base_url,
      custom: true,
    };
    const existing = llmProviders.find((item) => item.value === data.provider);
    if (existing) Object.assign(existing, entry);
    else llmProviders.push(entry);

    renderLlmProviders(data.provider);
    renderLlmModels(data.provider, payload.model);
    ["llm-custom-name", "llm-custom-url", "llm-custom-key", "llm-custom-model"].forEach(
      (id) => ($(id).value = "")
    );
    toast(`后端「${data.label}」已添加，点击“保存并生效”后启用`);
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- 管理端：提问受理 ---------- */

async function loadAdminQuestions() {
  const query = state.questionStatus ? `?status=${state.questionStatus}` : "";
  const data = await api(`/api/questions${query}`);
  const list = $("admin-question-list");

  document.querySelectorAll("#question-filter .chip").forEach((chip) => {
    const status = chip.dataset.status;
    const count = status ? data.counts[status] : Object.values(data.counts).reduce((sum, value) => sum + value, 0);
    const label = status ? STATUS_LABELS[status] : "全部";
    chip.textContent = `${label}（${count}）`;
  });

  if (!data.items.length) {
    list.innerHTML = '<p class="empty">当前筛选条件下没有提问。</p>';
    return;
  }

  list.innerHTML = data.items
    .map((row) => `
      <article class="card" data-id="${row.id}">
        <div class="card-head">
          <div>
            <div class="card-title">${esc(CATEGORY_LABELS[row.category] || row.category)} · ${esc(row.username)}</div>
            <div class="card-sub">#${row.id} · 提交于 ${esc(fmtTime(row.created_at))}</div>
          </div>
          <span class="tag ${esc(row.status)}">${esc(STATUS_LABELS[row.status] || row.status)}</span>
        </div>
        <div class="card-body">${esc(row.content)}</div>
        ${row.answer_content ? `<div class="answer-box"><strong>答复：</strong>\n${esc(row.answer_content)}</div>` : ""}
        ${row.status !== "closed" ? `
          <div class="answer-form">
            <textarea rows="2" maxlength="2000" placeholder="输入答复内容（1~2000 字）"></textarea>
            <button class="btn primary" type="button" data-action="answer">答复</button>
            <button class="btn" type="button" data-action="close">关闭</button>
          </div>` : ""}
      </article>`)
    .join("");
}

async function handleAdminQuestionClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest(".card");
  const id = card.dataset.id;
  button.disabled = true;

  try {
    if (button.dataset.action === "answer") {
      const answer = card.querySelector("textarea").value.trim();
      if (!answer) {
        toast("请先填写答复内容");
        return;
      }
      await api(`/api/questions/${id}/answer`, { method: "POST", body: { answer_content: answer } });
      toast("答复已发送");
    } else {
      await api(`/api/questions/${id}/close`, { method: "POST" });
      toast("提问已关闭");
    }
    await loadAdminQuestions();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

/* ---------- 管理端：公告管理 ---------- */

function resetAnnouncementForm() {
  state.editingAnnouncementId = null;
  $("announcement-id").value = "";
  $("announcement-title").value = "";
  $("announcement-category").value = "policy";
  $("announcement-content").value = "";
  $("announcement-submit").textContent = "保存草稿";
}

async function loadAdminAnnouncements() {
  const rows = await api("/api/announcements?include_draft=true");
  const list = $("admin-announcement-list");

  if (!rows.length) {
    list.innerHTML = '<p class="empty">还没有公告，先创建一条草稿。</p>';
    return;
  }

  list.innerHTML = rows
    .map((row) => {
      const sync = row.knowledge_synced === 2
        ? '<span class="tag sync-warn">知识库同步失败</span>'
        : row.knowledge_synced === 1
          ? '<span class="tag answered">已回灌知识库</span>'
          : "";
      return `
        <article class="card" data-id="${row.id}">
          <div class="card-head">
            <div>
              <div class="card-title">${esc(row.title)}</div>
              <div class="card-sub">${esc(CATEGORY_LABELS[row.category] || row.category)} · 更新于 ${esc(fmtTime(row.published_at || row.created_at))} · 浏览 ${row.view_count} 次</div>
            </div>
            <div>
              <span class="tag ${row.is_published ? "published" : "draft"}">${row.is_published ? "已发布" : "草稿"}</span>
              ${sync}
            </div>
          </div>
          <div class="card-body">${esc(row.content)}</div>
          <div class="card-actions">
            <button class="btn" type="button" data-action="edit">编辑</button>
            ${row.is_published
              ? '<button class="btn danger" type="button" data-action="unpublish">下架</button>'
              : '<button class="btn primary" type="button" data-action="publish">发布</button>'}
          </div>
        </article>`;
    })
    .join("");

  list.dataset.rows = JSON.stringify(rows);
}

async function submitAnnouncement(event) {
  event.preventDefault();
  const title = $("announcement-title").value.trim();
  const content = $("announcement-content").value.trim();
  const category = $("announcement-category").value;

  if (!title) {
    toast("请填写公告标题");
    return;
  }
  if (content.length < 10 || content.length > 5000) {
    toast("公告正文需在 10~5000 字之间");
    return;
  }

  try {
    if (state.editingAnnouncementId) {
      await api(`/api/announcements/${state.editingAnnouncementId}`, {
        method: "PUT",
        body: { title, content, category },
      });
      toast("公告已更新");
    } else {
      await api("/api/announcements", { method: "POST", body: { title, content, category } });
      toast("草稿已保存，可在下方列表中发布");
    }
    resetAnnouncementForm();
    await loadAdminAnnouncements();
  } catch (error) {
    toast(error.message);
  }
}

async function handleAdminAnnouncementClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const card = button.closest(".card");
  const id = Number(card.dataset.id);
  button.disabled = true;

  try {
    if (button.dataset.action === "publish") {
      await api(`/api/announcements/${id}/publish`, { method: "POST" });
      toast("公告已发布并回灌知识库");
    } else if (button.dataset.action === "unpublish") {
      await api(`/api/announcements/${id}/unpublish`, { method: "POST" });
      toast("公告已下架，AI 不再引用");
    } else {
      const rows = JSON.parse($("admin-announcement-list").dataset.rows || "[]");
      const row = rows.find((item) => item.id === id);
      if (!row) return;
      state.editingAnnouncementId = id;
      $("announcement-id").value = id;
      $("announcement-title").value = row.title;
      $("announcement-category").value = row.category;
      $("announcement-content").value = row.content;
      $("announcement-submit").textContent = "保存修改";
      $("announcement-title").focus();
      return;
    }
    await loadAdminAnnouncements();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

/* ---------- 事件绑定与启动 ---------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.mode));
});

$("auth-form").addEventListener("submit", handleAuth);
$("logout").addEventListener("click", logout);

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendChat();
});
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChat();
  }
});
resetChatBox();
$("chat-input").addEventListener("input", resizeChatInput);

$("chat-new").addEventListener("click", newConversation);
$("chat-history-toggle").addEventListener("click", () => {
  setHistoryOpen(!$("chat-history").classList.contains("open"));
});
$("conv-search").addEventListener("input", () => renderConversationList(state.conversations));
document.addEventListener("click", (event) => {
  const panel = $("chat-history");
  if (!panel.classList.contains("open")) return;
  if (event.target.closest("#chat-history") || event.target.closest("#chat-history-toggle")) return;
  setHistoryOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  setHistoryOpen(false);
  closeAnnouncementModal();
});
$("conversation-list").addEventListener("click", (event) => {
  const item = event.target.closest(".conv-item");
  if (!item) return;
  const button = event.target.closest("button");
  const action = button ? button.dataset.action : "";
  const id = Number(item.dataset.id);
  if (action === "rename") {
    renameConversation(item);
    return;
  }
  if (action === "delete") {
    deleteConversation(id);
    return;
  }
  if (action === "open") openConversation(id);
});

$("major-search").addEventListener("click", () => loadMajors().catch((error) => toast(error.message)));
["major-year", "major-province", "major-type"].forEach((id) => {
  $(id).addEventListener("change", () => loadMajors().catch((error) => toast(error.message)));
});
$("major-keyword").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadMajors().catch((error) => toast(error.message));
  }
});

$("plan-diff-run").addEventListener("click", () =>
  loadPlanDiff().catch((error) => toast(error.message))
);

$("question-form").addEventListener("submit", submitQuestion);
$("admin-question-list").addEventListener("click", handleAdminQuestionClick);
$("question-filter").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  state.questionStatus = chip.dataset.status;
  document.querySelectorAll("#question-filter .chip").forEach((item) => {
    item.classList.toggle("active", item === chip);
  });
  loadAdminQuestions().catch((error) => toast(error.message));
});

$("announcement-form").addEventListener("submit", submitAnnouncement);
$("announcement-reset").addEventListener("click", resetAnnouncementForm);
$("admin-announcement-list").addEventListener("click", handleAdminAnnouncementClick);

$("announcement-modal-close").addEventListener("click", closeAnnouncementModal);
$("announcement-modal-ok").addEventListener("click", closeAnnouncementModal);
$("announcement-modal").addEventListener("click", (event) => {
  if (event.target === $("announcement-modal")) closeAnnouncementModal();
});

initProfileForm();

$("llm-provider").addEventListener("change", () => renderLlmModels($("llm-provider").value));
$("llm-add-model").addEventListener("click", () => addLlmModel());
$("llm-add-provider").addEventListener("click", () => addLlmProvider());
$("llm-new-model").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addLlmModel();
  }
});
$("llm-save").addEventListener("click", () =>
  saveLlmSettings().catch((error) => toast(error.message))
);

(async function boot() {
  if (!state.token) return;
  try {
    state.profile = await api("/api/profile");
    enterApp();
  } catch (error) {
    logout();
  }
})();
