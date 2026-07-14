"use strict";

// =============================================================================
// AI model library (⚙ -> «Налаштування ШІ моделі»).
//
// Upload new YOLO weights, switch the active model, rename/delete, and see what
// state the detector is actually in. A .pt is converted to ONNX server-side by
// the exporter sidecar (asynchronously — the row goes pending -> converting ->
// ready, and this panel polls until it settles); a ready .onnx is usable at once.
//
// Switching the active model HOT-SWAPS it via AI.setModel() — no page reload, so
// the video and the 20 Hz control heartbeat are never interrupted.
//
// AI CUSTOM (pixel motion) is model-free and untouched by any of this.
// =============================================================================

(() => {
  const listEl = document.getElementById("model-list");
  if (!listEl) return;

  const fileEl = document.getElementById("model-file");
  const nameEl = document.getElementById("model-name");
  const classesEl = document.getElementById("model-classes");
  const uploadBtn = document.getElementById("model-upload-btn");
  const progressEl = document.getElementById("model-progress");
  const barEl = document.getElementById("model-progress-bar");
  const msgEl = document.getElementById("model-upload-msg");

  const stateModelEl = document.getElementById("ai-state-model");
  const stateEngineEl = document.getElementById("ai-state-engine");
  const stateExporterEl = document.getElementById("ai-state-exporter");

  const POLL_BUSY_MS = 2000;   // while a conversion is running
  const POLL_IDLE_MS = 30000;  // otherwise: just keeps the exporter dot honest

  const STATUS_TEXT = {
    pending: "у черзі",
    converting: "конвертується…",
    ready: "готова",
    error: "помилка",
  };

  let models = [];
  let activeId = (window.__AI__ && window.__AI__.model && window.__AI__.model.id) || null;
  let pollTimer = null;

  // ------------------------------------------------------------------ helpers
  function sizeText(bytes) {
    if (!bytes) return "—";
    return (bytes / (1024 * 1024)).toFixed(1) + " МБ";
  }

  function dateText(iso) {
    const date = new Date(iso);
    return isNaN(date) ? "" : date.toLocaleDateString("uk-UA");
  }

  function classesText(classes) {
    const names = Object.values(classes || {});
    return names.length ? names.join(", ") : "без класів";
  }

  // -------------------------------------------------------------------- render
  function render() {
    listEl.textContent = "";
    if (!models.length) {
      const empty = document.createElement("div");
      empty.className = "sp-note";
      empty.textContent = "Моделей немає. Завантажте .pt або .onnx.";
      listEl.appendChild(empty);
      return;
    }

    for (const model of models) {
      const row = document.createElement("div");
      row.className = "model-row" + (model.id === activeId ? " active" : "");

      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "active-model";
      radio.checked = model.id === activeId;
      // Only a ready model has weights to load.
      radio.disabled = model.status !== "ready";
      radio.addEventListener("change", () => activate(model.id));

      const body = document.createElement("div");
      body.className = "model-body";

      const title = document.createElement("div");
      title.className = "model-name";
      title.textContent = model.name + (model.builtin ? " (базова)" : "");

      const meta = document.createElement("div");
      meta.className = "model-meta";
      const status = document.createElement("span");
      status.className = "model-status " + model.status;
      status.textContent = STATUS_TEXT[model.status] || model.status;
      meta.appendChild(status);
      const rest = document.createElement("span");
      rest.textContent =
        model.status === "error"
          ? " " + model.error
          : ` ${sizeText(model.size_bytes)} · ${model.imgsz}px · ${classesText(model.classes)} · ${dateText(model.created_at)}`;
      meta.appendChild(rest);

      body.appendChild(title);
      body.appendChild(meta);

      const actions = document.createElement("div");
      actions.className = "model-actions";
      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.title = "Перейменувати";
      renameBtn.textContent = "✎";
      renameBtn.addEventListener("click", () => rename(model));
      actions.appendChild(renameBtn);
      // The builtin model is the fallback and can never be removed; the server
      // enforces this too (ModelStore.delete).
      if (!model.builtin) {
        const delBtn = document.createElement("button");
        delBtn.type = "button";
        delBtn.title = "Видалити";
        delBtn.textContent = "×";
        delBtn.addEventListener("click", () => remove(model));
        actions.appendChild(delBtn);
      }

      row.appendChild(radio);
      row.appendChild(body);
      row.appendChild(actions);
      listEl.appendChild(row);
    }
  }

  function renderState(exporterOnline) {
    const active = models.find((m) => m.id === activeId);
    if (stateModelEl) {
      stateModelEl.textContent = active ? `${active.name} — ${STATUS_TEXT[active.status]}` : "немає активної";
      stateModelEl.className = "ai-state-val " + (active && active.status === "ready" ? "ok" : "bad");
    }
    if (stateExporterEl && typeof exporterOnline === "boolean") {
      stateExporterEl.textContent = exporterOnline ? "доступний" : "недоступний (.pt не конвертувати)";
      stateExporterEl.className = "ai-state-val " + (exporterOnline ? "ok" : "bad");
    }
  }

  // The engine readout is pushed by ai.js, not polled: it is the browser's own
  // ONNX Runtime state, and a failed model load used to be invisible outside the
  // console (the #ai badge it wrote to no longer exists).
  if (window.AI && window.AI.onEngine && stateEngineEl) {
    window.AI.onEngine((engine) => {
      const text = {
        idle: "не запущено (клавіша I)",
        loading: "завантаження моделі…",
        running: `працює — ${engine.fps.toFixed(1)} к/с, ${engine.ms} мс`,
        error: "помилка: " + engine.error,
      }[engine.state];
      stateEngineEl.textContent = text || engine.state;
      stateEngineEl.className =
        "ai-state-val " + (engine.state === "running" ? "ok" : engine.state === "error" ? "bad" : "");
    });
  }

  // ------------------------------------------------------------------ requests
  async function refresh() {
    try {
      const res = await fetch("/api/models");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      models = data.models || [];
      activeId = data.active;
      render();
      renderState(data.exporter_online);
      schedulePoll(models.some((m) => m.status === "pending" || m.status === "converting"));
    } catch (err) {
      listEl.textContent = "";
      const note = document.createElement("div");
      note.className = "sp-note";
      note.textContent = "Не вдалося отримати список моделей.";
      listEl.appendChild(note);
      schedulePoll(false);
    }
  }

  function schedulePoll(busy) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refresh, busy ? POLL_BUSY_MS : POLL_IDLE_MS);
  }

  async function activate(id) {
    const res = await fetch(`/api/models/${id}/activate`, { method: "POST" });
    if (!res.ok) {
      note("Не вдалося активувати модель.", true);
      refresh();
      return;
    }
    const model = await res.json();
    activeId = model.id;
    render();
    renderState();
    // Hot swap: the detector picks up the new weights without a page reload.
    if (window.AI && window.AI.setModel) window.AI.setModel(model);
  }

  async function rename(model) {
    const name = window.prompt("Назва моделі", model.name);
    if (name === null) return;
    await fetch(`/api/models/${model.id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    refresh();
  }

  async function remove(model) {
    if (!window.confirm(`Видалити модель «${model.name}»?`)) return;
    const res = await fetch(`/api/models/${model.id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      note(body.error || "Не вдалося видалити модель.", true);
    }
    refresh();
  }

  function note(text, isError) {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.classList.toggle("bad", !!isError);
  }

  // -------------------------------------------------------------------- upload
  // XHR rather than fetch: weights are tens of MB and upload progress is the only
  // feedback the operator gets before the (much longer) conversion starts.
  function upload() {
    const file = fileEl.files[0];
    if (!file) return;

    const form = new FormData();
    form.append("file", file);
    form.append("name", nameEl.value || file.name.replace(/\.(pt|onnx)$/i, ""));
    if (classesEl && classesEl.files[0]) form.append("classes", classesEl.files[0]);

    uploadBtn.disabled = true;
    progressEl.hidden = false;
    barEl.style.width = "0%";
    note("");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/models");
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) barEl.style.width = Math.round((e.loaded / e.total) * 100) + "%";
    });
    xhr.addEventListener("load", () => {
      progressEl.hidden = true;
      uploadBtn.disabled = false;
      if (xhr.status === 202) note("Завантажено. Конвертація триває — стежте за статусом.");
      else if (xhr.status === 201) note("Модель готова.");
      else if (xhr.status === 413) note("Файл завеликий.", true);
      else {
        let message = "Не вдалося завантажити файл.";
        try { message = JSON.parse(xhr.responseText).error || message; } catch (_) {}
        note(message, true);
      }
      fileEl.value = "";
      nameEl.value = "";
      if (classesEl) classesEl.value = "";
      uploadBtn.disabled = true; // no file selected any more
      refresh();
    });
    xhr.addEventListener("error", () => {
      progressEl.hidden = true;
      uploadBtn.disabled = false;
      note("Помилка мережі під час завантаження.", true);
    });
    xhr.send(form);
  }

  fileEl.addEventListener("change", () => {
    uploadBtn.disabled = !fileEl.files.length;
    // A ready .onnx carries no class names — offer the sidecar upload for it.
    const isOnnx = fileEl.files.length && /\.onnx$/i.test(fileEl.files[0].name);
    if (classesEl) classesEl.hidden = !isOnnx;
    note(
      isOnnx
        ? "Файл .onnx береться як є, без конвертації: розмір входу — типовий ([track].imgsz). " +
          "Якщо модель експортована з іншим imgsz, завантажте .pt — конвертер визначить його точно."
        : "",
    );
  });
  uploadBtn.addEventListener("click", upload);

  refresh();
})();
