const state = {
  tasks: [],
  selectedTaskId: null,
  uploadNumbers: [],
  debounce: null,
  selectedPhones: new Set(),
  latestResults: [],
};

const $ = (id) => document.getElementById(id);

function toast(message, type = "info") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("toast-stack").appendChild(node);
  setTimeout(() => node.remove(), 3200);
}

function badge(status) {
  const text = (status || "unknown").replaceAll("_", " ");
  return `<span class="badge ${status}">${text}</span>`;
}

function taskProgress(task) {
  const total = Number(task?.total_numbers || 0);
  const completed = Number(task?.completed_numbers || 0);
  return total ? Math.round((completed / total) * 100) : 0;
}

function activeTask() {
  return state.tasks.find((task) => ["running", "queued", "paused"].includes(task.status)) || state.tasks[0] || null;
}

function setView(id) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === id));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === id));
  const titles = {
    dashboard: ["Dashboard", "Live crawler operations"],
    "new-task": ["New Task", "Upload a TXT or CSV list"],
    results: ["All Results", "Review task-level collection output"],
    history: ["Task History", "Manage previous collection tasks"],
    manual: ["Manual Tools", "Single and range collection"],
  };
  $("page-title").textContent = titles[id][0];
  $("page-subtitle").textContent = titles[id][1];
}

function openResults(status = "") {
  $("status-filter").value = status;
  setView("results");
  loadResults().catch((err) => toast(err.message, "error"));
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = "Request failed";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

async function loadTasks() {
  const payload = await api("/api/tasks");
  state.tasks = payload.data || [];
  if (!state.selectedTaskId && state.tasks.length) state.selectedTaskId = state.tasks[0].id;
  await loadBrowserStatus();
  renderDashboard();
  renderTaskSelectors();
  renderHistory();
  await loadResults();
}

async function loadBrowserStatus() {
  try {
    const data = await api("/api/browser/status");
    $("browser-state").textContent = data.state.replaceAll("_", " ");
    $("browser-message").textContent = data.message;
  } catch {
    $("browser-state").textContent = "Unknown";
    $("browser-message").textContent = "Browser status could not be read.";
  }
}

function renderDashboard() {
  const current = activeTask();
  if (current) {
    $("active-task-name").textContent = current.name;
    $("active-task-detail").textContent = `${current.status} | ${current.speed} | ${current.total_numbers} numbers`;
    $("progress-label").textContent = `${current.completed_numbers} of ${current.total_numbers} completed`;
    const percent = taskProgress(current);
    $("progress-percent").textContent = `${percent}%`;
    $("progress-fill").style.width = `${percent}%`;
    $("current-phone").textContent = `Current number: ${current.current_phone || "none"}`;
    $("metric-total").textContent = current.total_numbers;
    $("metric-completed").textContent = current.completed_numbers;
    $("metric-success").textContent = current.success_count;
    $("metric-hidden").textContent = current.hidden_dp_count;
    $("metric-no-wa").textContent = current.no_whatsapp_count;
    $("metric-error").textContent = current.error_count;
  } else {
    $("active-task-name").textContent = "No active task";
    $("active-task-detail").textContent = "Create or resume a task to begin collection.";
    $("progress-label").textContent = "0 of 0 completed";
    $("progress-percent").textContent = "0%";
    $("progress-fill").style.width = "0%";
    $("current-phone").textContent = "Current number: none";
    ["metric-total", "metric-completed", "metric-success", "metric-hidden", "metric-no-wa", "metric-error"].forEach((id) => {
      $(id).textContent = "0";
    });
  }

  const list = $("task-list");
  if (!state.tasks.length) {
    list.innerHTML = `<div class="task-card"><h3>No tasks yet</h3><small>Create a task from New Task or Manual Tools.</small></div>`;
    return;
  }
  list.innerHTML = state.tasks.map((task) => {
    const percent = taskProgress(task);
    const selected = Number(task.id) === Number(state.selectedTaskId) ? "selected" : "";
    const canPause = task.status === "running" || task.status === "queued";
    const canResume = task.status === "paused";
    const canCancel = ["running", "queued", "paused"].includes(task.status);
    return `
      <article class="task-card ${selected}" data-task-id="${task.id}">
        <div class="task-title-row">
          <div>
            <h3>${escapeHtml(task.name)}</h3>
            <small>${task.completed_numbers}/${task.total_numbers} completed</small>
          </div>
          ${badge(task.status)}
        </div>
        <div class="mini-progress"><div style="width:${percent}%"></div></div>
        <small>${task.source_type} | ${task.speed} | created ${new Date(`${task.created_at}Z`).toLocaleString()}</small>
        <div class="task-actions">
          <button class="task-action primary" data-action="open" data-task-id="${task.id}">Open Results</button>
          <button class="task-action" data-action="manage" data-task-id="${task.id}">Manage</button>
          ${canPause ? `<button class="task-action" data-action="pause" data-task-id="${task.id}">Pause</button>` : ""}
          ${canResume ? `<button class="task-action" data-action="resume" data-task-id="${task.id}">Resume</button>` : ""}
          ${canCancel ? `<button class="task-action danger-soft" data-action="cancel" data-task-id="${task.id}">Cancel</button>` : ""}
          <button class="task-action danger" data-action="delete" data-task-id="${task.id}">Delete</button>
        </div>
      </article>
    `;
  }).join("");
  list.querySelectorAll(".task-card").forEach((node) => {
    node.addEventListener("click", () => {
      state.selectedTaskId = Number(node.dataset.taskId);
      renderDashboard();
      renderTaskSelectors();
      loadResults();
    });
  });
  list.querySelectorAll(".task-action").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      handleTaskCardAction(button.dataset.action, Number(button.dataset.taskId)).catch((err) => toast(err.message, "error"));
    });
  });
}

