(function () {
  "use strict";

  // ── Read config from the script tag ────────────────────────────────────────
  const scriptTag = document.currentScript;
  const API_URL = scriptTag.getAttribute("data-server") || "http://localhost:5001";
  const BOT_ID = scriptTag.getAttribute("data-bot-id") || "";
  const BOT_NAME = scriptTag.getAttribute("data-bot-name") || "RAG Chatbot";
  const PRIMARY_COLOR = scriptTag.getAttribute("data-color") || "#6C63FF";
  const WELCOME_MSG =
    scriptTag.getAttribute("data-welcome") ||
    "Hi there! 👋 Ask me anything about the document.";

  // ── Session Management ─────────────────────────────────────────────────────
  let SESSION_ID = localStorage.getItem("rag_bot_session_id");
  if (!SESSION_ID) {
    SESSION_ID = "sess_" + Math.random().toString(36).substring(2, 15);
    localStorage.setItem("rag_bot_session_id", SESSION_ID);
  }

  // ── Inject styles ──────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
    #rag-widget-bubble {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 60px;
      height: 60px;
      border-radius: 50%;
      background: ${PRIMARY_COLOR};
      color: #fff;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 99999;
      transition: transform 0.3s ease, box-shadow 0.3s ease;
    }
    #rag-widget-bubble:hover {
      transform: scale(1.1);
      box-shadow: 0 6px 28px rgba(0,0,0,0.35);
    }
    #rag-widget-bubble svg { width: 28px; height: 28px; }

    #rag-widget-window {
      position: fixed;
      bottom: 100px;
      right: 24px;
      width: 380px;
      height: 380px;
      border-radius: 16px;
      background: #1a1a2e;
      box-shadow: 0 10px 40px rgba(0,0,0,0.4);
      z-index: 99998;
      display: none;
      flex-direction: column;
      overflow: hidden;
      font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
      border: 1px solid rgba(255,255,255,0.08);
    }
    #rag-widget-window.open { display: flex; }

    /* Header */
    .rag-w-header {
      background: ${PRIMARY_COLOR};
      padding: 16px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #fff;
    }
    .rag-w-header-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .rag-w-header h4 {
      margin: 0;
      font-size: 15px;
      font-weight: 600;
    }
    .rag-w-header p {
      margin: 0;
      font-size: 11px;
      opacity: 0.85;
    }
    .rag-w-dot {
      width: 8px;
      height: 8px;
      background: #4ade80;
      border-radius: 50%;
      display: inline-block;
    }
    .rag-w-close:hover { opacity: 1; }

    .rag-w-actions {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .rag-w-header-btn {
      background: none;
      border: none;
      color: #fff;
      cursor: pointer;
      font-size: 16px;
      padding: 4px;
      opacity: 0.7;
      display: flex;
      align-items: center;
      transition: opacity 0.2s;
    }
    .rag-w-header-btn:hover { opacity: 1; }
    .rag-w-header-btn svg { width: 16px; height: 16px; }

    /* Messages area */
    .rag-w-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .rag-w-messages::-webkit-scrollbar { width: 4px; }
    .rag-w-messages::-webkit-scrollbar-thumb {
      background: rgba(255,255,255,0.15);
      border-radius: 4px;
    }

    .rag-w-msg {
      max-width: 85%;
      padding: 12px 14px;
      border-radius: 12px;
      font-size: 13.5px;
      line-height: 1.6;
      word-wrap: break-word;
    }
    .rag-w-msg p { margin: 8px 0; }
    .rag-w-msg p:first-child { margin-top: 0; }
    .rag-w-msg p:last-child { margin-bottom: 0; }
    .rag-w-msg ul, .rag-w-msg ol { padding-left: 20px; margin: 8px 0; }
    .rag-w-msg code {
      background: rgba(0,0,0,0.3);
      padding: 2px 4px;
      border-radius: 4px;
      font-family: monospace;
    }
    .rag-w-msg pre {
      background: rgba(0,0,0,0.3);
      padding: 10px;
      border-radius: 8px;
      overflow-x: auto;
      margin: 8px 0;
    }
    .rag-w-msg.bot {
      align-self: flex-start;
      background: rgba(255,255,255,0.08);
      color: #e0e0e0;
      border-bottom-left-radius: 4px;
    }
    .rag-w-msg.user {
      align-self: flex-end;
      background: ${PRIMARY_COLOR};
      color: #fff;
      border-bottom-right-radius: 4px;
    }

    /* Typing indicator */
    .rag-w-typing {
      display: flex;
      gap: 4px;
      padding: 10px 14px;
      align-self: flex-start;
      background: rgba(255,255,255,0.08);
      border-radius: 12px;
      border-bottom-left-radius: 4px;
    }
    .rag-w-typing span {
      width: 6px;
      height: 6px;
      background: rgba(255,255,255,0.4);
      border-radius: 50%;
      animation: rag-bounce 1.2s infinite;
    }
    .rag-w-typing span:nth-child(2) { animation-delay: 0.2s; }
    .rag-w-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes rag-bounce {
      0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
      30% { transform: translateY(-6px); opacity: 1; }
    }

    /* Input area */
    .rag-w-input-area {
      padding: 12px 16px;
      border-top: 1px solid rgba(255,255,255,0.08);
      display: flex;
      gap: 8px;
      background: rgba(255,255,255,0.03);
    }
    .rag-w-input-area input {
      flex: 1;
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      font-size: 13px;
      outline: none;
      font-family: inherit;
    }
    .rag-w-input-area input::placeholder { color: rgba(255,255,255,0.35); }
    .rag-w-input-area input:focus {
      border-color: ${PRIMARY_COLOR};
    }
    .rag-w-input-area button {
      background: ${PRIMARY_COLOR};
      border: none;
      border-radius: 8px;
      padding: 0 14px;
      cursor: pointer;
      color: #fff;
      display: flex;
      align-items: center;
    }
    .rag-w-input-area button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .rag-w-input-area button svg { width: 18px; height: 18px; }

    /* Powered by badge */
    .rag-w-powered {
      text-align: center;
      padding: 6px;
      font-size: 10px;
      color: rgba(255,255,255,0.25);
    }
  `;
  document.head.appendChild(style);

  // ── Create chat bubble button ──────────────────────────────────────────────
  const bubble = document.createElement("button");
  bubble.id = "rag-widget-bubble";
  bubble.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>`;
  document.body.appendChild(bubble);

  // ── Create chat window ─────────────────────────────────────────────────────
  const win = document.createElement("div");
  win.id = "rag-widget-window";
  win.innerHTML = `
    <div class="rag-w-header">
      <div class="rag-w-header-left">
        <div>
          <h4>${BOT_NAME} <span class="rag-w-dot"></span></h4>
          <p>Online</p>
        </div>
      </div>
      <div class="rag-w-actions">
        <button class="rag-w-header-btn" id="rag-w-clear" title="Clear Chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" 
               stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
        </button>
        <button class="rag-w-close">&times;</button>
      </div>
    </div>
    <div class="rag-w-messages" id="rag-w-messages"></div>
    <div class="rag-w-input-area">
      <input type="text" id="rag-w-input" placeholder="Type your message..." autocomplete="off" />
      <button id="rag-w-send">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13"/>
          <polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>
      </button>
    </div>
    <div class="rag-w-powered">Powered by RAG Chatbot</div>`;
  document.body.appendChild(win);

  // ── Element references ─────────────────────────────────────────────────────
  const messagesEl = document.getElementById("rag-w-messages");
  const inputEl = document.getElementById("rag-w-input");
  const sendBtn = document.getElementById("rag-w-send");
  const clearBtn = document.getElementById("rag-w-clear");
  const closeBtn = win.querySelector(".rag-w-close");

  // ── Toggle open/close ──────────────────────────────────────────────────────
  let isOpen = false;
  let welcomed = false;

  bubble.addEventListener("click", () => {
    isOpen = !isOpen;
    win.classList.toggle("open", isOpen);
    if (isOpen && !welcomed) {
      addMessage(WELCOME_MSG, "bot");
      welcomed = true;
    }
    if (isOpen) inputEl.focus();
  });

  closeBtn.addEventListener("click", () => {
    isOpen = false;
    win.classList.remove("open");
  });

  // ── Markdown Parser (Lightweight) ──────────────────────────────────────────
  function parseMarkdown(text) {
    return text
      // Code blocks
      .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Bold
      .replace(/\*\*([^\*]+)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*([^\*]+)\*/g, '<em>$1</em>')
      // Bullet points
      .replace(/^\s*[\-\*]\s+(.*)$/gm, '<li>$1</li>')
      .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
      // Line breaks
      .replace(/\n/g, '<br/>');
  }

  // ── Message helpers ────────────────────────────────────────────────────────
  function addMessage(text, sender, save = true) {
    const el = document.createElement("div");
    el.className = `rag-w-msg ${sender}`;
    el.innerHTML = parseMarkdown(text);
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (save) {
      const history = JSON.parse(localStorage.getItem(`rag_history_${BOT_ID}`) || "[]");
      history.push({ text, sender });
      localStorage.setItem(`rag_history_${BOT_ID}`, JSON.stringify(history));
    }
    return el;
  }

  function loadHistory() {
    const history = JSON.parse(localStorage.getItem(`rag_history_${BOT_ID}`) || "[]");
    if (history.length > 0) {
      welcomed = true;
      history.forEach(msg => addMessage(msg.text, msg.sender, false));
    }
  }

  function clearHistory() {
    localStorage.removeItem(`rag_history_${BOT_ID}`);
    messagesEl.innerHTML = "";
    welcomed = false;
    addMessage(WELCOME_MSG, "bot");
    welcomed = true;
  }

  function addTyping() {
    const el = document.createElement("div");
    el.className = "rag-w-typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  // ── Send message ───────────────────────────────────────────────────────────
  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text || !BOT_ID) return;

    addMessage(text, "user");
    inputEl.value = "";
    sendBtn.disabled = true;
    inputEl.disabled = true;

    const typingEl = addTyping();

    try {
      const res = await fetch(`${API_URL}/api/widget/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bot_id: parseInt(BOT_ID),
          message: text,
          session_id: SESSION_ID
        }),
      });
      const data = await res.json();
      typingEl.remove();
      addMessage(data.response || data.detail || "No response.", "bot");
    } catch (err) {
      typingEl.remove();
      addMessage("Sorry, something went wrong. Please try again.", "bot");
    }

    sendBtn.disabled = false;
    inputEl.disabled = false;
    inputEl.focus();
  }

  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  clearBtn.addEventListener("click", () => {
    if (confirm("Clear this conversation?")) {
      clearHistory();
    }
  });

  // Initialize history
  loadHistory();
})();
