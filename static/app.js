/* ============================================================
   AI Chat — 프론트엔드 로직
   ============================================================ */
(() => {
  "use strict";

  // ------------------------------------------------------------ 상태
  const state = {
    conversations: [],
    currentId: null,
    model: null,
    streaming: false,
    abort: null,
    attachments: [], // { dataUrl }
    stick: true,
    suppressAbort: false, // 대화 전환 등 프로그램적 중지 시 토스트 억제
  };

  // ------------------------------------------------------------ DOM
  const $ = (sel) => document.querySelector(sel);
  const app = $("#app");
  const chat = $("#chat");
  const chatScroll = $("#chatScroll");
  const welcome = $("#welcome");
  const convList = $("#convList");
  const input = $("#input");
  const composer = $("#composer");
  const sendBtn = $("#sendBtn");
  const attachmentsEl = $("#attachments");
  const fileInput = $("#fileInput");
  const modelSelect = $("#modelSelect");
  const topbarTitle = $("#topbarTitle");
  const dropOverlay = $("#dropOverlay");
  const scrollBottomBtn = $("#scrollBottom");
  const toastHost = $("#toastHost");

  const ICON = {
    copy: '<svg viewBox="0 0 24 24" width="15" height="15"><rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5 15V5a2 2 0 012-2h8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    regen: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M21 12a9 9 0 11-3-6.7M21 4v4h-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M4 20h4L18 10l-4-4L4 16v4zM14 6l4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M5 13l4 4L19 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // ------------------------------------------------------------ util
  const esc = (s) =>
    s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function toast(msg, kind = "") {
    const t = document.createElement("div");
    t.className = "toast" + (kind ? " " + kind : "");
    t.textContent = msg;
    toastHost.appendChild(t);
    setTimeout(() => {
      t.style.transition = "opacity .3s, transform .3s";
      t.style.opacity = "0";
      t.style.transform = "translateY(8px)";
      setTimeout(() => t.remove(), 300);
    }, 2600);
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch {}
      ta.remove();
    }
    if (btn) {
      const prev = btn.innerHTML;
      btn.innerHTML = ICON.check + "<span>복사됨</span>";
      setTimeout(() => (btn.innerHTML = prev), 1400);
    }
  }

  // ------------------------------------------------------------ 마크다운
  if (window.marked) marked.setOptions({ gfm: true, breaks: true });
  if (window.DOMPurify) {
    DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
      if (node.tagName === "IMG") node.setAttribute("loading", "lazy");
    });
  }

  // 수식 하나를 KaTeX HTML 로 렌더. 실패/미로드 시 null → 원문 유지
  function katexHtml(tex, display) {
    if (!window.katex) return null;
    try {
      return katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false });
    } catch {
      return null;
    }
  }

  // 마크다운 파싱 "전에" 수식을 추출해 렌더한다.
  // 이유: LLM 이 $$ 를 줄바꿈해 출력하면 마크다운 파서가 여는/닫는 구분자를 서로 다른
  // 문단으로 쪼개, 파싱 후 렌더로는 여러 줄 블록 수식($$...$$)의 짝을 못 맞춘다.
  function extractMath(text, math) {
    // 1) 코드(펜스/인라인) 보호 — 코드 안의 $ 를 수식으로 오인하지 않게
    const code = [];
    let src = text.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`/g, (m) => {
      code.push(m);
      return `C${code.length - 1}`;
    });
    const stash = (tex, display) => {
      const html = katexHtml(tex, display);
      if (html == null) return null; // 렌더 실패 → 원문 유지 (?? m)
      math.push(html);
      return `K${math.length - 1}`;
    };
    // 2) 수식 추출 (display 먼저: $$, \[ \]  →  inline: \( \), $ )
    src = src.replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => stash(t, true) ?? m);
    src = src.replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => stash(t, true) ?? m);
    src = src.replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => stash(t, false) ?? m);
    // 인라인 $...$: 여는 $ 뒤·닫는 $ 앞이 공백이면 매칭 안 함 → "$5 and $10" 통화 오인 방지
    src = src.replace(/\$(?!\s)([^\n$]*?[^\s$])\$/g, (m, t) => stash(t, false) ?? m);
    // 3) 코드 복원 (마크다운이 정상적으로 코드로 처리하도록)
    src = src.replace(/C(\d+)/g, (m, i) => code[i]);
    return src;
  }

  function renderMarkdown(el, text, highlight) {
    // 새니타이저/파서가 없으면 fail-closed: 원시 HTML 주입 대신 평문 표시
    if (!window.marked || !window.DOMPurify) {
      el.textContent = text;
      return;
    }
    const math = [];
    const src = window.katex ? extractMath(text, math) : text;
    let html = DOMPurify.sanitize(marked.parse(src));
    // 수식 플레이스홀더 복원 — KaTeX 출력 주입 (renderToString 은 기본값 trust:false 로 안전)
    if (math.length) html = html.replace(/K(\d+)/g, (m, i) => math[i] || "");
    el.innerHTML = html;
    if (highlight) enhanceCode(el);
  }

  function enhanceCode(container) {
    container.querySelectorAll("pre > code").forEach((code) => {
      const pre = code.parentElement;
      if (pre.parentElement && pre.parentElement.classList.contains("code-block")) return;
      const cls = [...code.classList].find((c) => c.startsWith("language-"));
      const lang = cls ? cls.slice(9) : "";
      if (window.hljs) { try { hljs.highlightElement(code); } catch {} }

      const wrap = document.createElement("div");
      wrap.className = "code-block";
      const head = document.createElement("div");
      head.className = "code-block__head";
      const label = document.createElement("span");
      label.textContent = lang || "text";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy";
      btn.innerHTML = ICON.copy + "<span>복사</span>";
      btn.addEventListener("click", () => copyText(code.textContent, btn));
      head.append(label, btn);
      pre.replaceWith(wrap);
      wrap.append(head, pre);
    });
  }

  // ------------------------------------------------------------ API
  const api = {
    async models() { return (await fetch("/api/models")).json(); },
    async list() { return (await fetch("/api/conversations")).json(); },
    async get(id) {
      const r = await fetch(`/api/conversations/${id}`);
      if (!r.ok) throw new Error("대화를 불러오지 못했습니다");
      return r.json();
    },
    async rename(id, title) {
      return fetch(`/api/conversations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
    },
    async del(id) { return fetch(`/api/conversations/${id}`, { method: "DELETE" }); },
  };

  // ------------------------------------------------------------ 사이드바
  function dayBucket(ts) {
    const d = new Date(ts), now = new Date();
    const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((startOf(now) - startOf(d)) / 86400000);
    if (days <= 0) return "오늘";
    if (days === 1) return "어제";
    if (days <= 7) return "지난 7일";
    if (days <= 30) return "지난 30일";
    return "이전";
  }

  function renderSidebar() {
    convList.innerHTML = "";
    if (state.conversations.length === 0) {
      const e = document.createElement("div");
      e.className = "conv-empty";
      e.textContent = "아직 대화가 없습니다.\n새 대화를 시작해 보세요.";
      convList.appendChild(e);
      return;
    }
    let lastBucket = null;
    for (const c of state.conversations) {
      const bucket = dayBucket(c.updated_at);
      if (bucket !== lastBucket) {
        const h = document.createElement("div");
        h.className = "conv-day";
        h.textContent = bucket;
        convList.appendChild(h);
        lastBucket = bucket;
      }
      convList.appendChild(convItem(c));
    }
  }

  function convItem(c) {
    const item = document.createElement("div");
    item.className = "conv-item" + (c.id === state.currentId ? " active" : "");
    item.dataset.id = c.id;
    item.setAttribute("role", "button");
    item.tabIndex = 0;
    item.setAttribute("aria-label", (c.title || "새 대화") + " 대화 열기");

    const title = document.createElement("div");
    title.className = "conv-item__title";
    title.textContent = c.title || "새 대화";

    const actions = document.createElement("div");
    actions.className = "conv-item__actions";
    const editBtn = document.createElement("button");
    editBtn.className = "conv-act";
    editBtn.title = "이름 변경";
    editBtn.innerHTML = ICON.edit;
    const delBtn = document.createElement("button");
    delBtn.className = "conv-act danger";
    delBtn.title = "삭제";
    delBtn.innerHTML = ICON.trash;
    actions.append(editBtn, delBtn);

    item.append(title, actions);

    item.addEventListener("click", (e) => {
      if (e.target.closest(".conv-item__actions") || title.querySelector("input")) return;
      openConversation(c.id);
      closeSidebarMobile();
    });
    item.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && !title.querySelector("input") && e.target === item) {
        e.preventDefault();
        openConversation(c.id);
        closeSidebarMobile();
      }
    });
    editBtn.addEventListener("click", (e) => { e.stopPropagation(); startRename(c, title); });
    delBtn.addEventListener("click", (e) => { e.stopPropagation(); removeConversation(c); });
    return item;
  }

  function startRename(c, titleEl) {
    const old = c.title || "새 대화";
    titleEl.innerHTML = "";
    const inp = document.createElement("input");
    inp.value = old;
    titleEl.appendChild(inp);
    inp.focus();
    inp.select();
    const commit = async () => {
      const v = (inp.value.trim() || old).slice(0, 120);
      titleEl.textContent = v;
      if (v !== old) {
        c.title = v;
        await api.rename(c.id, v);
        if (c.id === state.currentId) topbarTitle.textContent = v;
      }
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
      if (e.key === "Escape") { titleEl.textContent = old; }
    });
    inp.addEventListener("blur", commit, { once: true });
  }

  async function removeConversation(c) {
    if (!confirm(`"${c.title || "새 대화"}" 대화를 삭제할까요?`)) return;
    await api.del(c.id);
    state.conversations = state.conversations.filter((x) => x.id !== c.id);
    if (state.currentId === c.id) newChat();
    renderSidebar();
    toast("대화를 삭제했습니다.");
  }

  async function refreshConversations() {
    const { conversations } = await api.list();
    state.conversations = conversations;
    renderSidebar();
  }

  // ------------------------------------------------------------ 대화 렌더
  function clearChat() { chat.innerHTML = ""; }

  function updateWelcome() {
    const hasMsgs = chat.querySelector(".msg");
    welcome.classList.toggle("hidden", !!hasMsgs);
  }

  function messageEl(role) {
    const msg = document.createElement("div");
    msg.className = `msg msg--${role}`;
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "나" : "✦";
    const body = document.createElement("div");
    body.className = "msg__body";
    const roleEl = document.createElement("div");
    roleEl.className = "msg__role";
    roleEl.textContent = role === "user" ? "나" : "AI";
    body.appendChild(roleEl);
    msg.append(avatar, body);
    return { msg, body };
  }

  function imagesRow(urls) {
    const row = document.createElement("div");
    row.className = "msg__images";
    for (const u of urls) {
      const img = document.createElement("img");
      img.src = u;
      img.addEventListener("click", () => openLightbox(u));
      row.appendChild(img);
    }
    return row;
  }

  function addUserMessage(text, imageUrls) {
    const { msg, body } = messageEl("user");
    if (imageUrls && imageUrls.length) body.appendChild(imagesRow(imageUrls));
    const content = document.createElement("div");
    content.className = "msg__content";
    content.style.whiteSpace = "pre-wrap";
    content.textContent = text;
    body.appendChild(content);
    chat.appendChild(msg);
    updateWelcome();
    return msg;
  }

  function addAssistantPlaceholder() {
    const { msg, body } = messageEl("assistant");
    const content = document.createElement("div");
    content.className = "msg__content md";
    const typing = document.createElement("div");
    typing.className = "typing";
    typing.innerHTML = "<span></span><span></span><span></span>";
    content.appendChild(typing);
    body.appendChild(content);
    chat.appendChild(msg);
    updateWelcome();
    return { msg, content };
  }

  function addAssistantActions(msg, contentEl, rawText) {
    const body = msg.querySelector(".msg__body");
    body.querySelector(".msg__actions")?.remove();
    const actions = document.createElement("div");
    actions.className = "msg__actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "msg-act";
    copyBtn.innerHTML = ICON.copy + "<span>복사</span>";
    copyBtn.addEventListener("click", () => copyText(rawText, copyBtn));
    actions.append(copyBtn);
    body.appendChild(actions);
    refreshRegenButton();
  }

  // '다시 생성'은 마지막 assistant 메시지에만 노출 (재생성은 항상 마지막 턴 대상)
  function refreshRegenButton() {
    chat.querySelectorAll(".msg-act--regen").forEach((b) => b.remove());
    const last = [...chat.querySelectorAll(".msg--assistant")].pop();
    const actions = last && last.querySelector(".msg__actions");
    if (!actions) return;
    const regenBtn = document.createElement("button");
    regenBtn.className = "msg-act msg-act--regen";
    regenBtn.innerHTML = ICON.regen + "<span>다시 생성</span>";
    regenBtn.addEventListener("click", () => regenerate());
    actions.appendChild(regenBtn);
  }

  // 스트리밍 렌더 스로틀 (rAF)
  let _raf = null, _pendingEl = null, _pendingText = "";
  function scheduleRender(el, text) {
    _pendingEl = el; _pendingText = text;
    if (_raf) return;
    _raf = requestAnimationFrame(() => {
      _raf = null;
      renderMarkdown(_pendingEl, _pendingText, false);
      _pendingEl.classList.add("cursor");
      maybeAutoScroll();
    });
  }

  // ------------------------------------------------------------ 스크롤
  function scrollToBottom(force) {
    if (force) state.stick = true;
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }
  function maybeAutoScroll() { if (state.stick) chatScroll.scrollTop = chatScroll.scrollHeight; }
  chatScroll.addEventListener("scroll", () => {
    const dist = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight;
    state.stick = dist < 120;
    scrollBottomBtn.classList.toggle("show", dist > 240);
  });
  scrollBottomBtn.addEventListener("click", () => scrollToBottom(true));

  // ------------------------------------------------------------ 스트리밍 핵심
  async function runStream(url, bodyObj) {
    const { msg, content } = addAssistantPlaceholder();
    setStreaming(true);
    scrollToBottom(true);
    const controller = new AbortController();
    state.abort = controller;
    let acc = "", started = false;

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyObj),
        signal: controller.signal,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `요청 실패 (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line); } catch { continue; }
          handleEvent(ev, {
            onStart: (e) => {
              if (!state.currentId) state.currentId = e.conversation_id;
              else state.currentId = e.conversation_id;
            },
            onDelta: (text) => {
              if (!started) { started = true; content.innerHTML = ""; }
              acc += text;
              scheduleRender(content, acc);
            },
            onTitle: (title) => {
              topbarTitle.textContent = title;
              const c = state.conversations.find((x) => x.id === state.currentId);
              if (c) { c.title = title; renderSidebar(); }
            },
            onError: (m) => { toast(m, "error"); },
            onDone: (c) => { if (c) acc = c; },
          });
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        if (!state.suppressAbort) toast("생성을 중지했습니다.");
      } else {
        toast(e.message || "오류가 발생했습니다.", "error");
        if (!acc) acc = "⚠️ 응답을 받지 못했습니다.";
      }
    } finally {
      if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
      content.classList.remove("cursor");
      renderMarkdown(content, acc || " ", true);
      addAssistantActions(msg, content, acc);
      setStreaming(false);
      state.abort = null;
      state.suppressAbort = false;
      maybeAutoScroll();
      await refreshConversations();
    }
  }

  function handleEvent(ev, cb) {
    switch (ev.type) {
      case "start": cb.onStart(ev); break;
      case "meta": break;
      case "delta": cb.onDelta(ev.text || ""); break;
      case "title": cb.onTitle(ev.title); break;
      case "error": cb.onError(ev.message); break;
      case "done": cb.onDone(ev.content); break;
    }
  }

  function setStreaming(on) {
    state.streaming = on;
    app.classList.toggle("streaming", on);
    sendBtn.setAttribute("aria-label", on ? "생성 중지" : "전송");
    sendBtn.title = on ? "생성 중지" : "전송";
    updateSendEnabled();
  }

  // ------------------------------------------------------------ 전송 / 재생성
  async function send() {
    if (state.streaming) return;
    const text = input.value.trim();
    const imgs = state.attachments.map((a) => a.dataUrl);
    if (!text && imgs.length === 0) return;

    addUserMessage(text, imgs);
    input.value = "";
    autoGrow();
    state.attachments = [];
    renderAttachments();
    updateSendEnabled();

    await runStream("/api/chat", {
      conversation_id: state.currentId,
      message: text,
      images: imgs,
      model: state.model,
    });
  }

  async function regenerate() {
    if (state.streaming || !state.currentId) return;
    // 마지막 assistant 메시지 요소 제거
    const msgs = [...chat.querySelectorAll(".msg--assistant")];
    if (msgs.length) msgs[msgs.length - 1].remove();
    await runStream(`/api/conversations/${state.currentId}/regenerate`, { model: state.model });
  }

  function stopGeneration() { if (state.abort) state.abort.abort(); }

  // ------------------------------------------------------------ 대화 열기
  async function openConversation(id) {
    if (state.streaming) { state.suppressAbort = true; stopGeneration(); }
    try {
      const conv = await api.get(id);
      state.currentId = id;
      if (conv.model && [...modelSelect.options].some((o) => o.value === conv.model)) {
        modelSelect.value = conv.model;
        state.model = conv.model;
      }
      topbarTitle.textContent = conv.title || "새 대화";
      clearChat();
      for (const m of conv.messages) {
        if (m.role === "user") {
          addUserMessage(m.content, m.images);
        } else {
          const { msg, content } = addAssistantPlaceholder();
          content.innerHTML = "";
          renderMarkdown(content, m.content || " ", true);
          addAssistantActions(msg, content, m.content || "");
        }
      }
      updateWelcome();
      renderSidebar();
      scrollToBottom(true);
    } catch (e) {
      toast(e.message || "대화를 불러오지 못했습니다.", "error");
    }
  }

  function newChat() {
    if (state.streaming) { state.suppressAbort = true; stopGeneration(); }
    state.currentId = null;
    clearChat();
    topbarTitle.textContent = "새 대화";
    state.attachments = [];
    renderAttachments();
    updateWelcome();
    renderSidebar();
    input.focus();
  }

  // ------------------------------------------------------------ 첨부 이미지
  function renderAttachments() {
    attachmentsEl.innerHTML = "";
    state.attachments.forEach((a, i) => {
      const t = document.createElement("div");
      t.className = "attach-thumb";
      const img = document.createElement("img");
      img.src = a.dataUrl;
      const x = document.createElement("button");
      x.className = "attach-thumb__x";
      x.type = "button";
      x.textContent = "✕";
      x.addEventListener("click", () => {
        state.attachments.splice(i, 1);
        renderAttachments();
        updateSendEnabled();
      });
      t.append(img, x);
      attachmentsEl.appendChild(t);
    });
  }

  function addFiles(files) {
    let imgs = [...files].filter((f) => f.type.startsWith("image/"));
    const room = 8 - state.attachments.length; // 비동기 push 전에 동기적으로 여유 계산
    if (imgs.length > room) {
      toast("이미지는 최대 8장까지 첨부할 수 있습니다.", "error");
      imgs = imgs.slice(0, Math.max(0, room));
    }
    for (const f of imgs) {
      const reader = new FileReader();
      reader.onload = () => {
        state.attachments.push({ dataUrl: reader.result });
        renderAttachments();
        updateSendEnabled();
      };
      reader.readAsDataURL(f);
    }
  }

  // ------------------------------------------------------------ 라이트박스
  let lightbox = null;
  function openLightbox(src) {
    if (!lightbox) {
      lightbox = document.createElement("div");
      lightbox.className = "lightbox";
      lightbox.innerHTML = '<img alt="">';
      lightbox.addEventListener("click", () => lightbox.classList.remove("show"));
      document.body.appendChild(lightbox);
    }
    lightbox.querySelector("img").src = src;
    lightbox.classList.add("show");
  }

  // ------------------------------------------------------------ 입력 UX
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
  }
  function updateSendEnabled() {
    const has = input.value.trim() || state.attachments.length;
    sendBtn.disabled = !state.streaming && !has;
  }

  input.addEventListener("input", () => { autoGrow(); updateSendEnabled(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      if (state.streaming) return;
      send();
    }
  });
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.streaming) { stopGeneration(); return; }
    send();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.streaming) stopGeneration();
  });

  $("#attachBtn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
  input.addEventListener("paste", (e) => {
    const items = [...(e.clipboardData?.items || [])].filter((i) => i.type.startsWith("image/"));
    if (items.length) {
      e.preventDefault();
      addFiles(items.map((i) => i.getAsFile()).filter(Boolean));
    }
  });

  // 드래그 앤 드롭
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    dragDepth++;
    dropOverlay.classList.add("show");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; dropOverlay.classList.remove("show"); } });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    dropOverlay.classList.remove("show");
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
  });

  // ------------------------------------------------------------ 사이드바 토글 / 테마
  function closeSidebarMobile() { if (window.innerWidth <= 860) app.classList.add("sidebar-hidden"); }
  $("#menuBtn").addEventListener("click", () => app.classList.toggle("sidebar-hidden"));
  $("#collapseBtn").addEventListener("click", () => app.classList.toggle("sidebar-hidden"));
  $("#scrim").addEventListener("click", () => app.classList.add("sidebar-hidden"));
  $("#newChatBtn").addEventListener("click", () => { newChat(); closeSidebarMobile(); });

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("aichat-theme", theme);
  }
  $("#themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    applyTheme(cur === "dark" ? "light" : "dark");
  });

  // 예시 프롬프트
  document.querySelectorAll(".example").forEach((b) =>
    b.addEventListener("click", () => {
      input.value = b.dataset.prompt;
      autoGrow();
      updateSendEnabled();
      input.focus();
    })
  );

  modelSelect.addEventListener("change", () => { state.model = modelSelect.value; });

  // ------------------------------------------------------------ 초기화
  async function init() {
    applyTheme(localStorage.getItem("aichat-theme") || "dark");
    if (window.innerWidth <= 860) app.classList.add("sidebar-hidden");

    try {
      const { models, default: def } = await api.models();
      modelSelect.innerHTML = "";
      for (const m of models) {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label;
        opt.title = m.desc || "";
        modelSelect.appendChild(opt);
      }
      state.model = def;
      modelSelect.value = def;
    } catch {
      toast("모델 목록을 불러오지 못했습니다.", "error");
    }

    await refreshConversations();
    updateWelcome();
    updateSendEnabled();
    autoGrow();
  }

  init();
})();
