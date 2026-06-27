/**
 * NeuroChat — Frontend Application Logic
 * Handles: SSE streaming, markdown rendering, commands,
 *          model selection, conversation management, UI state.
 */

"use strict";

// ──────────────────────────────────────────────────────────────
// Config
// ──────────────────────────────────────────────────────────────
const API = {
  CHAT:          "/chat",
  HEALTH:        "/health",
  CLEAR:         "/clear",
  MODELS:        "/models",
  CONVERSATIONS: "/conversations",
  SAVE:          "/conversations/save",
  MODEL_SWITCH:  "/models/switch",
};

const HEALTH_INTERVAL_MS = 10_000;   // re-check Ollama every 10s
const AUTO_SCROLL_THRESHOLD = 120;   // px from bottom to auto-scroll

// ──────────────────────────────────────────────────────────────
// DOM references
// ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const dom = {
  sidebar:       $("sidebar"),
  sidebarToggle: $("btn-sidebar-toggle"),
  modelSelect:   $("model-select"),
  statusDot:     $("status-dot"),
  statusText:    $("status-text"),
  btnNewChat:    $("btn-new-chat"),
  btnSave:       $("btn-save"),
  btnClear:      $("btn-clear"),
  savesList:     $("saves-list"),
  msgCount:      $("msg-count"),
  chat:          $("chat-container"),
  welcomeHero:   $("welcome-hero"),
  input:         $("user-input"),
  btnSend:       $("btn-send"),
  sendIcon:      document.querySelector(".send-icon"),
  stopIcon:      document.querySelector(".stop-icon"),
  topbarModel:   $("topbar-model-name"),
  btnTheme:      $("btn-theme"),
  saveModal:     $("save-modal"),
  saveNameInput: $("save-name-input"),
  modalCancel:   $("modal-cancel"),
  modalConfirm:  $("modal-confirm"),
  toast:         $("toast"),
};

// ──────────────────────────────────────────────────────────────
// App state
// ──────────────────────────────────────────────────────────────
let state = {
  streaming:    false,
  abortCtrl:    null,       // AbortController for active fetch
  model:        "mistral",
  msgCount:     0,
  lightTheme:   false,
};

// ──────────────────────────────────────────────────────────────
// Marked.js configuration
// ──────────────────────────────────────────────────────────────
marked.setOptions({
  breaks: true,
  gfm: true,
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  },
});

// ──────────────────────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────────────────────
(async function init() {
  lucide.createIcons();
  await refreshHealth();
  await loadModels();
  await loadSavesList();

  setInterval(refreshHealth, HEALTH_INTERVAL_MS);

  // Restore theme preference
  if (localStorage.getItem("nc-theme") === "light") {
    toggleTheme(false);
  }

  bindEvents();
  dom.input.focus();
})();

// ──────────────────────────────────────────────────────────────
// Event bindings
// ──────────────────────────────────────────────────────────────
function bindEvents() {
  // Sidebar toggle
  dom.sidebarToggle.addEventListener("click", () => {
    dom.sidebar.classList.toggle("collapsed");
  });

  // Theme toggle
  dom.btnTheme.addEventListener("click", () => toggleTheme(true));

  // Mouse glow effect
  document.addEventListener("mousemove", e => {
    document.documentElement.style.setProperty("--mouse-x", `${e.clientX}px`);
    document.documentElement.style.setProperty("--mouse-y", `${e.clientY}px`);
  });

  // Model changed
  dom.modelSelect.addEventListener("change", async () => {
    const model = dom.modelSelect.value;
    state.model = model;
    dom.topbarModel.textContent = model;
    await fetch(API.MODEL_SWITCH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).catch(() => {});
    showToast(`Model → ${model}`);
  });

  // Send button
  dom.btnSend.addEventListener("click", () => {
    if (state.streaming) {
      stopStreaming();
    } else {
      sendMessage();
    }
  });

  // Enter key
  dom.input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!state.streaming) sendMessage();
    }
  });

  // Auto-resize textarea
  dom.input.addEventListener("input", () => {
    dom.input.style.height = "auto";
    dom.input.style.height = Math.min(dom.input.scrollHeight, 180) + "px";
  });

  // Clear button
  dom.btnClear.addEventListener("click", clearChat);

  // New chat
  dom.btnNewChat.addEventListener("click", clearChat);

  // Save button → open modal
  dom.btnSave.addEventListener("click", () => openSaveModal());

  // Modal actions
  dom.modalCancel.addEventListener("click", () => closeSaveModal());
  dom.modalConfirm.addEventListener("click", () => confirmSave());
  dom.saveModal.addEventListener("click", e => {
    if (e.target === dom.saveModal) closeSaveModal();
  });
  dom.saveNameInput.addEventListener("keydown", e => {
    if (e.key === "Enter") confirmSave();
    if (e.key === "Escape") closeSaveModal();
  });

  // Suggestion chips
  document.querySelectorAll(".chip").forEach(chip => {
    chip.addEventListener("click", () => {
      dom.input.value = chip.dataset.prompt;
      dom.input.dispatchEvent(new Event("input"));
      dom.input.focus();
      sendMessage();
    });
  });
}

