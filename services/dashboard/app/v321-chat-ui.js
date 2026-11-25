/* V3.2.1 P19A — AI Chat Workspace UI (Chat / Task / Plan & DAG).
 * Strictly XSS-safe: renders all user & AI content via textContent / safe DOM nodes (never raw innerHTML). */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  };
  const vnTime = v => (v && Number.isFinite(Date.parse(v)) ? new Date(v).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "—");

  let activeConvId = null;
  let loadEpoch = 0;

  function renderSafeContent(container, text) {
    // Split fenced code blocks ```...``` safely using textContent only (0 XSS risk)
    const parts = String(text || "").split(/```/);
    parts.forEach((part, idx) => {
      if (idx % 2 === 1) {
        const lines = part.split("\n");
        const lang = lines[0].trim();
        const code = lines.length > 1 ? lines.slice(1).join("\n") : part;
        const pre = el("pre", code);
        pre.style.whiteSpace = "pre-wrap";
        if (lang) pre.title = "Code: " + lang;
        container.append(pre);
      } else if (part.trim()) {
        const p = el("div", part.trim());
        p.style.whiteSpace = "pre-wrap";
        container.append(p);
      }
    });
  }

  async function initChatSelectors() {
    try {
      const [projs, bizs, provs] = await Promise.all([
        apiFetch("/api/projects"),
        apiFetch("/api/v31/businesses"),
        apiFetch("/api/v321/providers"),
      ]);
      const pSel = $("chat-proj-sel");
      if (pSel) {
        const cur = pSel.value;
        pSel.replaceChildren(...(projs.items || []).map(p => new Option(`${p.name} (${p.project_id.slice(0, 8)})`, p.project_id)));
        if (cur && (projs.items || []).some(p => p.project_id === cur)) pSel.value = cur;
      }
      const bSel = $("chat-biz-sel");
      if (bSel) {
        const cur = bSel.value;
        const activeBiz = (bizs.items || []).filter(b => !b.archived);
        bSel.replaceChildren(...activeBiz.map(b => new Option(b.name, b.id)));
        if (cur && activeBiz.some(b => b.id === cur)) bSel.value = cur;
      }
      const prSel = $("chat-prov-sel");
      if (prSel) {
        const cur = prSel.value;
        prSel.replaceChildren(...(provs.items || []).map(p => new Option(`${p.display_name} (${p.selected_model})${p.enabled ? "" : " [Tắt]"}`, p.id)));
        if (cur && (provs.items || []).some(p => p.id === cur)) prSel.value = cur;
      }
      await loadConversations();
    } catch (e) {
      if ($("chat-conv-list")) $("chat-conv-list").textContent = "Lỗi tải Chat Workspace: " + e.message;
    }
  }

  async function loadConversations() {
    const pid = $("chat-proj-sel")?.value;
    const listBox = $("chat-conv-list");
    if (!pid || !listBox) return;
    const myEpoch = ++loadEpoch;
    try {
      const params = new URLSearchParams({ project_id: pid });
      const q = ($("chat-search-inp")?.value || "").trim();
      if (q) params.set("search", q);
      const data = await apiFetch("/api/v321/chat/conversations?" + params);
      if (myEpoch !== loadEpoch) return; // Discard stale response on rapid switching
      listBox.replaceChildren();
      const items = data.items || [];
      if (!items.length) {
        listBox.append(el("p", "Chưa có cuộc trò chuyện nào. Bấm '+ Cuộc trò chuyện mới' để bắt đầu.", "trace-muted"));
        return;
      }
      for (const c of items) {
        const btn = el("button", `${c.title} (${c.message_count})`, `secondary${c.id === activeConvId ? " active" : ""}`);
        btn.type = "button";
        btn.style.width = "100%";
        btn.style.textAlign = "left";
        btn.style.marginBottom = "6px";
        btn.addEventListener("click", () => openConversation(c.id));
        listBox.append(btn);
      }
      if (activeConvId && items.some(c => c.id === activeConvId)) {
        await openConversation(activeConvId);
      }
    } catch (e) {
      listBox.textContent = "Lỗi tải danh sách: " + e.message;
    }
  }

  async function openConversation(convId) {
    const pid = $("chat-proj-sel")?.value;
    if (!pid || !convId) return;
    activeConvId = convId;
    const myEpoch = ++loadEpoch;
    try {
      const conv = await apiFetch(`/api/v321/chat/conversations/${encodeURIComponent(convId)}?project_id=${encodeURIComponent(pid)}`);
      if (myEpoch !== loadEpoch) return;
      $("chat-conv-title").textContent = `${conv.title} · [${conv.mode}]`;
      const box = $("chat-messages");
      box.replaceChildren();
      const msgs = conv.messages || [];
      if (!msgs.length) {
        box.append(el("p", "Cuộc trò chuyện mới sẵn sàng. Nhập câu hỏi bên dưới và bấm Gửi tin nhắn.", "trace-muted"));
        return;
      }
      for (const m of msgs) {
        const wrap = el("div", null, "card");
        wrap.style.marginBottom = "10px";
        wrap.style.borderLeft = m.role === "user" ? "3px solid var(--accent)" : "3px solid var(--ok)";
        const head = el("div", null, "row-actions");
        head.style.justifyContent = "space-between";
        const left = el("div", null, "row-actions");
        left.append(
          el("strong", m.role === "user" ? "Bạn" : `AI (${m.provider_key || m.mode}${m.model_id ? " / " + m.model_id : ""})`),
          el("span", m.delivery_status, `badge ${m.delivery_status === "delivered" ? "ok" : m.delivery_status === "missing" ? "warn" : "err"}`),
          el("small", vnTime(m.created_at), "trace-muted")
        );
        const right = el("div", null, "row-actions");
        const bCopy = el("button", "Copy", "secondary");
        bCopy.type = "button";
        bCopy.addEventListener("click", () => { navigator.clipboard?.writeText(m.content || ""); toast("Đã sao chép nội dung."); });
        right.append(bCopy);
        if (m.trace_id) {
          const bTr = el("button", "Trace", "secondary");
          bTr.type = "button";
          bTr.addEventListener("click", () => {
            showTab("traces", $("trace-nav"));
          });
          right.append(bTr);
        }
        head.append(left, right);
        wrap.append(head);

        const body = el("div");
        body.style.marginTop = "6px";
        renderSafeContent(body, m.content);
        wrap.append(body);

        if (m.role === "assistant") {
          const u = m.usage || {};
          const s = m.sources || {};
          const metaParts = [];
          if (u.input_tokens != null || u.output_tokens != null) {
            metaParts.push(`Token in=${u.input_tokens ?? "?"} / out=${u.output_tokens ?? "?"} / think=${u.reasoning_tokens ?? "—"}`);
          }
          if (u.estimated_cost_usd != null) metaParts.push(`Cost≈$${Number(u.estimated_cost_usd).toFixed(5)}`);
          if (s.memories != null) metaParts.push(`Memory=${s.memories}`);
          if ((s.skills || []).length) metaParts.push(`Skills=${s.skills.join(",")}`);
          if ((s.tools || []).length) metaParts.push(`MCP=${s.tools.join(",")}`);
          if (metaParts.length) wrap.append(el("small", metaParts.join(" · "), "trace-muted"));
        }
        box.append(wrap);
      }
      box.scrollTop = box.scrollHeight;
    } catch (e) {
      $("chat-messages").textContent = "Lỗi tải hội thoại: " + e.message;
    }
  }

  async function createNewConversation() {
    const pid = $("chat-proj-sel")?.value;
    const bid = $("chat-biz-sel")?.value;
    if (!pid || !bid) { toast("Chọn Dự án và Nghiệp vụ trước."); return; }
    const conv = await apiFetch("/api/v321/chat/conversations", {
      method: "POST",
      body: JSON.stringify({
        project_id: pid,
        business_id: bid,
        title: "Cuộc trò chuyện mới",
        mode: $("chat-mode-sel")?.value || "chat",
        provider_profile_id: $("chat-prov-sel")?.value || "prov-mock-deterministic",
      }),
    });
    activeConvId = conv.id;
    await loadConversations();
    await openConversation(conv.id);
    toast("Đã tạo cuộc trò chuyện mới.");
  }

  async function sendChatMessage() {
    const pid = $("chat-proj-sel")?.value;
    const text = ($("chat-input")?.value || "").trim();
    if (!pid || !text) { toast("Nhập nội dung tin nhắn."); return; }
    if (!activeConvId) {
      await createNewConversation();
      if (!activeConvId) return;
    }
    const btn = $("chat-send-btn");
    const st = $("chat-send-state");
    btn.disabled = true;
    st.textContent = "Đang xử lý...";
    try {
      const idem = `ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const res = await apiFetch(`/api/v321/chat/conversations/${encodeURIComponent(activeConvId)}/messages`, {
        method: "POST",
        body: JSON.stringify({
          project_id: pid,
          content: text,
          mode: $("chat-mode-sel")?.value || "chat",
          provider_profile_id: $("chat-prov-sel")?.value || "prov-mock-deterministic",
          thinking_level: $("chat-think-sel")?.value || "none",
          max_budget_usd: Number($("chat-budget-inp")?.value) || 0.05,
          allow_tools: Boolean($("chat-allow-tools")?.checked),
          egress_consent_confirmed: Boolean($("chat-consent-chk")?.checked),
          idempotency_key: idem,
        }),
      });
      $("chat-input").value = "";
      st.textContent = `Đã nhận (${res.assistant_message?.delivery_status || "ok"})`;
      await openConversation(activeConvId);
      await loadConversations();
    } catch (e) {
      st.textContent = "Lỗi: " + e.message;
      toast("Lỗi gửi tin nhắn: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  window.addEventListener("hub-ready", () => {
    $("chat-proj-sel")?.addEventListener("change", () => { activeConvId = null; loadConversations(); });
    $("chat-search-inp")?.addEventListener("input", loadConversations);
    $("chat-new-btn")?.addEventListener("click", createNewConversation);
    $("chat-send-btn")?.addEventListener("click", sendChatMessage);
    $("chat-input")?.addEventListener("keydown", e => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendChatMessage();
    });
    $("chat-export-btn")?.addEventListener("click", async () => {
      const pid = $("chat-proj-sel")?.value;
      if (!pid || !activeConvId) return;
      const exp = await apiFetch(`/api/v321/chat/conversations/${encodeURIComponent(activeConvId)}/export?project_id=${encodeURIComponent(pid)}`);
      const blob = new Blob([JSON.stringify(exp, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `chat-${activeConvId.slice(0, 8)}.json`;
      a.click();
    });
  });

  window.addEventListener("hub-tab", ev => {
    if (ev.detail === "chat") initChatSelectors();
  });
})();
