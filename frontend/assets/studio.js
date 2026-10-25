const API = "/api/v1";
const state = {
  sessionId: null,
  session: null,
  busy: false,
  jobTimer: null,
  historyDepth: 0,
  redoAvailable: false,
  sourceVideoUrl: null,
  lastRenderedUrl: null,
  showingRendered: false,
};
const $ = (id) => document.getElementById(id);
const whisperLanguages = [
  ["af", "Afrikaans"], ["am", "Amharic"], ["ar", "Arabic"], ["as", "Assamese"], ["az", "Azerbaijani"], ["ba", "Bashkir"], ["be", "Belarusian"], ["bg", "Bulgarian"], ["bn", "Bengali"], ["bo", "Tibetan"], ["br", "Breton"], ["bs", "Bosnian"], ["ca", "Catalan"], ["cs", "Czech"], ["cy", "Welsh"], ["da", "Danish"], ["de", "German"], ["el", "Greek"], ["en", "English"], ["es", "Spanish"], ["et", "Estonian"], ["eu", "Basque"], ["fa", "Persian"], ["fi", "Finnish"], ["fo", "Faroese"], ["fr", "French"], ["gl", "Galician"], ["gu", "Gujarati"], ["ha", "Hausa"], ["haw", "Hawaiian"], ["he", "Hebrew"], ["hi", "Hindi"], ["hr", "Croatian"], ["ht", "Haitian Creole"], ["hu", "Hungarian"], ["hy", "Armenian"], ["id", "Indonesian"], ["is", "Icelandic"], ["it", "Italian"], ["ja", "Japanese"], ["jw", "Javanese"], ["ka", "Georgian"], ["kk", "Kazakh"], ["km", "Khmer"], ["kn", "Kannada"], ["ko", "Korean"], ["la", "Latin"], ["lb", "Luxembourgish"], ["ln", "Lingala"], ["lo", "Lao"], ["lt", "Lithuanian"], ["lv", "Latvian"], ["mg", "Malagasy"], ["mi", "Maori"], ["mk", "Macedonian"], ["ml", "Malayalam"], ["mn", "Mongolian"], ["mr", "Marathi"], ["ms", "Malay"], ["mt", "Maltese"], ["my", "Burmese"], ["ne", "Nepali"], ["nl", "Dutch"], ["nn", "Norwegian Nynorsk"], ["no", "Norwegian"], ["oc", "Occitan"], ["pa", "Punjabi"], ["pl", "Polish"], ["ps", "Pashto"], ["pt", "Portuguese"], ["ro", "Romanian"], ["ru", "Russian"], ["sa", "Sanskrit"], ["sd", "Sindhi"], ["si", "Sinhala"], ["sk", "Slovak"], ["sl", "Slovenian"], ["sn", "Shona"], ["so", "Somali"], ["sq", "Albanian"], ["sr", "Serbian"], ["su", "Sundanese"], ["sv", "Swedish"], ["sw", "Swahili"], ["ta", "Tamil"], ["te", "Telugu"], ["tg", "Tajik"], ["th", "Thai"], ["tk", "Turkmen"], ["tl", "Tagalog"], ["tt", "Tatar"], ["uk", "Ukrainian"], ["ur", "Urdu"], ["uz", "Uzbek"], ["vi", "Vietnamese"], ["yi", "Yiddish"], ["yo", "Yoruba"], ["zh", "Chinese"], ["yue", "Cantonese"]
];
for (const [code, name] of whisperLanguages) {
  const option = document.createElement("option"); option.value = code; option.textContent = `${name} (${code})`; $("sourceLanguage").append(option);
}

/* ─── Utilities ─── */
function key() { return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`; }
function fmt(value) { const s = Math.max(0, Math.floor(Number(value) || 0)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

let toastTimer = null;
function toast(message, duration = 3200) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), duration);
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = options._timeout || 120_000;
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${API}${path}`, { ...options, signal: controller.signal });
    const type = response.headers.get("content-type") || "";
    if (!response.ok) {
      let data;
      try { data = type.includes("json") ? await response.json() : { detail: await response.text() }; } catch { data = {}; }
      const message = data?.error?.message || data?.detail?.error?.message || data?.detail || `Request failed (${response.status})`;
      const err = new Error(message);
      err.status = response.status;
      err.retryable = data?.error?.retryable || response.status >= 500;
      throw err;
    }
    return type.includes("json") ? await response.json() : response;
  } finally {
    clearTimeout(timer);
  }
}