// ──────────────────────────────────────────────────────────────
// Send message
// ──────────────────────────────────────────────────────────────
async function sendMessage() {
  const text = dom.input.value.trim();
  if (!text || state.streaming) return;

  dom.input.value = "";
  dom.input.style.height = "auto";
  hideWelcome();

  // Render user bubble
  appendUserMessage(text);

  // Typing indicator
  const typingRow = appendTypingIndicator();

  setStreaming(true);
  dom.input.focus();

  const abortCtrl = new AbortController();
  state.abortCtrl = abortCtrl;

  // Create assistant bubble (will be filled by stream)
  let assistantBubble = null;
  let accText = "";
  let rawText  = "";

  try {
    const resp = await fetch(API.CHAT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, model: state.model }),
      signal: abortCtrl.signal,
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    typingRow.remove();

    // Create assistant bubble before first token
    assistantBubble = createAssistantBubble();

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep partial line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);

        if (payload === "[DONE]") break;

        // Handle load command response
        if (payload.startsWith("[LOAD]")) {
          const data = JSON.parse(payload.slice(6));
          if (data.messages) rebuildChatFromHistory(data.messages);
          if (data.model)   syncModel(data.model);
          continue;
        }

        // Unescape newlines encoded by backend
        const token = payload.replace(/\\n/g, "\n");
        rawText += token;
        accText += token;

        // Re-render markdown every ~80 chars or on newline for smooth streaming
        updateBubbleContent(assistantBubble, accText);
        maybeScrollToBottom();
      }
    }
  } catch (err) {
    typingRow?.remove();
    if (err.name !== "AbortError") {
      if (!assistantBubble) assistantBubble = createAssistantBubble();
      updateBubbleContent(
        assistantBubble,
        `⚠️ **Error:** ${err.message}\n\nMake sure you have an internet connection and your API key is valid.`
      );
    }
  } finally {
    setStreaming(false);
    if (assistantBubble) {
      // Final render pass
      updateBubbleContent(assistantBubble, rawText, true);
      addCodeCopyButtons(assistantBubble);
      hljs.highlightAll();
    }
    updateMsgCount();
    scrollToBottom();
  }
}

// ──────────────────────────────────────────────────────────────
// Streaming controls
// ──────────────────────────────────────────────────────────────
function setStreaming(active) {
  state.streaming = active;
  dom.btnSend.classList.toggle("streaming", active);
  dom.sendIcon.classList.toggle("hidden", active);
  dom.stopIcon.classList.toggle("hidden", !active);
  dom.input.disabled = active;
}

function stopStreaming() {
  state.abortCtrl?.abort();
  setStreaming(false);
}

// ──────────────────────────────────────────────────────────────
// Message rendering helpers
// ──────────────────────────────────────────────────────────────
function appendUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row user";

  const wrap = document.createElement("div");
  wrap.className = "message-bubble-wrap";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;

  const meta = makeMeta("user", text);

  wrap.appendChild(bubble);
  wrap.appendChild(meta);
  row.appendChild(wrap);
  dom.chat.appendChild(row);

  state.msgCount++;
  scrollToBottom();
}

function appendTypingIndicator() {
  const row = document.createElement("div");
  row.className = "message-row assistant";

  const wrap = document.createElement("div");
  wrap.className = "message-bubble-wrap";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";

  const indicator = document.createElement("div");
  indicator.className = "typing-indicator";
  indicator.innerHTML = "<span></span><span></span><span></span>";

  bubble.appendChild(indicator);
  wrap.appendChild(bubble);
  row.appendChild(wrap);
  dom.chat.appendChild(row);
  scrollToBottom();
  return row;
}

function createAssistantBubble() {
  const row = document.createElement("div");
  row.className = "message-row assistant";

  const wrap = document.createElement("div");
  wrap.className = "message-bubble-wrap";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.dataset.raw = "";

  const meta = makeMeta("assistant", "");

  wrap.appendChild(bubble);
  wrap.appendChild(meta);
  row.appendChild(wrap);
  dom.chat.appendChild(row);

  state.msgCount++;
  return bubble;
}