function renderHistory() {
  const body = $("history-body");
  if (!body) return;
  if (!state.tasks.length) {
    body.innerHTML = `<tr><td colspan="7">No task history yet.</td></tr>`;
    return;
  }
  body.innerHTML = state.tasks.map((task) => {
    const started = task.started_at ? new Date(`${task.started_at}Z`) : null;
    const finished = task.finished_at ? new Date(`${task.finished_at}Z`) : null;
    const duration = started && finished ? formatDuration((finished - started) / 1000) : started ? "Running" : "-";
    return `
      <tr>
        <td>
          <strong>${escapeHtml(task.name)}</strong><br>
          <small>${new Date(`${task.created_at}Z`).toLocaleString()}</small>
        </td>
        <td>${badge(task.status)}</td>
        <td>${escapeHtml(task.source_type)}</td>
        <td>${escapeHtml(task.speed)}</td>
        <td>${task.completed_numbers}/${task.total_numbers}</td>
        <td>${duration}</td>
        <td>
          <div class="row-actions">
            <button class="task-action primary" data-history-action="open" data-task-id="${task.id}">Open</button>
            <button class="task-action" data-history-action="rename" data-task-id="${task.id}">Rename</button>
            <button class="task-action danger" data-history-action="delete" data-task-id="${task.id}">Delete</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
  body.querySelectorAll("[data-history-action]").forEach((button) => {
    button.addEventListener("click", () => {
      handleTaskCardAction(button.dataset.historyAction, Number(button.dataset.taskId)).catch((err) => toast(err.message, "error"));
    });
  });
}

async function handleTaskCardAction(action, taskId) {
  state.selectedTaskId = taskId;
  if (action === "open") {
    renderDashboard();
    renderTaskSelectors();
    openResults("");
    return;
  }
  if (action === "manage") {
    renderDashboard();
    renderTaskSelectors();
    toast("Task selected. Use the controls above to pause, resume, or cancel.", "info");
    return;
  }
  if (action === "delete") {
    const task = state.tasks.find((item) => Number(item.id) === Number(taskId));
    if (!confirm(`Delete task "${task?.name || taskId}" and its results?`)) return;
    await api(`/api/tasks/${taskId}`, { method: "DELETE" });
    if (Number(state.selectedTaskId) === Number(taskId)) state.selectedTaskId = null;
    toast("Task deleted", "success");
    await loadTasks();
    return;
  }
  if (action === "rename") {
    const task = state.tasks.find((item) => Number(item.id) === Number(taskId));
    const name = prompt("Rename task", task?.name || "");
    if (!name || !name.trim()) return;
    await api(`/api/tasks/${taskId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    toast("Task renamed", "success");
    await loadTasks();
    return;
  }
  await api(`/api/tasks/${taskId}/${action}`, { method: "POST" });
  toast(`Task ${action} requested`, "success");
  await loadTasks();
}

function renderTaskSelectors() {
  const select = $("results-task");
  if (!state.tasks.length) {
    select.innerHTML = `<option>No tasks</option>`;
    return;
  }
  select.innerHTML = state.tasks.map((task) => (
    `<option value="${task.id}" ${Number(task.id) === Number(state.selectedTaskId) ? "selected" : ""}>${escapeHtml(task.name)}</option>`
  )).join("");
}

async function loadResults() {
  const taskId = state.selectedTaskId;
  const body = $("results-body");
  if (!taskId) {
    body.innerHTML = `<tr><td colspan="8">No task selected.</td></tr>`;
    return;
  }
  const params = new URLSearchParams({
    limit: "100",
    status: $("status-filter").value,
    search: $("result-search").value.trim(),
  });
  const payload = await api(`/api/tasks/${taskId}/results?${params}`);
  state.latestResults = payload.data || [];
  state.selectedPhones.clear();
  $("select-all-results").checked = false;
  if (!payload.data.length) {
    body.innerHTML = `<tr><td colspan="8">No results for this task.</td></tr>`;
    return;
  }
  body.innerHTML = payload.data.map((row) => `
    <tr>
      <td><input class="result-check" type="checkbox" data-phone="${escapeHtml(row.phone)}"></td>
      <td>${row.display_image_url ? `<img class="avatar" src="${row.display_image_url}" alt="">` : `<div class="avatar">-</div>`}</td>
      <td class="phone-cell">${escapeHtml(row.phone)}</td>
      <td>${escapeHtml(row.name || "-")}</td>
      <td>${badge(row.status)}</td>
      <td>${new Date(`${row.updated_at}Z`).toLocaleString()}</td>
      <td>${escapeHtml(row.error_msg || "")}</td>
      <td><button class="task-action primary details-btn" data-result-id="${row.id}">View Details</button></td>
    </tr>
  `).join("");
  body.querySelectorAll(".result-check").forEach((check) => {
    check.addEventListener("change", () => {
      if (check.checked) state.selectedPhones.add(check.dataset.phone);
      else state.selectedPhones.delete(check.dataset.phone);
    });
  });
  body.querySelectorAll(".details-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const row = state.latestResults.find((item) => String(item.id) === String(button.dataset.resultId));
      if (row) showDetails(row);
    });
  });
}