function setBusy(value) {
  state.busy = value;
  $("sendBtn").disabled = value || !state.sessionId;
  $("promptInput").disabled = value || !state.sessionId;
  $("renderBtn").disabled = value || !state.sessionId;
  updateUndoRedoState();
}

function updateUndoRedoState() {
  $("undoBtn").disabled = state.busy || !state.sessionId || state.historyDepth < 1;
  $("redoBtn").disabled = state.busy || !state.sessionId || !state.redoAvailable;
}

/* ─── Upload (click and drag-and-drop) ─── */
async function uploadVideo(file) {
  if (!file) return;
  // Validate file type client-side
  if (file.type && !file.type.startsWith("video/") && file.type !== "application/octet-stream") {
    toast("Please select a video file.");
    return;
  }
  // Validate file size (2GB default)
  if (file.size > 2_000_000_000) {
    toast("File is too large. Maximum upload size is 2 GB.");
    return;
  }

  const localUrl = URL.createObjectURL(file);
  $("videoPreview").src = localUrl;
  $("previewFrame").classList.add("has-video");
  $("projectLabel").textContent = file.name.replace(/\.[^.]+$/, "") || "Untitled edit";
  const form = new FormData(); form.append("file", file);
  if ($("sourceLanguage").value) form.append("language", $("sourceLanguage").value);
  setBusy(true); showJob("Uploading & storing source…", 3);
  try {
    const job = await api("/sessions/upload", { method: "POST", headers: { "Idempotency-Key": key() }, body: form, _timeout: 600_000 });
    await watchJob(job.job_id, "Transcribing your video…", async (result) => {
      state.sessionId = result.session_id;
      await loadSession();
      $("sessionChip").textContent = "SESSION READY";
      $("sessionChip").classList.add("active");
      state.sourceVideoUrl = `${API}/sessions/${state.sessionId}/source`;
      $("videoPreview").src = state.sourceVideoUrl;
      $("videoPreview").load();
      state.showingRendered = false;
      setBusy(false);
      toast("Transcript ready — your edit session is live.");
    });
  } catch (error) {
    hideJob();
    setBusy(false);
    toast(error.message);
    // Reset preview on failure
    if (!state.sessionId) {
      $("previewFrame").classList.remove("has-video");
      $("videoPreview").removeAttribute("src");
    }
  }
}

// Drag-and-drop support on the preview area
const previewFrame = $("previewFrame");
previewFrame.addEventListener("dragover", (event) => { event.preventDefault(); previewFrame.classList.add("drag-over"); });
previewFrame.addEventListener("dragleave", () => previewFrame.classList.remove("drag-over"));
previewFrame.addEventListener("drop", (event) => {
  event.preventDefault();
  previewFrame.classList.remove("drag-over");
  const file = event.dataTransfer?.files?.[0];
  if (file) uploadVideo(file);
});

/* ─── Job tracking ─── */
function showJob(label, progress = 0) { $("jobStatus").hidden = false; $("jobLabel").textContent = label; setProgress(progress); }
function setProgress(value) { const n = Math.max(0, Math.min(100, Number(value) || 0)); $("jobPercent").textContent = `${n}%`; $("jobProgress").style.width = `${n}%`; }
function hideJob() { $("jobStatus").hidden = true; }

async function watchJob(jobId, label, onSuccess) {
  showJob(label, 5);
  let failures = 0;
  const maxFailures = 5;
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1200));
    let job;
    try {
      job = await api(`/jobs/${jobId}`);
      failures = 0;  // Reset on success
    } catch (err) {
      failures++;
      if (failures >= maxFailures) throw new Error(`Lost connection to server after ${maxFailures} retries.`);
      $("jobLabel").textContent = "Reconnecting…";
      continue;
    }
    setProgress(job.progress);
    if (job.status === "succeeded") {
      setProgress(100);
      await new Promise(resolve => setTimeout(resolve, 300));
      hideJob();
      await onSuccess(job.result || {});
      return job.result;
    }
    if (job.status === "failed") {
      hideJob();
      throw new Error(job.error?.message || "The background job failed.");
    }
    $("jobLabel").textContent = job.status === "running" ? label : "Waiting for a worker…";
  }
}