function updateBubbleContent(bubble, text, final = false) {
  bubble.dataset.raw = text;
  // During streaming use a lightweight render; final pass does full highlight
  try {
    bubble.innerHTML = marked.parse(text);
  } catch {
    bubble.textContent = text;
  }

  // Update the copy button's target text
  const wrap = bubble.parentElement;
  const copyBtn = wrap?.querySelector(".btn-copy");
  if (copyBtn) copyBtn.dataset.text = text;
}

function makeMeta(role, text) {
  const meta = document.createElement("div");
  meta.className = "message-meta";

  const time = document.createElement("span");
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const copyBtn = document.createElement("button");
  copyBtn.className = "btn-copy";
  copyBtn.dataset.text = text;
  copyBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy`;
  copyBtn.addEventListener("click", () => {
    const textToCopy = copyBtn.closest(".message-bubble-wrap")?.querySelector(".message-bubble")?.dataset.raw || copyBtn.dataset.text;
    navigator.clipboard.writeText(textToCopy || "").then(() => {
      copyBtn.classList.add("copied");
      copyBtn.textContent = "✓ Copied";
      setTimeout(() => {
        copyBtn.classList.remove("copied");
        copyBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy`;
      }, 2000);
    });
  });

  meta.appendChild(time);
  meta.appendChild(copyBtn);
  return meta;
}

function addCodeCopyButtons(bubble) {
  bubble.querySelectorAll("pre").forEach(pre => {
    if (pre.querySelector(".code-copy-btn")) return;
    const btn = document.createElement("button");
    btn.className = "code-copy-btn";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => {
      const code = pre.querySelector("code")?.textContent || "";
      navigator.clipboard.writeText(code).then(() => {
        btn.textContent = "✓ Copied!";
        setTimeout(() => { btn.textContent = "Copy"; }, 2000);
      });
    });
    pre.style.position = "relative";
    pre.appendChild(btn);
  });
}

// ──────────────────────────────────────────────────────────────
// Scroll helpers
// ──────────────────────────────────────────────────────────────
function scrollToBottom() {
  dom.chat.scrollTop = dom.chat.scrollHeight;
}

function maybeScrollToBottom() {
  const distFromBottom = dom.chat.scrollHeight - dom.chat.scrollTop - dom.chat.clientHeight;
  if (distFromBottom < AUTO_SCROLL_THRESHOLD) scrollToBottom();
}

// ──────────────────────────────────────────────────────────────
// Clear / New chat
// ──────────────────────────────────────────────────────────────
async function clearChat() {
  await fetch(API.CLEAR, { method: "POST" }).catch(() => {});

  // Remove all message rows but keep the welcome hero
  const rows = dom.chat.querySelectorAll(".message-row");
  rows.forEach(r => r.remove());

  dom.welcomeHero.style.display = "";
  state.msgCount = 0;
  updateMsgCount();
  showToast("Memory cleared");
}

// ──────────────────────────────────────────────────────────────
// Welcome hero
// ──────────────────────────────────────────────────────────────
function hideWelcome() {
  dom.welcomeHero.style.display = "none";
}

// ──────────────────────────────────────────────────────────────
// Health check
// ──────────────────────────────────────────────────────────────
async function refreshHealth() {
  try {
    const resp = await fetch(API.HEALTH);
    if (!resp.ok) throw new Error();
    const data = await resp.json();

    dom.statusDot.className = "status-dot " + (data.api_reachable ? "online" : "offline");
    dom.statusText.textContent = data.api_reachable
      ? `Online · ${data.current_model}`
      : "API offline";

    state.msgCount = Math.floor(data.message_count / 2);
    updateMsgCount();
  } catch {
    dom.statusDot.className = "status-dot offline";
    dom.statusText.textContent = "Cannot reach server";
  }
}

// ──────────────────────────────────────────────────────────────
// Models
// ──────────────────────────────────────────────────────────────
async function loadModels() {
  try {
    const resp = await fetch(API.MODELS);
    if (!resp.ok) return;
    const data = await resp.json();

    dom.modelSelect.innerHTML = "";

    if (!data.models || data.models.length === 0) {
      const opt = document.createElement("option");
      opt.value = "mistral";
      opt.textContent = "mistral (not pulled yet)";
      dom.modelSelect.appendChild(opt);
      return;
    }

    data.models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === data.current) opt.selected = true;
      dom.modelSelect.appendChild(opt);
    });

    state.model = data.current || data.models[0];
    dom.topbarModel.textContent = state.model;
  } catch {
    // API not reachable yet — keep default option
  }
}