function showDetails(row) {
  $("details-phone").textContent = row.phone;
  $("details-image").src = row.display_image_url || "";
  $("details-image").style.display = row.display_image_url ? "block" : "none";
  $("cover-wrap").style.display = row.cover_image_url ? "block" : "none";
  $("details-cover").src = row.cover_image_url || "";
  const details = row.details || {};
  const publicText = Array.isArray(details.public_text) ? details.public_text : [];
  const links = Array.isArray(details.links) ? details.links : [];
  const rows = [
    ["Phone", row.phone],
    ["Name", row.name || "-"],
    ["Status", row.status],
    ["About", row.about || "-"],
    ["Updated", new Date(`${row.updated_at}Z`).toLocaleString()],
    ["Error", row.error_msg || "-"],
  ];
  $("details-content").innerHTML = `
    <div class="detail-grid">
      ${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
    <h3>Public Profile Text</h3>
    <div class="detail-list">${publicText.length ? publicText.map((item) => `<div>${escapeHtml(item)}</div>`).join("") : "<div>No extra public text captured.</div>"}</div>
    <h3>Links</h3>
    <div class="detail-list">${links.length ? links.map((item) => `<div>${escapeHtml(item.text || item.href)}<br><small>${escapeHtml(item.href || "")}</small></div>`).join("") : "<div>No public links captured.</div>"}</div>
  `;
  $("details-modal").hidden = false;
}

