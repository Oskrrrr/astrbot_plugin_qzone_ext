const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

function unwrap(result) {
  if (result && typeof result === "object") {
    if (result.ok === false) throw new Error(result.error?.message || result.message || "请求失败");
    if (Object.prototype.hasOwnProperty.call(result, "data")) return result.data;
  }
  return result ?? {};
}

const apiGet = async (endpoint) => unwrap(await bridge.apiGet(endpoint, {}));
const apiPost = async (endpoint, body) => unwrap(await bridge.apiPost(endpoint, body || {}));

function setMsg(text, kind = "") {
  const box = $("msg");
  box.textContent = text || "";
  box.className = "msg" + (kind ? " " + kind : "");
}

function setSaveMsg(text, kind = "") {
  const box = $("saveMsg");
  box.textContent = text || "";
  box.className = "msg" + (kind ? " " + kind : "");
}

function fmtTime(ts) {
  const n = Number(ts || 0);
  if (!n) return "";
  const d = new Date(n * 1000);
  const pad = (v) => String(v).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderStatus(data) {
  $("stReady").textContent = data.ready ? "已连接" : "未就绪";
  $("stReady").style.color = data.ready ? "var(--ok)" : "var(--danger)";
  $("stToday").textContent = `${data.today}/${data.daily_limit}`;
  $("stHours").textContent = data.hours ? `${data.hours} 点` : "全天";
  $("stReplied").textContent = `${data.replied_count} 条`;
  $("dryRun").checked = Boolean(data.dry_run);
  $("dryRun").closest(".switch").classList.toggle("on", Boolean(data.dry_run));
  $("dryHint").textContent = data.dry_run
    ? "打开中：只生成文案并记录，不会真的发布。"
    : "已关闭：到点会真的发到 QQ 空间。";
  renderHistory(data.history || []);
}

function renderHistory(items) {
  const list = $("histList");
  $("histCount").textContent = items.length ? `最近 ${items.length} 条` : "";
  if (!items.length) {
    list.innerHTML = `<li class="empty">还没有生成过文案，点上面「试发一条」试试。</li>`;
    return;
  }
  list.innerHTML = items
    .map((item) => {
      const mode = item.mode === "dry" ? "dry-run" : item.ok === false ? "失败" : "已发布";
      const cls = item.mode === "dry" ? "dry" : item.ok === false ? "err" : "ok";
      const err = item.error ? ` · ${esc(item.error)}` : "";
      const note = item.note ? ` · ${esc(item.note)}` : "";
      const source =
        item.image_source === "stock" ? "预存图" : item.image_source === "generated" ? "生成图" : "";
      const promptBox = item.image_prompt
        ? `<details class="prompt-box"><summary>画面描述</summary><pre>${esc(item.image_prompt)}</pre></details>`
        : "";
      return `<li>
        <div class="hist-top">
          <span class="hist-time">${esc(fmtTime(item.ts))}</span>
          <span class="tag ${cls}">${mode}</span>
          ${source ? `<span class="hist-time">配图：${source}</span>` : ""}
          ${err ? `<span class="hist-time">${err}</span>` : ""}
          ${note ? `<span class="hist-time">${note}</span>` : ""}
        </div>
        <div class="hist-text">${esc(item.text || "")}</div>
        ${promptBox}
      </li>`;
    })
    .join("");
}

async function load() {
  const data = await apiGet("page/status");
  renderStatus(data);
}

// ------------------------------------------------------------------ 设置

function fillSelect(select, items, emptyLabel, value) {
  const rows = [`<option value="">${esc(emptyLabel)}</option>`].concat(
    items.map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`)
  );
  select.innerHTML = rows.join("");
  select.value = value || "";
}

function fillConfig(data) {
  const values = data.values || {};
  const options = data.options || {};
  $("cfgEnabled").checked = Boolean(values.enabled);
  $("cfgDryRun").checked = Boolean(values.dry_run);
  fillSelect(
    $("cfgPersona"),
    (options.personas || []).map((p) => ({ id: p.id, label: p.name || p.id })),
    "跟随全局默认人格",
    values.persona_id
  );
  fillSelect(
    $("cfgProvider"),
    (options.providers || []).map((p) => ({
      id: p.id,
      label: p.model && p.model !== p.id ? `${p.id} · ${p.model}` : p.id,
    })),
    "跟随全局默认模型",
    values.provider_id
  );
  $("cfgPublishEnabled").checked = Boolean(values.publish_enabled);
  $("cfgOffset").value = values.publish_offset_minutes ?? 50;
  $("cfgSkip").value = values.publish_skip_probability ?? 0.85;
  $("cfgDaily").value = values.daily_limit ?? 3;
  $("cfgHours").value = values.publish_hours ?? "8-23";
  $("cfgAttachImage").checked = Boolean(values.attach_image);
  $("cfgImagePrompt").value = values.image_prompt || "";
  $("cfgImagePrompt").placeholder = options.defaults?.image_prompt || "";
  $("cfgDynamicImage").checked = Boolean(values.dynamic_image_prompt);
  $("cfgDirectorPrompt").value = values.image_director_prompt || "";
  $("cfgImageNegative").value = values.image_negative || "";
  $("cfgDirectorPrompt").placeholder = options.defaults?.image_director_prompt || "";
  $("cfgInjectTime").checked = values.inject_time !== false;
  $("cfgInjectWeather").checked = Boolean(values.inject_weather);
  $("cfgWeatherCity").value = values.weather_city || "";
  $("cfgStockEnabled").checked = Boolean(values.stock_image_enabled);
  $("cfgStockDir").value = values.stock_image_dir || "";
  $("cfgStockRatio").value = values.stock_image_ratio ?? 0.5;
  const drawOk = Boolean(options.draw_available);
  $("drawNote").textContent = drawOk
    ? "已检测到「画图」插件：开启后会先画一张再发布，画失败会自动退回纯文字。"
    : "没检测到「画图」插件：开启也只发纯文字（不会报错）。";
  $("drawNote").classList.toggle("err", !drawOk);
  const source = options.style_source || {};
  const preview = (source.text || "").slice(0, 40);
  $("styleSource").textContent =
    "风格来源：" + (source.label || "无") + (preview ? ` ｜ ${preview}${(source.text || "").length > 40 ? "…" : ""}` : "");
  $("styleSource").classList.toggle("err", false);
  if (options.card_available) {
    $("cfgPersona").title = "依赖角色卡：选角色卡里的人格名即可套用它的说说配置";
  }
  $("cfgMaterialEnabled").checked = Boolean(values.material_enabled);
  $("cfgMaterialMax").value = values.material_max_messages ?? 150;
  $("cfgMaterialGroups").value = values.material_groups || "";
  $("cfgMaterialIgnore").value = values.material_ignore_groups || "";
  $("cfgRequirePersona").checked = values.require_persona !== false;
  $("cfgInteractEnabled").checked = Boolean(values.interact_enabled);
  $("cfgInteractLike").checked = values.interact_like !== false;
  $("cfgInteractInterval").value = values.interact_interval_minutes ?? 60;
  $("cfgInteractMax").value = values.interact_max_per_run ?? 2;
  $("cfgInteractIgnore").value = values.interact_ignore_users || "";
  $("cfgInteractMinChars").value = values.interact_min_chars ?? 8;
  $("cfgCommentPrompt").value = values.comment_prompt || "";
  $("cfgReplyEnabled").checked = Boolean(values.reply_enabled);
  $("cfgInterval").value = values.reply_interval_minutes ?? 15;
  $("cfgReplyMax").value = values.reply_max_per_run ?? 3;
  $("cfgReplyMinAge").value = values.reply_min_age_seconds ?? 60;
  $("cfgReplyChars").value = values.max_reply_chars ?? 80;
  $("cfgPostPrompt").value = values.post_prompt || "";
  $("cfgReplyPrompt").value = values.reply_prompt || "";
  $("cfgPostPrompt").placeholder = options.defaults?.post_prompt || "";
  $("cfgReplyPrompt").placeholder = options.defaults?.reply_prompt || "";
}

function collectConfig() {
  return {
    enabled: $("cfgEnabled").checked,
    dry_run: $("cfgDryRun").checked,
    persona_id: $("cfgPersona").value,
    provider_id: $("cfgProvider").value,
    publish_enabled: $("cfgPublishEnabled").checked,
    publish_offset_minutes: Number($("cfgOffset").value || 0),
    publish_skip_probability: Number($("cfgSkip").value || 0),
    daily_limit: Number($("cfgDaily").value || 3),
    publish_hours: $("cfgHours").value.trim(),
    attach_image: $("cfgAttachImage").checked,
    image_prompt: $("cfgImagePrompt").value,
    dynamic_image_prompt: $("cfgDynamicImage").checked,
    image_negative: $("cfgImageNegative").value,
    image_director_prompt: $("cfgDirectorPrompt").value,
    inject_time: $("cfgInjectTime").checked,
    inject_weather: $("cfgInjectWeather").checked,
    weather_city: $("cfgWeatherCity").value,
    stock_image_enabled: $("cfgStockEnabled").checked,
    stock_image_dir: $("cfgStockDir").value,
    stock_image_ratio: Number($("cfgStockRatio").value || 0),
    material_enabled: $("cfgMaterialEnabled").checked,
    material_groups: $("cfgMaterialGroups").value,
    material_ignore_groups: $("cfgMaterialIgnore").value,
    material_max_messages: Number($("cfgMaterialMax").value || 150),
    require_persona: $("cfgRequirePersona").checked,
    interact_enabled: $("cfgInteractEnabled").checked,
    interact_like: $("cfgInteractLike").checked,
    interact_interval_minutes: Number($("cfgInteractInterval").value || 60),
    interact_max_per_run: Number($("cfgInteractMax").value || 2),
    interact_ignore_users: $("cfgInteractIgnore").value,
    interact_min_chars: Number($("cfgInteractMinChars").value || 8),
    comment_prompt: $("cfgCommentPrompt").value,
    reply_enabled: $("cfgReplyEnabled").checked,
    reply_interval_minutes: Number($("cfgInterval").value || 15),
    reply_max_per_run: Number($("cfgReplyMax").value || 3),
    reply_min_age_seconds: Number($("cfgReplyMinAge").value || 60),
    max_reply_chars: Number($("cfgReplyChars").value || 80),
    post_prompt: $("cfgPostPrompt").value,
    reply_prompt: $("cfgReplyPrompt").value,
  };
}

async function loadConfig() {
  const data = await apiGet("page/config");
  fillConfig(data);
}

async function saveConfig() {
  const btn = $("btnSave");
  btn.disabled = true;
  setSaveMsg("保存中…");
  try {
    const data = await apiPost("page/save", collectConfig());
    await loadConfig();
    if (data.status) renderStatus(data.status);
    setSaveMsg("已保存，已生效", "ok");
  } catch (error) {
    setSaveMsg(error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ 概览操作

async function toggleDryRun(event) {
  const value = event.target.checked;
  event.target.closest(".switch").classList.toggle("on", value);
  setMsg("保存中…");
  try {
    const data = await apiPost("page/toggle", { dry_run: value });
    renderStatus(data);
    setMsg(value ? "已切到试运行" : "已关闭试运行：下一条会真的发布", "ok");
  } catch (error) {
    event.target.checked = !value;
    event.target.closest(".switch").classList.toggle("on", !value);
    setMsg(error.message, "err");
  }
}

async function publish() {
  const btn = $("btnPublish");
  btn.disabled = true;
  setMsg("正在生成文案…");
  $("preview").hidden = true;
  try {
    const data = await apiPost("page/publish", {});
    const result = data.result || {};
    if (result.ok) {
      const image = result.image || null;
      if (image && image.data) {
        $("previewImg").src = image.data;
        $("previewPath").textContent = image.path || "";
        $("previewPrompt").textContent = result.image_prompt || "";
        $("preview").hidden = false;
      }
      const suffix = image ? "（已生成配图预览）" : "";
      setMsg(result.dry_run ? `试运行生成${suffix}：${result.text}` : `已发布：${result.text}`, "ok");
    } else {
      setMsg(`没发：${result.reason || "未知原因"}`, "err");
    }
    if (data.status) renderStatus(data.status);
  } catch (error) {
    setMsg(error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

async function checkReply() {
  const btn = $("btnReply");
  btn.disabled = true;
  setMsg("正在检查评论…");
  try {
    const data = await apiPost("page/reply", {});
    const result = data.result || {};
    const previewed = result.previewed ? `，试运行 ${result.previewed} 条` : "";
    setMsg(`回复 ${result.replied ?? 0} 条${previewed}`, "ok");
    if (data.status) renderStatus(data.status);
  } catch (error) {
    setMsg(error.message, "err");
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ 初始化

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  $("tab-overview").hidden = name !== "overview";
  $("tab-settings").hidden = name !== "settings";
  if (name === "settings") {
    loadConfig().catch((error) => setSaveMsg(error.message, "err"));
  }
}

async function init() {
  if (!bridge) {
    document.body.innerHTML = '<div class="empty">没有检测到 AstrBot 页面桥接，请从 WebUI 插件页面进入。</div>';
    return;
  }
  try {
    await bridge.ready();
  } catch (error) {
    setMsg(error.message || "页面初始化失败", "err");
    return;
  }
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
  $("dryRun").addEventListener("change", toggleDryRun);
  $("btnPublish").addEventListener("click", publish);
  $("btnReply").addEventListener("click", checkReply);
  $("btnSave").addEventListener("click", saveConfig);
  $("btnRefresh").addEventListener("click", () => load().catch((e) => setMsg(e.message, "err")));
  try {
    await load();
  } catch (error) {
    setMsg(error.message, "err");
  }
}

init();
