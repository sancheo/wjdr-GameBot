"use strict";

const $ = id => document.getElementById(id);
const resources = [["meat", "生肉"], ["wood", "木材"], ["coal", "煤矿"], ["iron", "铁矿"]];
const monsters = [["beast", "野兽", 30, 1], ["polar_beast", "冰原巨兽", 6, 2]];
const helpClickerHint = "打开时其他功能不会执行, 只会进入联盟互助界面快速点击互助";
const bearHint = "开启时其他任务不执行, 只专注打熊";
const bearModes = [["bear_drive", "主动开车"], ["bear_follow", "自动跟车"], ["bear_follow_no_heroes", "无英雄跟车"]];
let running = false;
let connected = false;
let selected = null;
let devicesKey = "";
let lastLog = 0;
let logKey = null;
let lastNotice = 0;
let mutation = Promise.resolve();
let syncTimer = null;
let taskStatusKey = "";
let updateState = null;
let updateRequestPending = false;
const deviceStatuses = {stopped: "未启动", starting: "启动中", restarting: "重启中", running: "已启动", unknown: "状态待确认", unauthorized: "待授权", offline: "离线"};
const deviceStatus = device => device.kind === "physical" && device.status === "running" ? "已连接" : deviceStatuses[device.status];
const deviceName = device => device.name + (device.kind === "physical" ? "（真机）" : "（模拟器）");
const taskStatuses = {waiting: "等待执行", running: "执行中", retrying: "异常重试中", disabled: "已停用", stopped: "已停止", failed: "异常结束"};

function renderTaskStatus(state) {
  const rows = state.task_status || [];
  const key = JSON.stringify([rows, state.running, state.stopping, connected]);
  if (key === taskStatusKey) return;
  taskStatusKey = key;
  $("task-status-empty").hidden = rows.length > 0;
  const fragment = document.createDocumentFragment();
  for (const task of rows) {
    const row = document.createElement("div");
    row.className = "task-status-row " + task.status;
    row.dataset.task = task.name;
    const heading = document.createElement("div");
    heading.className = "task-status-name";
    const name = document.createElement("strong");
    name.textContent = task.label;
    const status = document.createElement("span");
    status.textContent = task.retry_requested ? "已安排重试" : taskStatuses[task.status];
    heading.append(name, status);
    const detail = document.createElement("div");
    detail.className = "task-status-detail";
    if (task.next_run !== null && task.next_run !== undefined) {
      const date = new Date(task.next_run * 1000);
      detail.textContent = "下次执行：" + date.toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false});
    } else detail.textContent = task.reason || (task.status === "running" ? "正在检查游戏画面" : "");
    row.append(heading, detail);
    if (task.status === "disabled") {
      const retry = document.createElement("button");
      retry.className = "task-retry";
      retry.textContent = task.retry_requested ? "已安排" : "单项重试";
      retry.setAttribute("aria-label", "重试" + task.label);
      retry.disabled = !connected || !state.running || state.stopping || task.retry_requested;
      retry.addEventListener("click", () => {
        retry.disabled = true;
        queue(async () => {
          try { render(await window.pywebview.api.retry_task(task.name)); }
          finally { taskStatusKey = ""; }
        });
      });
      row.append(retry);
    }
    fragment.append(row);
  }
  $("task-status-rows").replaceChildren(fragment);
}

function closeDevices(returnFocus = false) {
  $("device-list").hidden = true;
  $("device").setAttribute("aria-expanded", "false");
  if (returnFocus) $("device").focus();
}