async function previewFile(file) {
  const data = new FormData();
  data.append("file", file);
  $("file-state").textContent = "Parsing";
  const payload = await api("/api/tasks/preview", { method: "POST", body: data });
  state.uploadNumbers = payload.numbers || [];
  $("file-state").textContent = file.name;
  $("preview-count").textContent = `${payload.total} numbers`;
  $("duplicates-count").textContent = payload.duplicates_removed;
  $("invalid-count").textContent = payload.invalid_rows;
  $("column-name").textContent = payload.detected_column || "-";
  $("collect-data").disabled = state.uploadNumbers.length === 0;
  $("preview-list").classList.toggle("empty", state.uploadNumbers.length === 0);
  $("preview-list").innerHTML = payload.preview.length
    ? payload.preview.map((phone) => `<div>${escapeHtml(phone)}</div>`).join("")
    : "No valid numbers found.";
}

async function createUploadTask() {
  const name = $("task-name").value.trim();
  if (!name || !state.uploadNumbers.length) return;
  const payload = {
    name,
    numbers: state.uploadNumbers,
    speed: $("speed-select").value,
    skip_checked: $("skip-checked").checked,
    max_errors: Number($("max-errors").value || 0),
  };
  const estimate = estimateDuration(state.uploadNumbers.length, payload.speed);
  const ok = confirm(
    `Start task "${name}"?\n\nNumbers: ${state.uploadNumbers.length}\nSpeed: ${payload.speed}\nEstimated time: ${estimate}\nStop after errors: ${payload.max_errors || "No limit"}`
  );
  if (!ok) return;
  const result = await api("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.selectedTaskId = result.task.id;
  toast("Task started", "success");
  await loadTasks();
  setView("dashboard");
}

async function taskAction(action) {
  const task = activeTask();
  if (!task) return;
  await api(`/api/tasks/${task.id}/${action}`, { method: "POST" });
  toast(`Task ${action} requested`, "success");
  await loadTasks();
}

async function createSingle() {
  const phone = $("single-phone").value.trim();
  if (!phone) return;
  const result = await api("/api/crawl/single", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone, force: true }),
  });
  state.selectedTaskId = result.task.id;
  toast("Single lookup started", "success");
  await loadTasks();
  setView("dashboard");
}

async function createRange() {
  const payload = {
    name: $("range-name").value.trim() || "Range crawl",
    prefix: $("range-prefix").value.trim(),
    start: $("range-start").value.trim(),
    end: $("range-end").value.trim(),
    speed: "balanced",
    skip_checked: true,
  };
  const result = await api("/api/crawl/range", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.selectedTaskId = result.task.id;
  toast("Range task started", "success");
  await loadTasks();
  setView("dashboard");
}

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onopen = () => {
    $("live-dot").className = "live-dot connected";
    $("live-label").textContent = "Live";
  };
  source.onerror = () => {
    $("live-dot").className = "live-dot disconnected";
    $("live-label").textContent = "Disconnected";
  };
  ["task_created", "task_updated", "task_progress", "task_completed", "task_error", "task_deleted"].forEach((event) => {
    source.addEventListener(event, async () => {
      await loadTasks();
    });
  });
}

function formatDuration(seconds) {
  const sec = Math.max(0, Math.round(seconds));
  const min = Math.floor(sec / 60);
  const hr = Math.floor(min / 60);
  if (hr) return `${hr}h ${min % 60}m`;
  if (min) return `${min}m ${sec % 60}s`;
  return `${sec}s`;
}