/* ─── Session state ─── */
async function loadSession() {
  const session = await api(`/sessions/${state.sessionId}`);
  state.session = session;
  const meta = session.video_metadata || {};
  $("previewMeta").hidden = false;
  $("videoDimensions").textContent = `${meta.width || "—"} × ${meta.height || "—"}`;
  $("videoDuration").textContent = fmt(meta.duration);
  const language = meta.transcript_language ? ` · ${meta.transcript_language.toUpperCase()}` : " · AUTO LANGUAGE";
  $("speechCount").textContent = `${session.transcript?.length || 0} WORDS${language}`;
  $("durationTime").textContent = fmt(session.duration_s);
  $("wordCount").textContent = `${session.active_word_ids.length} ACTIVE WORDS · REV ${String(session.revision).padStart(2, "0")}`;
  $("revisionLabel").textContent = `REV ${String(session.revision).padStart(2, "0")}`;

  // The session revision is a concurrency token; history depths come from timeline state.
  state.historyDepth = session.history_depth;
  state.redoAvailable = session.redo_depth > 0;
  updateUndoRedoState();

  renderTranscript(session);
  renderCaptionLane(session);
  $("waveform").classList.toggle("inactive", !session.active_word_ids.length);
  $("promptInput").disabled = false;
  $("sendBtn").disabled = false;
  $("renderBtn").disabled = false;
}

function renderTranscript(session) {
  const active = new Set(session.active_word_ids);
  const words = (session.transcript || []).filter(w => active.has(w.word_id));
  if (!words.length) {
    $("transcriptTrack").innerHTML = '<span class="transcript-empty">No active transcript words.</span>';
    return;
  }
  $("transcriptTrack").innerHTML = words.map(w => {
    const conf = Math.round((w.confidence ?? 0) * 100);
    const confClass = conf >= 80 ? "high" : conf >= 50 ? "medium" : "low";
    return `<button class="word-pill conf-${confClass}" type="button" data-time="${Number(w.start)}" title="${fmt(w.start)} · ${conf}% confidence">${escapeHtml(w.word)}</button>`;
  }).join("");
  $("transcriptTrack").querySelectorAll("[data-time]").forEach(button =>
    button.addEventListener("click", () => {
      $("videoPreview").currentTime = Number(button.dataset.time);
      $("videoPreview").play().catch(() => { });
    })
  );
}

function renderCaptionLane(session) {
  const captions = session.captions || [];
  if (!captions.length) {
    $("captionBlocks").innerHTML = '<span class="empty-lane">—</span>';
    return;
  }
  // Build caption chips with timing context
  const active = new Set(session.active_word_ids);
  const wordMap = {};
  for (const w of (session.transcript || [])) wordMap[w.word_id] = w;

  $("captionBlocks").innerHTML = captions.map(c => {
    const targets = (c.target_word_ids || []).filter(id => active.has(id));
    const start = targets.length && wordMap[targets[0]] ? fmt(wordMap[targets[0]].start) : "—";
    const style = c.style || {};
    const color = style.color || "white";
    return `<span class="caption-chip" title="${start}" style="border-color: ${escapeHtml(color)}">${escapeHtml(c.text)}</span>`;
  }).join("");
}

/* ─── Conversation ─── */
function appendMessage(label, text, kind = "user") {
  const el = document.createElement("div");
  el.className = kind === "user" ? "user-message" : "assistant-message result-message";
  el.innerHTML = `<span class="message-label">${escapeHtml(label)}</span><p>${escapeHtml(text)}</p>`;
  $("conversation").appendChild(el);
  $("conversation").scrollTop = $("conversation").scrollHeight;
}