function renderDevices(state) {
  const key = JSON.stringify([state.devices, state.selected]);
  if (key !== devicesKey) {
    devicesKey = key;
    $("device-list").replaceChildren();
    for (const device of state.devices) {
      const row = document.createElement("div");
      row.className = "device-item" + (device.id === state.selected ? " selected" : "");
      const choice = document.createElement("button");
      choice.className = "device-choice";
      choice.dataset.deviceId = device.id;
      choice.setAttribute("aria-pressed", String(device.id === state.selected));
      const name = document.createElement("span");
      name.textContent = deviceName(device);
      const status = document.createElement("span");
      status.className = "device-state " + device.status;
      status.textContent = deviceStatus(device) + (device.serial ? ` · ${device.serial}` : "");
      if (device.status === "unauthorized") choice.title = "请解锁手机并允许 USB 调试，然后点击刷新";
      if (device.status === "offline") choice.title = "请检查数据线、USB 调试和手机连接，然后点击刷新";
      choice.append(name, status);
      if (device.note) {
        const note = document.createElement("span");
        note.className = "device-note-text";
        note.textContent = device.note;
        choice.append(note);
      }
      choice.addEventListener("click", () => {
        clearTimeout(syncTimer);
        const config = formConfig();
        closeDevices(true);
        queue(async () => render(await window.pywebview.api.select_device(device.id, config)));
      });
      row.append(choice);
      const editNote = document.createElement("button");
      editNote.className = "device-note";
      editNote.textContent = device.note ? "修改备注" : "备注";
      editNote.setAttribute("aria-label", `编辑设备备注 ${device.name}`);
      editNote.addEventListener("click", () => {
        closeDevices();
        $("device-note-dialog").dataset.deviceId = device.id;
        $("device-note-name").textContent = deviceName(device);
        $("device-note-input").value = device.note || "";
        $("device-note-dialog").showModal();
        $("device-note-input").focus();
      });
      row.append(editNote);
      $("device-list").append(row);
    }
  }
  const device = state.devices.find(item => item.id === state.selected);
  $("device-label").textContent = device ? `${deviceName(device)}${device.note ? " · " + device.note : ""} · ${deviceStatus(device)}` : "请选择设备";
  $("device").disabled = running || state.scanning || !connected || !state.devices.length;
  $("device-list").querySelectorAll("button").forEach(button => button.disabled = running || state.scanning || !connected);
  if ($("device").disabled) closeDevices();
}

$("device-note-form").addEventListener("submit", event => {
  event.preventDefault();
  const id = $("device-note-dialog").dataset.deviceId;
  const note = $("device-note-input").value;
  $("device-note-save").disabled = true;
  queue(async () => {
    try {
      render(await window.pywebview.api.set_device_note(id, note));
      $("device-note-dialog").close();
      $("device").focus();
    } finally { $("device-note-save").disabled = false; }
  });
});
$("device-note-cancel").addEventListener("click", () => $("device-note-dialog").close());

function notice(message, title = "提示") {
  $("notice-title").textContent = title;
  $("notice-message").textContent = String(message);
  if (!$("notice").open) $("notice").showModal();
}

function option(card, name, label, indent = false, hint = "") {
  const row = document.createElement("div");
  row.className = "option-row" + (indent ? " indent" : "");
  row.innerHTML = `<label class="option-name" for="option-${name}">${label}</label><label class="switch"><input type="checkbox" id="option-${name}" data-option="${name}" aria-label="${label}" ${["upgrade", "bounty", "help_clicker", "bear"].includes(name) ? "" : "checked"}><span></span></label>`;
  if (hint) {
    const icon = document.createElement("img");
    icon.className = "info"; icon.src = "ui/info.png"; icon.alt = "说明"; icon.title = hint; icon.tabIndex = 0;
    row.querySelector(".option-name").append(icon);
  }
  $(card).querySelector(".card-fields").append(row);
}

function field(card, name, label, value, minimum, hint = "") {
  const row = document.createElement("div");
  row.className = "input-row";
  row.innerHTML = `<label for="entry-${name}">${label}</label><input id="entry-${name}" data-entry="${name}" type="number" min="${minimum}" step="${name === "reconnect_minutes" ? "any" : "1"}" value="${value}" aria-label="${label}">`;
  if (hint) {
    const icon = document.createElement("img");
    icon.className = "info"; icon.src = "ui/info.png"; icon.alt = "说明"; icon.title = hint; icon.tabIndex = 0;
    row.querySelector("label").append(icon);
  }
  $(card).querySelector(".card-fields").append(row);
}