function estimateDuration(count, speed) {
  const perNumber = speed === "safe" ? 18 : speed === "fast" ? 7 : 11;
  return formatDuration(count * perNumber);
}

async function copySelectedPhones() {
  if (!state.selectedPhones.size) return toast("No visible results selected", "error");
  await navigator.clipboard.writeText([...state.selectedPhones].join("\n"));
  toast("Selected phones copied", "success");
}

async function retryErrors() {
  if (!state.selectedTaskId) return;
  await api(`/api/tasks/${state.selectedTaskId}/retry-errors`, { method: "POST" });
  toast("Error results queued for retry", "success");
  await loadTasks();
}

async function retrySelected() {
  if (!state.selectedPhones.size) return toast("No visible results selected", "error");
  const task = state.tasks.find((item) => Number(item.id) === Number(state.selectedTaskId));
  const result = await api("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `Retry selected - ${task?.name || "task"}`,
      numbers: [...state.selectedPhones],
      speed: "balanced",
      skip_checked: false,
      max_errors: 0,
    }),
  });
  state.selectedTaskId = result.task.id;
  toast("Selected numbers queued as a new task", "success");
  await loadTasks();
  setView("dashboard");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bind() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => setView(item.dataset.view));
  });
  $("refresh-tasks").addEventListener("click", loadTasks);
  $("refresh-history").addEventListener("click", loadTasks);
  $("active-pause").addEventListener("click", () => taskAction("pause"));
  $("active-resume").addEventListener("click", () => taskAction("resume"));
  $("active-cancel").addEventListener("click", () => taskAction("cancel"));
  $("collect-data").addEventListener("click", () => createUploadTask().catch((err) => toast(err.message, "error")));
  $("single-run").addEventListener("click", () => createSingle().catch((err) => toast(err.message, "error")));
  $("range-run").addEventListener("click", () => createRange().catch((err) => toast(err.message, "error")));
  $("results-task").addEventListener("change", (event) => {
    state.selectedTaskId = Number(event.target.value);
    renderDashboard();
    loadResults();
  });
  $("status-filter").addEventListener("change", loadResults);
  $("result-search").addEventListener("input", () => {
    clearTimeout(state.debounce);
    state.debounce = setTimeout(loadResults, 250);
  });
  $("export-results").addEventListener("click", () => {
    if (!state.selectedTaskId) return;
    const params = new URLSearchParams({
      status: $("status-filter").value,
      search: $("result-search").value.trim(),
    });
    location.href = `/api/tasks/${state.selectedTaskId}/export.csv?${params}`;
  });
  $("copy-selected").addEventListener("click", () => copySelectedPhones().catch((err) => toast(err.message, "error")));
  $("retry-selected").addEventListener("click", () => retrySelected().catch((err) => toast(err.message, "error")));
  $("retry-errors").addEventListener("click", () => retryErrors().catch((err) => toast(err.message, "error")));
  $("select-all-results").addEventListener("change", (event) => {
    document.querySelectorAll(".result-check").forEach((check) => {
      check.checked = event.target.checked;
      if (check.checked) state.selectedPhones.add(check.dataset.phone);
      else state.selectedPhones.delete(check.dataset.phone);
    });
  });
  $("details-close").addEventListener("click", () => {
    $("details-modal").hidden = true;
  });
  $("details-modal").addEventListener("click", (event) => {
    if (event.target.id === "details-modal") $("details-modal").hidden = true;
  });
  document.querySelectorAll("[data-result-filter]").forEach((metric) => {
    metric.addEventListener("click", () => openResults(metric.dataset.resultFilter || ""));
  });

  const dropzone = $("dropzone");
  const fileInput = $("file-input");
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) previewFile(fileInput.files[0]).catch((err) => toast(err.message, "error"));
  });
  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files[0];
    if (file) previewFile(file).catch((err) => toast(err.message, "error"));
  });
}

bind();
connectEvents();
loadTasks().catch((err) => toast(err.message, "error"));
