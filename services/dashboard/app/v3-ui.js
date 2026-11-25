/* V3 Dashboard UI: Conversation History (V3.2/V3.3) & Dynamic MCP Registry (V3.0). All values rendered via textContent. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const vnTime = v => (v && Number.isFinite(Date.parse(v)) ? new Date(v).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "Unknown");

  let convOffset = 0, convNext = null, selectedConv = null;

  function syncConvWorktrees() {
    const wts = (window.worktreesByProject || {})[$("conv-proj").value] || [];
    $("conv-wt").replaceChildren(new Option("Tất cả worktree", ""), ...wts.map(w => new Option(w.branch || w.worktree_id, w.worktree_id)));
  }

  async function loadConversations(reset = true) {
    if (reset) convOffset = 0;
    const pid = $("conv-proj").value;
    if (!pid) return;
    const q = $("conv-search").value.trim();
    const params = new URLSearchParams({ project_id: pid, limit: "30", offset: String(convOffset) });
    if ($("conv-wt").value) params.set("worktree_id", $("conv-wt").value);
    if ($("conv-client").value) params.set("client_type", $("conv-client").value);
    if ($("conv-model").value.trim()) params.set("model_id", $("conv-model").value.trim());

    const listBox = $("conv-list");
    listBox.textContent = "Đang tải...";
    try {
      if (q) {
        params.set("query", q);
        const res = await apiFetch("/api/v3/conversations/search?" + params);
        listBox.replaceChildren();
        const items = res.items || [];
        if (!items.length) {
          listBox.append(el("p", "Không tìm thấy đoạn hội thoại khớp truy vấn FTS5.", "trace-muted"));
          return;
        }
        for (const it of items) {
          const btn = el("button", null, "conv-list-item");
          btn.type = "button";
          btn.append(
            el("strong", it.title),
            el("small", `${it.client_type} · ${it.model_id || "Unknown"} · #${it.seq_no} (${it.role})`),
            el("small", it.excerpt, "trace-muted")
          );
          btn.addEventListener("click", () => openConversation(it.conversation_id));
          listBox.append(btn);
        }
        return;
      }
      const res = await apiFetch("/api/v3/conversations?" + params);
      convNext = res.next_offset;
      $("conv-prev").disabled = convOffset === 0;
      $("conv-next").disabled = convNext == null;
      listBox.replaceChildren();
      const items = res.items || [];
      if (!items.length) {
        listBox.append(el("p", "Chưa có hội thoại nào được đồng bộ (opt-in).", "trace-muted"));
        return;
      }
      for (const c of items) {
        const btn = el("button", null, `conv-list-item${selectedConv === c.id ? " is-selected" : ""}`);
        btn.type = "button";
        btn.append(
          el("strong", c.title),
          el("small", `${c.client_type} · ${c.model_id || "Unknown"} · ${c.message_count} tin`),
          el("small", vnTime(c.updated_at), "trace-muted")
        );
        btn.addEventListener("click", () => openConversation(c.id));
        listBox.append(btn);
      }
    } catch (e) {
      listBox.textContent = "Lỗi tải hội thoại: " + e.message;
    }
  }

  async function openConversation(convId) {
    selectedConv = convId;
    const pid = $("conv-proj").value;
    const pane = $("conv-messages");
    pane.textContent = "Đang tải nội dung hội thoại...";
    try {
      const data = await apiFetch(`/api/v3/conversations/${encodeURIComponent(convId)}?` + new URLSearchParams({ project_id: pid, limit: "100" }));
      const conv = data.conversation;
      const head = $("conv-detail-head");
      head.replaceChildren();
      head.append(
        el("h3", conv.title),
        el("small", `Client: ${conv.client_type} · Provider: ${conv.provider || "Unknown"} · Model: ${conv.model_id || "Unknown"} · Cập nhật: ${vnTime(conv.updated_at)}`)
      );
      const actions = el("div", null, "row-actions");
      if (conv.links?.session_id || conv.links?.plan_id) {
        const bTrace = el("button", "Mở Bản đồ thực thi", "secondary");
        bTrace.type = "button";
        bTrace.addEventListener("click", () => window.openExecutionMap(pid, conv.links.plan_id || "", conv.links.task_id || "", conv.links.session_id || ""));
        actions.append(bTrace);
      } else {
        actions.append(el("span", "Unlinked (chưa gắn Session/Task)", "badge muted"));
      }
      const bJson = el("button", "Xuất JSON", "secondary");
      bJson.type = "button";
      bJson.addEventListener("click", () => window.open(`/api/v3/conversations/${encodeURIComponent(convId)}/export?project_id=${encodeURIComponent(pid)}&format=json`, "_blank"));
      const bMd = el("button", "Xuất MD", "secondary");
      bMd.type = "button";
      bMd.addEventListener("click", () => window.open(`/api/v3/conversations/${encodeURIComponent(convId)}/export?project_id=${encodeURIComponent(pid)}&format=md`, "_blank"));
      const bDel = el("button", "Xóa hội thoại", "secondary");
      bDel.type = "button";
      bDel.addEventListener("click", async () => {
        if (!confirm("Xóa hội thoại này khỏi AI Memory Hub?")) return;
        await apiFetch(`/api/v3/conversations/${encodeURIComponent(convId)}?project_id=${encodeURIComponent(pid)}`, { method: "DELETE" });
        toast("Đã xóa hội thoại.");
        selectedConv = null;
        pane.textContent = "Chọn hội thoại bên trái.";
        head.replaceChildren();
        loadConversations(true);
      });
      actions.append(bJson, bMd, bDel);
      head.append(actions);

      pane.replaceChildren();
      for (const m of data.messages || []) {
        const card = el("div", null, `conv-msg conv-msg-${m.role}`);
        const top = el("div", null, "trace-page-head");
        top.append(
          el("strong", `${m.role === "user" ? "Người dùng" : "AI Agent"} #${m.seq_no}`),
          el("small", `${vnTime(m.created_at)}${m.model_id ? " · " + m.model_id : ""}${m.input_tokens != null ? ` · in:${m.input_tokens}/out:${m.output_tokens ?? "?"}` : ""}`, "trace-muted")
        );
        card.append(top);
        const text = m.visible_text || "";
        if (text.length > 600) {
          const pre = el("pre", text.slice(0, 600) + "…", "conv-msg-text");
          const tog = el("button", "Mở rộng", "secondary");
          tog.type = "button";
          let open = false;
          tog.addEventListener("click", () => {
            open = !open;
            pre.textContent = open ? text : text.slice(0, 600) + "…";
            tog.textContent = open ? "Thu gọn" : "Mở rộng";
          });
          card.append(pre, tog);
        } else {
          card.append(el("pre", text, "conv-msg-text"));
        }
        const bProm = el("button", "Lưu thành Candidate Memory", "secondary");
        bProm.type = "button";
        bProm.addEventListener("click", async () => {
          const title = prompt("Tiêu đề Candidate Memory:", `${conv.title.slice(0, 60)} #${m.seq_no}`);
          if (!title) return;
          const r = await apiFetch(`/api/v3/conversations/${encodeURIComponent(convId)}/promote`, {
            method: "POST",
            body: JSON.stringify({ project_id: pid, message_id: m.id, title }),
          });
          toast("Đã tạo Candidate Memory: " + r.memory_id.slice(0, 8));
        });
        card.append(bProm);
        pane.append(card);
      }
    } catch (e) {
      pane.textContent = "Lỗi tải nội dung: " + e.message;
    }
  }

  async function loadMcpRegistry() {
    const box = $("mcp-servers-list");
    const auditBox = $("mcp-audit-list");
    box.textContent = "Đang tải Dynamic MCP Registry...";
    try {
      const [srvs, aud] = await Promise.all([apiFetch("/api/v3/mcp/servers"), apiFetch("/api/v3/mcp/audit?limit=30")]);
      box.replaceChildren();
      for (const s of srvs.items || []) {
        const card = el("div", null, "card");
        const top = el("div", null, "trace-page-head");
        top.append(el("h3", `${s.name} (${s.version})`), el("span", `${s.trust_tier} · ${s.approval_state} · ${s.health_status}`, `badge ${s.approval_state === "approved" ? "ok" : "warn"}`));
        card.append(top, el("p", `Transport: ${s.transport} · Endpoint: ${s.endpoint_or_cmd}`, "trace-muted"));
        const tList = el("ul");
        for (const t of s.tools || []) {
          tList.append(el("li", `${t.tool_name} [${t.side_effect_level}] — ${(t.capability_tags || []).join(", ")} (${t.description})`));
        }
        card.append(tList);
        if (s.trust_tier !== "builtin") {
          const dec = s.approval_state === "approved" ? "revoke" : "approve";
          const b = el("button", dec === "approve" ? "Phê duyệt kích hoạt" : "Thu hồi quyền", "secondary");
          b.type = "button";
          b.addEventListener("click", async () => {
            await apiFetch(`/api/v3/mcp/servers/${encodeURIComponent(s.id)}/approval`, { method: "POST", body: JSON.stringify({ decision: dec }) });
            toast("Đã cập nhật trạng thái MCP server.");
            loadMcpRegistry();
          });
          card.append(b);
        }
        box.append(card);
      }
      auditBox.replaceChildren();
      const tbl = el("table", null, "trace-table");
      tbl.append(el("tr"));
      for (const h of ["Thời gian (VN)", "Hành động", "Trạng thái", "Actor", "Server"]) tbl.firstChild.append(el("th", h));
      for (const a of aud.items || []) {
        const tr = el("tr");
        for (const v of [vnTime(a.created_at), a.action, a.status, a.actor, a.server_id || "—"]) tr.append(el("td", v));
        tbl.append(tr);
      }
      auditBox.append(tbl);
    } catch (e) {
      box.textContent = "Lỗi tải MCP Registry: " + e.message;
    }
  }

  window.addEventListener("hub-ready", () => {
    syncConvWorktrees();
    $("conv-proj").addEventListener("change", () => { syncConvWorktrees(); loadConversations(true); });
    $("conv-refresh").addEventListener("click", () => loadConversations(true));
    $("conv-search").addEventListener("keydown", e => { if (e.key === "Enter") loadConversations(true); });
    $("conv-prev").addEventListener("click", () => { convOffset = Math.max(0, convOffset - 30); loadConversations(false); });
    $("conv-next").addEventListener("click", () => { if (convNext != null) { convOffset = convNext; loadConversations(false); } });
    $("mcp-refresh").addEventListener("click", loadMcpRegistry);
    $("mcp-route-btn").addEventListener("click", async () => {
      const pid = $("mcp-proj").value;
      const caps = $("mcp-route-caps").value.split(",").map(s => s.trim()).filter(Boolean);
      if (!pid || !caps.length) return;
      try {
        const res = await apiFetch("/api/v3/mcp/route", {
          method: "POST",
          body: JSON.stringify({ project_id: pid, required_capabilities: caps, allow_write: $("mcp-route-write").checked }),
        });
        $("mcp-route-result").replaceChildren(el("pre", JSON.stringify(res, null, 2)));
        loadMcpRegistry();
      } catch (e) {
        $("mcp-route-result").textContent = e.message;
      }
    });
    window.addEventListener("hub-tab", e => {
      if (e.detail === "conversations") { syncConvWorktrees(); loadConversations(true); }
      if (e.detail === "mcp") loadMcpRegistry();
    });
  });
})();