function syncModel(model) {
  state.model = model;
  dom.topbarModel.textContent = model;
  const opts = [...dom.modelSelect.options];
  const match = opts.find(o => o.value === model);
  if (match) match.selected = true;
}

// ──────────────────────────────────────────────────────────────
// Saved conversations
// ──────────────────────────────────────────────────────────────
async function loadSavesList() {
  try {
    const resp = await fetch(API.CONVERSATIONS);
    if (!resp.ok) return;
    const data = await resp.json();
    renderSavesList(data.conversations || []);
  } catch {}
}

function renderSavesList(conversations) {
  dom.savesList.innerHTML = "";

  if (!conversations.length) {
    dom.savesList.innerHTML = '<p class="saves-empty">No saved chats yet.</p>';
    return;
  }

  conversations.forEach(conv => {
    const item = document.createElement("div");
    item.className = "save-item";

    const name = document.createElement("span");
    name.className = "save-item-name";
    name.textContent = conv.name;
    name.title = `${conv.messages} messages`;

    const delBtn = document.createElement("button");
    delBtn.className = "save-item-del";
    delBtn.title = "Delete";
    delBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>`;
    delBtn.addEventListener("click", async e => {
      e.stopPropagation();
      await fetch(`${API.CONVERSATIONS}/${conv.name}`, { method: "DELETE" });
      await loadSavesList();
      showToast(`Deleted "${conv.name}"`);
    });

    item.appendChild(name);
    item.appendChild(delBtn);

    // Click to load
    item.addEventListener("click", async () => {
      const resp = await fetch(`${API.CONVERSATIONS}/${conv.name}/load`, { method: "POST" });
      const data = await resp.json();
      if (data.data?.messages) {
        rebuildChatFromHistory(data.data.messages);
        if (data.data.model) syncModel(data.data.model);
        showToast(`Loaded "${conv.name}"`);
      } else {
        showToast(data.message || "Loaded");
      }
    });

    dom.savesList.appendChild(item);
  });
}

function rebuildChatFromHistory(messages) {
  // Clear existing messages
  dom.chat.querySelectorAll(".message-row").forEach(r => r.remove());
  dom.welcomeHero.style.display = "none";
  state.msgCount = 0;

  messages.forEach(msg => {
    if (msg.role === "user") {
      appendUserMessage(msg.content);
    } else if (msg.role === "assistant") {
      const bubble = createAssistantBubble();
      updateBubbleContent(bubble, msg.content, true);
      addCodeCopyButtons(bubble);
    }
  });

  hljs.highlightAll();
  scrollToBottom();
  updateMsgCount();
}

// ──────────────────────────────────────────────────────────────
// Save modal
// ──────────────────────────────────────────────────────────────
function openSaveModal() {
  const ts = new Date().toISOString().slice(0, 16).replace("T", "_").replace(":", "-");
  dom.saveNameInput.value = `chat_${ts}`;
  dom.saveModal.classList.remove("hidden");
  setTimeout(() => { dom.saveNameInput.select(); dom.saveNameInput.focus(); }, 50);
}

function closeSaveModal() {
  dom.saveModal.classList.add("hidden");
}

async function confirmSave() {
  const name = dom.saveNameInput.value.trim() || undefined;
  closeSaveModal();

  try {
    const resp = await fetch(API.SAVE, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await resp.json();
    showToast(data.message || "Saved!");
    await loadSavesList();
  } catch {
    showToast("⚠️ Could not save.");
  }
}

// ──────────────────────────────────────────────────────────────
// Theme
// ──────────────────────────────────────────────────────────────
function toggleTheme(persist = true) {
  state.lightTheme = !state.lightTheme;
  document.body.classList.toggle("light", state.lightTheme);
  const icon = dom.btnTheme.querySelector("i");
  if (icon) {
    icon.setAttribute("data-lucide", state.lightTheme ? "moon" : "sun");
    lucide.createIcons();
  }
  if (persist) localStorage.setItem("nc-theme", state.lightTheme ? "light" : "dark");
}

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────
function updateMsgCount() {
  dom.msgCount.textContent = `${state.msgCount} message${state.msgCount !== 1 ? "s" : ""} in context`;
}

let toastTimer;
function showToast(msg, duration = 2800) {
  dom.toast.textContent = msg;
  dom.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => dom.toast.classList.add("hidden"), duration);
}