function appendOperationSummary(summaries) {
  if (!summaries || !summaries.length) return;
  const el = document.createElement("div");
  el.className = "assistant-message ops-summary";
  el.innerHTML = `<span class="message-label">OPERATIONS</span><ul>${summaries.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`;
  $("conversation").appendChild(el);
  $("conversation").scrollTop = $("conversation").scrollHeight;
}

function showClarification(result, originalPrompt) {
  const box = $("clarificationCard"); box.hidden = false;
  box.innerHTML = `<strong>One detail before I edit</strong><div>${escapeHtml(result.question)}</div>`;
  for (const option of result.options || []) {
    const button = document.createElement("button");
    button.textContent = `${option.start_time_current} · ${option.word_snippet || option.description}`;
    button.addEventListener("click", () => {
      const specific = `${originalPrompt}. Specifically, choose the words "${option.word_snippet}" around ${option.start_time_current}.`;
      box.hidden = true;
      submitPrompt(specific, `Selected ${option.start_time_current}: ${option.word_snippet}`);
    });
    box.appendChild(button);
  }
}

/* ─── Edit Submission ─── */
async function submitPrompt(prompt, displayText = prompt) {
  if (!state.sessionId || state.busy || !prompt.trim()) return;
  setBusy(true);
  appendMessage("YOU", displayText, "user");
  $("clarificationCard").hidden = true;
  try {
    const response = await api(`/sessions/${state.sessionId}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key() },
      body: JSON.stringify({ prompt }),
    });
    const result = response.result;
    if (result.status === "needs_clarification") {
      appendMessage("NEEDS CLARIFICATION", result.question, "assistant");
      showClarification(result, prompt);
      // With clarification, redo state is unknown — disable for safety
      state.redoAvailable = false;
    } else if (result.status === "rejected") {
      appendMessage("NOT APPLIED", result.reason, "assistant");
    } else {
      appendMessage("EDIT APPLIED", `${result.summary} · ${fmt(response.duration_s)} total`, "assistant");
      if (result.operations && result.operations.length > 1) {
        appendOperationSummary(result.operations.map(op => op.summary || op.op || "operation"));
      }
      await loadSession();
    }
  } catch (error) {
    const retryHint = error.retryable ? " This may be temporary — try again." : "";
    appendMessage("REQUEST FAILED", error.message + retryHint, "assistant");
  } finally {
    setBusy(false);
  }
}

/* ─── Render ─── */
async function renderEdit() {
  if (!state.sessionId || state.busy) return;
  setBusy(true);
  appendMessage("YOU", "Render the current cut", "user");
  try {
    const queued = await api(`/sessions/${state.sessionId}/render`, { method: "POST", headers: { "Idempotency-Key": key() } });
    await watchJob(queued.job_id, "Rendering your cut…", async () => {
      const resultUrl = `${API}/jobs/${queued.job_id}/result`;
      state.lastRenderedUrl = resultUrl;
      state.showingRendered = true;
      const video = $("videoPreview");
      video.src = resultUrl;
      video.load();

      appendMessage("RENDER READY", "Your rendered video is ready. Use the download link or the player menu to save a copy.", "assistant");
      const actions = document.createElement("div");
      actions.className = "render-actions";

      const link = document.createElement("a");
      link.href = resultUrl; link.download = "incubrix-edit.mp4";
      link.className = "download-link"; link.textContent = "Download render ↓";

      const switchBtn = document.createElement("button");
      switchBtn.className = "download-link switch-source";
      switchBtn.textContent = "Show original source ↩";
      switchBtn.addEventListener("click", () => {
        if (state.showingRendered && state.sourceVideoUrl) {
          video.src = state.sourceVideoUrl; video.load();
          switchBtn.textContent = "Show rendered ↗";
          state.showingRendered = false;
        } else if (state.lastRenderedUrl) {
          video.src = state.lastRenderedUrl; video.load();
          switchBtn.textContent = "Show original source ↩";
          state.showingRendered = true;
        }
      });

      actions.appendChild(link);
      actions.appendChild(switchBtn);
      $("conversation").appendChild(actions);
      $("conversation").scrollTop = $("conversation").scrollHeight;
    });
  } catch (error) {
    hideJob();
    appendMessage("RENDER FAILED", error.message, "assistant");
  } finally {
    setBusy(false);
  }
}

/* ─── New Project ─── */
function resetProject() {
  state.sessionId = null;
  state.session = null;
  state.historyDepth = 0;
  state.redoAvailable = false;
  state.sourceVideoUrl = null;
  state.lastRenderedUrl = null;
  state.showingRendered = false;
  $("videoInput").value = "";
  $("videoPreview").removeAttribute("src");
  $("videoPreview").load();
  $("previewFrame").classList.remove("has-video");
  $("previewMeta").hidden = true;
  $("sessionChip").textContent = "NO SESSION";
  $("sessionChip").classList.remove("active");
  $("transcriptTrack").innerHTML = '<div class="transcript-empty">Your word-level transcript will appear here after upload.</div>';
  $("captionBlocks").innerHTML = '<span class="empty-lane">—</span>';
  $("promptInput").value = "";
  $("promptInput").disabled = true;
  $("sendBtn").disabled = true;
  $("renderBtn").disabled = true;
  $("wordCount").textContent = "WAITING FOR VIDEO";
  $("revisionLabel").textContent = "REV 00";
  $("undoBtn").disabled = true;
  $("redoBtn").disabled = true;
  $("clarificationCard").hidden = true;
  hideJob();
  // Clear conversation history
  const conv = $("conversation");
  conv.innerHTML = `<div class="date-stamp">A NEW EDITING SESSION</div><div class="assistant-message"><span class="message-label">INCUBRIX ASSISTANT</span><p>Upload your video and describe the edit in your language. Ask for Tamil or another target language when you want translated captions.</p></div><div class="suggestion-title">TRY A PROMPT</div><div class="suggestions"><button class="suggestion" data-prompt="Remove the first 5 seconds">Trim the opening <span>↗</span></button><button class="suggestion" data-prompt="Add a caption 'Key idea' when I say the main point">Add a caption <span>↗</span></button><button class="suggestion" data-prompt="Remove the filler words um and uh">Remove filler words <span>↗</span></button><button class="suggestion" data-prompt="Automatically caption this video in Tamil with no more than 6 words per caption">Add Tamil captions <span>↗</span></button></div>`;
  // Re-bind suggestion click handlers
  conv.querySelectorAll(".suggestion").forEach(button =>
    button.addEventListener("click", () => { $("promptInput").value = button.dataset.prompt; $("promptInput").focus(); })
  );
  toast("Ready for a new project.");
}

/* ─── Event Bindings ─── */
$("videoInput").addEventListener("change", event => uploadVideo(event.target.files?.[0]));
$("promptForm").addEventListener("submit", event => {
  event.preventDefault();
  const input = $("promptInput");
  const prompt = input.value.trim();
  if (prompt) { input.value = ""; submitPrompt(prompt); }
});
$("promptInput").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("promptForm").requestSubmit(); }
});
$("renderBtn").addEventListener("click", renderEdit);
$("undoBtn").addEventListener("click", () => {
  submitPrompt("Undo", "Undo the last edit");
});
$("redoBtn").addEventListener("click", () => {
  submitPrompt("Redo", "Redo the last undone edit");
});
$("newProjectBtn").addEventListener("click", resetProject);
$("conversation").querySelectorAll(".suggestion").forEach(button =>
  button.addEventListener("click", () => { $("promptInput").value = button.dataset.prompt; $("promptInput").focus(); })
);
$("videoPreview").addEventListener("timeupdate", () => { $("playheadTime").textContent = fmt($("videoPreview").currentTime); });

/* ─── Keyboard Shortcuts ─── */
document.addEventListener("keydown", (event) => {
  // Skip if user is typing in an input
  if (event.target.tagName === "TEXTAREA" || event.target.tagName === "INPUT") return;

  if ((event.ctrlKey || event.metaKey) && event.key === "z" && !event.shiftKey) {
    event.preventDefault();
    if (!$("undoBtn").disabled) $("undoBtn").click();
  }
  if ((event.ctrlKey || event.metaKey) && (event.key === "y" || (event.key === "z" && event.shiftKey))) {
    event.preventDefault();
    if (!$("redoBtn").disabled) $("redoBtn").click();
  }
});