for (const [name, label] of [["help", "联盟互助"], ["explore", "探险经验"], ["donate", "联盟捐献"], ["alliance_chests", "联盟宝箱"], ["recruit", "免费招募"], ["treasure", "宠物寻宝"], ["warehouse", "仓库补给"], ["dawn", "晨曦回礼"], ["pet", "宠物技能"], ["task_rewards", "任务奖励"]]) option("daily", name, label, false, name === "pet" ? "只触发心灵伴侣、重物搬运、敏锐器官、大地馈赠" : "");
option("training", "train", "自动练兵");
option("training", "upgrade", "兵种升级", true, "开启后不再训练新兵而是将低级兵种升到当前最高级");
for (const [name, label] of [["shield", "盾兵"], ["spear", "矛兵"], ["archer", "射手"]]) option("training", name, label, true);
option("training", "heal", "自动治疗", false, "在野外检测伤兵图标，快速选择并治疗，再请求联盟互助；可独立于自动练兵开启");
option("connection", "tree", "自动收集生命结晶");
field("connection", "tree_threshold", "收集阈值 · 大于 0", "1000", 1);
option("connection", "reconnect", "强制下线后自动重连");
field("connection", "reconnect_minutes", "点击重连前等待 · 分钟", "10", 0);
option("wilderness", "bear", "自动打熊", false, bearHint);
const bearModeList = document.createElement("div");
bearModeList.className = "resources bear-modes";
bearModeList.setAttribute("role", "group");
bearModeList.setAttribute("aria-label", "自动打熊子选项");
for (const [name, label] of bearModes) {
  const row = document.createElement("div");
  row.className = "resource";
  row.innerHTML = `<label><input type="checkbox" id="option-${name}" data-option="${name}" ${name === "bear_follow_no_heroes" ? "checked" : ""}>${label}</label>`;
  if (name === "bear_follow") {
    const icon = document.createElement("img");
    icon.className = "info"; icon.src = "ui/info.png"; icon.alt = "说明"; icon.title = "自动跟车会主动更换车头英雄为杰西、书允、杰塞尔"; icon.tabIndex = 0;
    row.querySelector("label").append(icon);
  }
  bearModeList.append(row);
}
$("wilderness").querySelector(".card-fields").append(bearModeList);
option("wilderness", "intelligence", "灯塔情报");
option("wilderness", "bounty", "大师悬赏", true);
field("wilderness", "stamina_threshold", "体力触发阈值", "0", 0, "值为0时不限制，非0时判断体力大于该值时才执行操作");
option("wilderness", "hunt", "自动打怪");
const monsterList = document.createElement("div");
monsterList.className = "resources";
for (const [name, label, maximum, minimum] of monsters) {
  const row = document.createElement("div"); row.className = "resource";
  row.innerHTML = `<label><input type="checkbox" id="option-${name}" data-option="${name}" checked>${label}</label><select id="hunt-level-${name}" data-hunt-level="${name}" aria-label="${label}挑战等级">${Array.from({length: maximum - minimum + 1}, (_, i) => `<option>${i + minimum}级</option>`).join("")}</select>`;
  monsterList.append(row);
}
$("wilderness").querySelector(".card-fields").append(monsterList);
option("wilderness", "gather", "资源采集", false, "按顺序执行采集，无队列或兵力不足时自动跳过");
const gatherFilter = document.createElement("div");
gatherFilter.className = "gather-filter";
gatherFilter.innerHTML = '<label><input type="checkbox" id="option-gather_full_only" data-option="gather_full_only" checked>仅搜索资源为满的资源点</label>';
$("wilderness").querySelector(".card-fields").append(gatherFilter);
const resourceList = document.createElement("div");
resourceList.className = "gather-steps";
resourceList.setAttribute("role", "group");
resourceList.setAttribute("aria-label", "资源采集顺序");
function gatherSteps() {
  return [...resourceList.querySelectorAll(".gather-step")].map(row => ({
    resource: row.querySelector("[data-gather-resource]").value,
    level: row.querySelector("[data-gather-level]").value
  }));
}
function renderGatherSteps(steps) {
  resourceList.replaceChildren();
  steps.forEach((step, index) => {
    const row = document.createElement("div");
    row.className = "gather-step";
    row.innerHTML = `<select data-gather-resource aria-label="第${index + 1}项采集资源">${resources.map(([name, label]) => `<option value="${name}">${label}</option>`).join("")}</select><select data-gather-level aria-label="第${index + 1}项采集等级">${Array.from({length: 8}, (_, i) => `<option>${i + 1}级</option>`).join("")}</select>`;
    row.querySelector("[data-gather-resource]").value = step.resource;
    row.querySelector("[data-gather-level]").value = step.level;
    if (index < steps.length - 1) {
      const arrow = document.createElement("span");
      arrow.className = "gather-arrow";
      const image = document.createElement("img");
      image.src = "ui/connect_chevrons_25x25.png";
      image.alt = "";
      image.width = 25;
      image.height = 25;
      arrow.append(image);
      arrow.setAttribute("aria-hidden", "true");
      row.append(arrow);
    } else {
      for (const [action, label, icon] of [["add", "增加采集子选项", "add_icon_25x25"], ["remove", "删除最后一个采集子选项", "delete_icon_25x25"]]) {
        if ((action === "add" && steps.length === 6) || (action === "remove" && steps.length === 1)) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.gatherAction = action;
        button.setAttribute("aria-label", label);
        button.title = label;
        const image = document.createElement("img");
        image.src = `ui/${icon}.png`;
        image.alt = "";
        image.width = 25;
        image.height = 25;
        button.append(image);
        row.append(button);
      }
    }
    resourceList.append(row);
  });
}
renderGatherSteps(resources.map(([resource]) => ({resource, level: "8级"})));
resourceList.addEventListener("click", event => {
  const button = event.target.closest("[data-gather-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.gatherAction;
  const steps = gatherSteps();
  if (action === "add" && steps.length < 6) steps.push({resource: "meat", level: "8级"});
  if (action === "remove" && steps.length > 1) steps.pop();
  renderGatherSteps(steps);
  syncConfig();
});
$("wilderness").querySelector(".card-fields").append(resourceList);
option("alt-account", "help_clicker", "互助连点", false, helpClickerHint);

function formConfig() {
  const config = {options: {}, entries: {}, gather_steps: gatherSteps(), hunt_levels: {}};
  document.querySelectorAll("[data-option]").forEach(input => config.options[input.dataset.option] = input.checked);
  document.querySelectorAll("[data-entry]").forEach(input => config.entries[input.dataset.entry] = input.value);
  document.querySelectorAll("[data-hunt-level]").forEach(input => config.hunt_levels[input.dataset.huntLevel] = input.value);
  return config;
}

function loadConfig(config) {
  for (const [name, value] of Object.entries(config.options)) {
    const input = $("option-" + name);
    if (input) input.checked = value;
  }
  for (const [name, value] of Object.entries(config.entries)) $("entry-" + name).value = value;
  renderGatherSteps(config.gather_steps);
  for (const [name, value] of Object.entries(config.hunt_levels)) $("hunt-level-" + name).value = value;
  dependencies();
}

function dependencies() {
  const on = name => $("option-" + name).checked;
  document.querySelectorAll("[data-option], [data-entry], [data-gather-resource], [data-gather-level], [data-gather-action], [data-hunt-level]").forEach(input => input.disabled = running || !connected);
  if (on("help_clicker")) {
    document.querySelectorAll("[data-option], [data-entry], [data-gather-resource], [data-gather-level], [data-gather-action], [data-hunt-level]").forEach(input => {
      if (!["help_clicker", "bear", "reconnect"].includes(input.dataset.option) && input.dataset.entry !== "reconnect_minutes") input.disabled = true;
    });
  }
  if (on("bear")) {
    document.querySelectorAll("[data-option], [data-entry], [data-gather-resource], [data-gather-level], [data-gather-action], [data-hunt-level]").forEach(input => {
      if (!["bear", "bear_drive", "bear_follow", "bear_follow_no_heroes", "help_clicker", "reconnect"].includes(input.dataset.option) && input.dataset.entry !== "reconnect_minutes") input.disabled = true;
    });
  }
  for (const [name] of bearModes) $("option-" + name).disabled ||= !on("bear");
  for (const name of ["upgrade", "shield", "spear", "archer"]) $("option-" + name).disabled ||= !on("train");
  $("entry-tree_threshold").disabled ||= !on("tree");
  $("entry-reconnect_minutes").disabled ||= !on("reconnect");
  $("option-bounty").disabled ||= !on("intelligence");
  $("entry-stamina_threshold").disabled ||= !on("intelligence");
  $("option-gather_full_only").disabled ||= !on("gather");
  resourceList.querySelectorAll("select, button").forEach(input => input.disabled ||= !on("gather"));
  for (const [name] of monsters) {
    $("option-" + name).disabled ||= !on("hunt");
    $("hunt-level-" + name).disabled ||= !on("hunt") || !on(name);
  }
}

function queue(operation) {
  mutation = mutation.then(operation).catch(error => notice(error.message || error, "无法执行"));
  return mutation;
}

function syncConfig() {
  dependencies();
  clearTimeout(syncTimer);
  const config = formConfig();
  syncTimer = setTimeout(() => queue(() => window.pywebview.api.update_config(config)), 120);
}

$("help-clicker-confirm").addEventListener("close", () => {
  $("option-help_clicker").checked = $("help-clicker-confirm").returnValue === "yes" && connected && !running;
  if ($("option-help_clicker").checked) $("option-bear").checked = false;
  syncConfig();
  $("option-help_clicker").focus();
});

document.querySelector(".cards").addEventListener("change", event => {
  if (event.target.id === "option-bear_follow" && event.target.checked) $("option-bear_follow_no_heroes").checked = false;
  if (event.target.id === "option-bear_follow_no_heroes" && event.target.checked) $("option-bear_follow").checked = false;
  if (event.target.id === "option-bear" && event.target.checked) $("option-help_clicker").checked = false;
  if (event.target.id === "option-help_clicker" && event.target.checked) {
    event.target.checked = false;
    $("help-clicker-confirm").returnValue = "";
    $("help-clicker-confirm-message").textContent = helpClickerHint;
    $("help-clicker-confirm").showModal();
    return;
  }
  if (monsters.some(([name]) => event.target.id === "option-" + name) && !monsters.some(([name]) => $("option-" + name).checked)) {
    event.target.checked = true;
    notice("至少要勾选一项打怪目标", "自动打怪");
  }
  if (bearModes.some(([name]) => event.target.id === "option-" + name) && !bearModes.some(([name]) => $("option-" + name).checked)) {
    event.target.checked = true;
    notice("自动打熊至少要勾选一项", "自动打熊");
  }
  syncConfig();
});

for (const id of ["log-lines", "error-lines"]) {
  const box = $(id);
  box.addEventListener("mousedown", event => {
    if (event.button === 0) box.focus({ preventScroll: true });
  });
  box.addEventListener("keydown", event => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "a") return;
    event.preventDefault();
    event.stopPropagation();
    const range = document.createRange();
    range.selectNodeContents(box);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

function appendLines(box, lines) {
  if (!lines.length) return;
  const selection = window.getSelection();
  const selecting = selection && !selection.isCollapsed && box.contains(selection.anchorNode);
  const fragment = document.createDocumentFragment();
  for (const line of lines) {
    const node = document.createElement("div");
    node.className = "log-entry" + (line.error ? " error" : line.warning ? " warning" : "");
    const screenshot = line.error && /失败截图：(.+?\.png)(?=；|$)/i.exec(line.text);
    if (screenshot) {
      const start = screenshot.index + "失败截图：".length;
      const link = document.createElement("a");
      link.className = "screenshot-link";
      link.href = "#";
      link.textContent = screenshot[1];
      link.title = "打开异常截图";
      link.addEventListener("click", async event => {
        event.preventDefault();
        try { await window.pywebview.api.open_screenshot(screenshot[1]); }
        catch (error) { notice(error.message || String(error), "打开截图失败"); }
      });
      node.append(document.createTextNode(line.text.slice(0, start)), link,
        document.createTextNode(line.text.slice(start + screenshot[1].length)));
    } else node.textContent = line.text;
    fragment.append(node);
  }
  box.append(fragment);
  if (!selecting) box.scrollTop = box.scrollHeight;
}

function render(state) {
  running = state.running;
  renderUpdate(state.update);
  renderTaskStatus(state);
  renderDevices(state);
  if (selected !== state.selected) {
    selected = state.selected;
    loadConfig(state.config);
  }
  $("refresh").disabled = state.scanning || !connected;
  $("refresh").title = "重新读取模拟器、USB 真机及连接状态";
  $("device-status").textContent = state.device_status;
  $("run-status").textContent = state.stopping ? "● 正在停止" : running ? "● 运行中" : "● 未运行";
  $("run-status").style.color = running ? (state.stopping ? "var(--red)" : "var(--green)") : "var(--muted)";
  $("run-label").textContent = state.stopping ? "正在停止…" : running ? "停止" : "启动";
  $("run").classList.toggle("running", running);
  $("run").disabled = !connected || state.stopping || (!running && (state.scanning || !selected));
  $("run").querySelector(".play").hidden = running;
  dependencies();
  if (state.log_reset || (state.log_key !== undefined && state.log_key !== logKey)) {
    $("log-lines").replaceChildren(); $("error-lines").replaceChildren();
    $("errors").hidden = true;
    lastLog = 0;
  }
  if (state.log_key !== undefined) logKey = state.log_key;
  const logs = state.logs.filter(line => line.id > lastLog);
  if (logs.length) {
    $("empty-log").hidden = true;
    $("log-lines").hidden = false;
    appendLines($("log-lines"), logs);
    const errors = logs.filter(line => line.error);
    if (errors.length) { $("errors").hidden = false; appendLines($("error-lines"), errors); }
  }
  lastLog = Math.max(lastLog, state.last_log);
  if (state.notice && state.notice.id > lastNotice) {
    lastNotice = state.notice.id;
    notice(state.notice.message, "程序异常");
  }
}

function renderUpdate(update) {
  if (!update) return;
  updateState = update;
  const busy = ["checking", "downloading", "cancelling", "ready", "installing"].includes(update.phase);
  const hasUpdate = Boolean(update.new_version);
  $("app-version").textContent = "v" + update.version;
  $("check-updates").disabled = !connected || busy;
  $("check-updates").title = update.phase === "error" && !hasUpdate ? update.message + "；点击重试" : "点击检查更新";
  $("new-version").hidden = !hasUpdate;
  $("new-version").disabled = !connected;
  $("new-version").setAttribute("aria-label", hasUpdate ? "查看新版本 v" + update.new_version : "新版本");
  $("update-current").textContent = "当前 v" + update.version;
  $("update-version").textContent = hasUpdate ? "v" + update.new_version : "";
  const notes = update.notes || "该版本未提供更新说明";
  if ($("update-notes").textContent !== notes) $("update-notes").textContent = notes;
  $("update-message").hidden = !["downloading", "cancelling", "ready", "installing", "error"].includes(update.phase);
  $("update-message").textContent = update.message;
  $("update-message").classList.toggle("error", update.phase === "error");
  $("update-progress").hidden = update.phase !== "downloading";
  $("update-progress").value = update.total ? Math.min(100, update.downloaded * 100 / update.total) : 0;
  const ready = update.phase === "ready";
  $("update-action").disabled = updateRequestPending || !connected || !update.installable || !hasUpdate || !(ready || ["available", "error"].includes(update.phase));
  $("update-action").title = update.installable ? "" : "当前运行目录不支持自动安装，请使用完整发行包";
  $("update-action").textContent = ready ? "安装并重启" : update.phase === "installing" ? "正在安装…" : update.phase === "downloading" ? "正在更新…" : "更新";
  $("update-cancel").disabled = updateRequestPending || ["cancelling", "installing"].includes(update.phase);
}

$("new-version").addEventListener("click", () => {
  if (connected && updateState?.new_version && !$("update-dialog").open) $("update-dialog").showModal();
});
function dismissUpdate() {
  if ($("update-cancel").disabled) return;
  if (updateState?.phase === "downloading") queue(async () => render(await window.pywebview.api.cancel_update_download()));
  $("update-dialog").close();
}
$("update-cancel").addEventListener("click", dismissUpdate);
$("update-dialog").addEventListener("cancel", event => { event.preventDefault(); dismissUpdate(); });
$("update-dialog").addEventListener("close", () => { if (!$("new-version").hidden) $("new-version").focus(); });
$("check-updates").addEventListener("click", () => {
  $("check-updates").disabled = true;
  queue(async () => render(await window.pywebview.api.check_updates()));
});
$("update-action").addEventListener("click", () => {
  if (updateRequestPending || $("update-action").disabled) return;
  const install = updateState?.phase === "ready";
  updateRequestPending = true;
  renderUpdate(updateState);
  queue(async () => {
    try {
      if (install) {
        await window.pywebview.api.install_update();
        renderUpdate({...updateState, phase: "installing", message: "正在停止任务并安装更新，完成后自动打开新版…"});
      } else render(await window.pywebview.api.download_update());
    } finally {
      updateRequestPending = false;
      renderUpdate(updateState);
    }
  });
});

$("device").addEventListener("click", () => {
  const open = $("device-list").hidden;
  $("device-list").hidden = !open;
  $("device").setAttribute("aria-expanded", String(open));
  if (open) $("device-list").querySelector(".selected .device-choice, .device-choice")?.focus();
});
document.addEventListener("click", event => {
  if (!event.target.closest(".device-picker")) closeDevices();
});
$("device-list").addEventListener("keydown", event => {
  if (event.key === "Escape") { event.preventDefault(); closeDevices(true); }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const buttons = [...$("device-list").querySelectorAll("button:not(:disabled)")];
  const index = buttons.indexOf(document.activeElement);
  const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
    (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
  buttons[next]?.focus();
});
$("device-list").addEventListener("focusout", () => {
  setTimeout(() => { if (!document.activeElement.closest(".device-picker")) closeDevices(); }, 0);
});
$("refresh").addEventListener("click", () => {
  clearTimeout(syncTimer);
  const config = formConfig();
  closeDevices();
  queue(async () => { if (!running) await window.pywebview.api.update_config(config); render(await window.pywebview.api.refresh_devices()); });
});
$("run").addEventListener("click", () => {
  clearTimeout(syncTimer);
  const config = formConfig(), stopping = running;
  queue(async () => render(await (stopping ? window.pywebview.api.stop() : window.pywebview.api.start(config))));
});

async function poll() {
  if (!connected) return;
  try { render(await window.pywebview.api.get_state(lastLog)); }
  catch (error) { $("device-status").textContent = error.message || String(error); }
  setTimeout(poll, 200);
}

async function initialize() {
  if (connected) return;
  connected = true;
  try {
    render(await window.pywebview.api.get_state());
    render(await window.pywebview.api.refresh_devices());
  } catch (error) { notice(error.message || error, "界面初始化失败"); }
  poll();
}
dependencies();
window.addEventListener("gamebot-backend-error", event => {
  connected = false;
  dependencies();
  $("run").disabled = true;
  $("refresh").disabled = true;
  $("device").disabled = true;
  $("check-updates").disabled = true;
  $("new-version").disabled = true;
  $("update-action").disabled = true;
  closeDevices();
  $("device-status").textContent = "Python 后台已断开";
  notice(event.detail, "后台异常");
});
window.addEventListener("pywebviewready", initialize, {once: true});
if (window.pywebview && window.pywebview.api) initialize();
