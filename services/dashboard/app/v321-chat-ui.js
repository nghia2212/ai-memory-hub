/* V3.2.1 P19A.1 / P19A.2 — AI Chat Workspace UI (Separate Execution Source & Model + Subscription Agents + Manual Handoff).
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
  const QUOTA_LABEL = {
    observed_available: "Có hạn mức",
    exhausted: "Hết quota",
    unknown: "Quota chưa rõ",
  };

  let activeConvId = null;
  let loadEpoch = 0;
  let sourcesCache = [];
  let defaultsCache = null;

  function renderSafeContent(container, text) {
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

  function findSource(sourceKey) {
    return sourcesCache.find(s => s.source_key === sourceKey) || null;
  }

  function renderSourceMeta() {
    const metaBox = $("chat-source-meta");
    if (!metaBox) return;
    const skey = $("chat-source-sel")?.value;
    const mid = $("chat-model-sel")?.value;
    const src = findSource(skey);
    metaBox.replaceChildren();
    if (!src) {
      metaBox.append(el("span", "Chọn Nguồn thực thi và Model để bắt đầu.", "trace-muted"));
      return;
    }
    const left = el("div", null, "row-actions");
    left.style.flexWrap = "wrap";
    left.style.gap = "6px";

    const billingCls = src.group === "subscription_cli" ? "ok" : (src.group === "simulation" ? "warn" : (src.group === "local_compute" ? "ok" : "err"));
    left.append(el("span", src.billing_label, `badge ${billingCls}`));
    left.append(el("span", src.admin_enabled ? "Agent: Bật" : "Agent: Đã tắt", `badge ${src.admin_enabled ? "ok" : "err"}`));
    left.append(el("span", `Runtime: ${src.runtime_status}${src.cli_version ? " (" + src.cli_version + ")" : ""}`, `badge ${src.runtime_status === "ready" ? "ok" : "warn"}`));

    const mObj = (src.models || []).find(m => m.model_id === mid);
    if (mObj) {
      const qst = mObj.quota?.state || "unknown";
      left.append(el("span", `Quota: ${QUOTA_LABEL[qst] || qst}`, `badge ${qst === "exhausted" ? "err" : (qst === "observed_available" ? "ok" : "warn")}`));
      if (!mObj.eligible && mObj.ineligible_reason) {
        left.append(el("small", `⚠ ${mObj.ineligible_reason}`, "trace-muted"));
      }
    } else if (src.group === "subscription_cli" && !(src.models || []).length) {
      left.append(el("small", src.runtime_note || "Chưa có model xác minh trong catalog.", "trace-muted"));
    }

    if (defaultsCache && !defaultsCache.persisted && defaultsCache.recommendation_reason) {
      left.append(el("small", `💡 ${defaultsCache.recommendation_reason}`, "trace-muted"));
    }

    const right = el("div", null, "row-actions");
    const btnCtrl = el("button", "Mở Agent Control Center", "secondary");
    btnCtrl.type = "button";
    btnCtrl.style.marginTop = "0";
    btnCtrl.addEventListener("click", () => showTab("agents", document.querySelector('[data-tab="agents"]')));
    right.append(btnCtrl);

    metaBox.append(left, right);

    // Update mode selector capability states honestly
    const modeSel = $("chat-mode-sel");
    if (modeSel) {
      for (const opt of modeSel.options) {
        const capOk = opt.value === "plan_dag" ? Boolean(src.capabilities?.plan_dag) : (Boolean(src.capabilities?.[opt.value]) || Boolean(src.capabilities?.manual_handoff));
        opt.disabled = !capOk;
      }
    }
  }

  function populateModelsForSource(sourceKey, preferredModelId = "") {
    const mSel = $("chat-model-sel");
    if (!mSel) return;
    const src = findSource(sourceKey);
    mSel.replaceChildren();
    if (!src) {
      mSel.append(new Option("(Chưa chọn nguồn)", ""));
      renderSourceMeta();
      return;
    }
    const models = src.models || [];
    if (!models.length) {
      const o = new Option("(Chưa có model xác minh — dùng Bàn giao IDE hoặc đồng bộ Catalog)", "");
      mSel.append(o);
      renderSourceMeta();
      return;
    }
    if (src.group === "subscription_cli") {
      mSel.append(new Option("Tự động chọn model khả dụng (0 phí API)", ""));
    }
    for (const m of models) {
      const qLabel = QUOTA_LABEL[m.quota?.state] || "Quota chưa rõ";
      const statusTag = !m.admin_enabled ? "Đã tắt" : (m.quota?.state === "exhausted" ? "Hết quota" : qLabel);
      const opt = new Option(`${m.display_name || m.model_id} · [${statusTag}]`, m.model_id);
      if (!m.eligible) opt.disabled = true;
      mSel.append(opt);
    }
    if (preferredModelId && models.some(m => m.model_id === preferredModelId && m.eligible)) {
      mSel.value = preferredModelId;
    } else {
      const firstEligible = models.find(m => m.eligible);
      if (firstEligible) mSel.value = firstEligible.model_id;
    }
    renderSourceMeta();
  }

  async function loadExecutionSources(applyProjectDefaults = false) {
    const pid = $("chat-proj-sel")?.value;
    const params = new URLSearchParams({ probe_host: "true" });
    if (pid) params.set("project_id", pid);
    const data = await apiFetch("/api/v321/execution-sources?" + params);
    sourcesCache = data.sources || [];
    defaultsCache = data.defaults || null;

    const sSel = $("chat-source-sel");
    if (sSel) {
      const cur = sSel.value;
      sSel.replaceChildren();
      const groupsOrder = ["subscription_cli", "local_compute", "metered_api", "simulation"];
      for (const g of groupsOrder) {
        const items = sourcesCache.filter(s => s.group === g);
        if (!items.length) continue;
        const og = document.createElement("optgroup");
        og.label = items[0].group_label || g;
        for (const s of items) {
          const tag = !s.admin_enabled ? " [Đã tắt]" : (s.runtime_status === "ready" ? "" : ` [${s.runtime_status}]`);
          const opt = new Option(`${s.display_name}${tag}`, s.source_key);
          og.append(opt);
        }
        sSel.append(og);
      }
      if (applyProjectDefaults && defaultsCache) {
        if (defaultsCache.business_id && $("chat-biz-sel")) {
          const bSel = $("chat-biz-sel");
          if ([...bSel.options].some(o => o.value === defaultsCache.business_id)) bSel.value = defaultsCache.business_id;
        }
        if (defaultsCache.mode && $("chat-mode-sel")) $("chat-mode-sel").value = defaultsCache.mode;
        if (defaultsCache.execution_source && sourcesCache.some(s => s.source_key === defaultsCache.execution_source)) {
          sSel.value = defaultsCache.execution_source;
        }
      } else if (cur && sourcesCache.some(s => s.source_key === cur)) {
        sSel.value = cur;
      } else if (defaultsCache?.execution_source && sourcesCache.some(s => s.source_key === defaultsCache.execution_source)) {
        sSel.value = defaultsCache.execution_source;
      }
      populateModelsForSource(sSel.value, (applyProjectDefaults && defaultsCache?.model_id) || "");
    }
  }

  async function initChatSelectors() {
    try {
      const [projs, bizs] = await Promise.all([
        apiFetch("/api/projects"),
        apiFetch("/api/v31/businesses"),
      ]);
      const pSel = $("chat-proj-sel");
      const prevProj = pSel?.value;
      if (pSel) {
        pSel.replaceChildren(...(projs.items || []).map(p => new Option(`${p.name} (${p.project_id.slice(0, 8)})`, p.project_id)));
        if (prevProj && (projs.items || []).some(p => p.project_id === prevProj)) pSel.value = prevProj;
      }
      const bSel = $("chat-biz-sel");
      if (bSel) {
        const cur = bSel.value;
        const activeBiz = (bizs.items || []).filter(b => !b.archived);
        bSel.replaceChildren(...activeBiz.map(b => new Option(b.name, b.id)));
        if (cur && activeBiz.some(b => b.id === cur)) bSel.value = cur;
      }
      await loadExecutionSources(!prevProj);
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
      if (myEpoch !== loadEpoch) return;
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

  function renderPendingHandoffs(conv) {
    const box = $("chat-handoff-box");
    if (!box) return;
    const pending = (conv.handoffs || []).filter(h => h.status === "awaiting_external");
    if (!pending.length) {
      box.style.display = "none";
      box.replaceChildren();
      return;
    }
    box.style.display = "block";
    box.replaceChildren();
    const h = pending[0];
    box.append(el("strong", `Đang chờ kết quả bàn giao IDE (${h.source_key} · Handoff ${h.id.slice(0, 8)})`));
    box.append(el("p", "Chạy gói prompt trên IDE thực tế rồi dán kết quả bên dưới để đồng bộ vào lịch sử & Execution Map (không giả lập).", "trace-muted"));
    const ta = document.createElement("textarea");
    ta.rows = 3;
    ta.placeholder = "Dán kết quả thực tế từ IDE...";
    const row = el("div", null, "row-actions");
    row.style.marginTop = "6px";
    const inpModel = document.createElement("input");
    inpModel.placeholder = "Model thực tế (tùy chọn)";
    inpModel.style.width = "200px";
    const inpSid = document.createElement("input");
    inpSid.placeholder = "Session/Plan ID IDE (tùy chọn)";
    inpSid.style.width = "200px";
    const btnImp = el("button", "Nhập kết quả thực tế", "primary");
    btnImp.type = "button";
    btnImp.style.marginTop = "0";
    btnImp.addEventListener("click", async () => {
      const content = ta.value.trim();
      if (!content) { toast("Dán nội dung kết quả trước khi nhập."); return; }
      try {
        await apiFetch(`/api/v321/chat/handoffs/${encodeURIComponent(h.id)}/import`, {
          method: "POST",
          body: JSON.stringify({
            project_id: $("chat-proj-sel").value,
            result_content: content,
            provider_reported_model: inpModel.value.trim() || null,
            external_session_id: inpSid.value.trim() || null,
          }),
        });
        toast("Đã nhập kết quả bàn giao IDE thực tế.");
        await openConversation(activeConvId);
      } catch (e) {
        toast("Lỗi nhập kết quả: " + e.message);
      }
    });
    row.append(inpModel, inpSid, btnImp);
    box.append(ta, row);
  }

  async function openConversation(convId) {
    const pid = $("chat-proj-sel")?.value;
    if (!pid || !convId) return;
    activeConvId = convId;
    const myEpoch = ++loadEpoch;
    try {
      const conv = await apiFetch(`/api/v321/chat/conversations/${encodeURIComponent(convId)}?project_id=${encodeURIComponent(pid)}`);
      if (myEpoch !== loadEpoch) return;
      $("chat-conv-title").textContent = `${conv.title} · [${conv.mode}]${conv.execution_source ? " · " + conv.execution_source : ""}`;
      renderPendingHandoffs(conv);
      const box = $("chat-messages");
      box.replaceChildren();
      const msgs = conv.messages || [];
      if (!msgs.length) {
        box.append(el("p", "Cuộc trò chuyện mới sẵn sàng. Nhập yêu cầu bên dưới và bấm Gửi tin nhắn.", "trace-muted"));
        return;
      }
      for (const m of msgs) {
        const wrap = el("div", null, "card");
        wrap.style.marginBottom = "10px";
        wrap.style.borderLeft = m.role === "user" ? "3px solid var(--accent)" : "3px solid var(--ok)";
        const head = el("div", null, "row-actions");
        head.style.justifyContent = "space-between";
        const left = el("div", null, "row-actions");
        const isSim = Boolean(m.usage?.is_simulation || m.sources?.is_simulation || m.provider_key === "mock");
        left.append(
          el("strong", m.role === "user" ? "Bạn" : `AI (${m.provider_key || m.mode}${m.model_id ? " / " + m.model_id : ""})`),
          el("span", m.delivery_status, `badge ${m.delivery_status === "delivered" ? "ok" : m.delivery_status === "pending" ? "warn" : "err"}`),
          ...(isSim ? [el("span", "Mô phỏng (không tính AI thật)", "badge warn")] : []),
          el("small", vnTime(m.created_at), "trace-muted")
        );
        const right = el("div", null, "row-actions");
        const bCopy = el("button", "Copy", "secondary");
        bCopy.type = "button";
        bCopy.addEventListener("click", () => { navigator.clipboard?.writeText(m.content || ""); toast("Đã sao chép nội dung."); });
        right.append(bCopy);
        if (m.trace_id) {
          const bTr = el("button", "Execution Map", "secondary");
          bTr.type = "button";
          bTr.addEventListener("click", () => showTab("traces", $("trace-nav")));
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
          if (s.reported_model && s.reported_model !== m.model_id) metaParts.push(`Model báo cáo=${s.reported_model}`);
          if (u.input_tokens != null || u.output_tokens != null) {
            metaParts.push(`Token in=${u.input_tokens ?? "?"} / out=${u.output_tokens ?? "?"} / think=${u.reasoning_tokens ?? "—"}`);
          }
          if (u.estimated_cost_usd != null && !isSim) metaParts.push(`Cost≈$${Number(u.estimated_cost_usd).toFixed(5)}`);
          if (s.quota_state) metaParts.push(`Quota=${QUOTA_LABEL[s.quota_state] || s.quota_state}`);
          if (s.memories != null) metaParts.push(`Memory=${s.memories}`);
          if ((s.skills || []).length) metaParts.push(`Skills=${s.skills.join(",")}`);
          if ((s.tools || []).length) metaParts.push(`MCP=${s.tools.join(",")}`);
          if (s.handoff_id) metaParts.push(`Handoff=${String(s.handoff_id).slice(0, 8)}`);
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
    const skey = $("chat-source-sel")?.value || "antigravity";
    const mid = $("chat-model-sel")?.value || "";
    const conv = await apiFetch("/api/v321/chat/conversations", {
      method: "POST",
      body: JSON.stringify({
        project_id: pid,
        business_id: bid,
        title: "Cuộc trò chuyện mới",
        mode: $("chat-mode-sel")?.value || "chat",
        execution_source: skey,
        model_id: mid || null,
      }),
    });
    activeConvId = conv.id;
    await loadConversations();
    await openConversation(conv.id);
    toast("Đã tạo cuộc trò chuyện mới.");
  }

  async function saveProjectDefaults() {
    const pid = $("chat-proj-sel")?.value;
    if (!pid) return;
    try {
      const res = await apiFetch(`/api/v321/projects/${encodeURIComponent(pid)}/chat-defaults`, {
        method: "PUT",
        body: JSON.stringify({
          business_id: $("chat-biz-sel")?.value || "",
          execution_source: $("chat-source-sel")?.value || "",
          model_id: $("chat-model-sel")?.value || "",
          mode: $("chat-mode-sel")?.value || "task",
          allow_paid_api_fallback: false,
        }),
      });
      defaultsCache = res;
      renderSourceMeta();
      toast("Đã lưu Agent & Model mặc định cho dự án (không bật fallback API trả phí).");
    } catch (e) {
      toast("Lỗi lưu mặc định: " + e.message);
    }
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
          execution_source: $("chat-source-sel")?.value || null,
          model_id: $("chat-model-sel")?.value || null,
          thinking_level: $("chat-think-sel")?.value || "none",
          max_budget_usd: Number($("chat-budget-inp")?.value) || 0.05,
          allow_tools: Boolean($("chat-allow-tools")?.checked),
          egress_consent_confirmed: Boolean($("chat-consent-chk")?.checked),
          manual_handoff: Boolean($("chat-handoff-chk")?.checked),
          idempotency_key: idem,
        }),
      });
      $("chat-input").value = "";
      st.textContent = `Đã nhận (${res.assistant_message?.delivery_status || "ok"})`;
      await openConversation(activeConvId);
      await loadConversations();
    } catch (e) {
      st.textContent = "Lỗi: " + e.message;
      toast("Không gửi được: " + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  window.addEventListener("hub-ready", () => {
    $("chat-proj-sel")?.addEventListener("change", async () => {
      activeConvId = null;
      await loadExecutionSources(true);
      await loadConversations();
    });
    $("chat-source-sel")?.addEventListener("change", () => {
      populateModelsForSource($("chat-source-sel").value);
    });
    $("chat-model-sel")?.addEventListener("change", renderSourceMeta);
    $("chat-save-default-btn")?.addEventListener("click", saveProjectDefaults);
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

  // ==================== P19A.2: AGENT CONTROL CENTER (ENABLE/DISABLE AGENT, MODEL & QUOTA) ====================
  let accSourcesCache = [];

  function renderAgentModelsTable() {
    const tblBox = $("agent-models-table");
    const filt = $("agent-model-source-filter")?.value || "";
    if (!tblBox) return;
    tblBox.replaceChildren();
    const rows = [];
    for (const s of accSourcesCache) {
      if (filt && s.source_key !== filt) continue;
      for (const m of (s.models || [])) rows.push({ src: s, m });
    }
    if (!rows.length) {
      tblBox.append(el("p", "Không có model nào trong bộ lọc. Bấm 'Đồng bộ model từ Host CLI' để quét model thực tế.", "trace-muted"));
      return;
    }
    const tbl = document.createElement("table");
    const thead = document.createElement("tr");
    ["Agent", "Model ID", "Trạng thái Model", "Quota", "Reset dự kiến", "Hợp lệ điều phối", "Thao tác"].forEach(h => thead.append(el("th", h)));
    tbl.append(thead);

    for (const { src, m } of rows) {
      const tr = document.createElement("tr");
      const qst = m.quota?.state || "unknown";
      tr.append(
        el("td", src.display_name),
        el("td", m.model_id),
        el("td", m.admin_enabled ? "Đang bật" : `Đã tắt${m.disabled_reason ? " (" + m.disabled_reason + ")" : ""}`),
        el("td", `${QUOTA_LABEL[qst] || qst}${m.quota?.source ? " (" + m.quota.source + ")" : ""}`),
        el("td", vnTime(m.quota?.reset_at)),
        el("td", m.eligible ? "Sẵn sàng" : (m.ineligible_reason || "Bị chặn"))
      );
      const tdAct = el("td", null, "row-actions");
      const bToggle = el("button", m.admin_enabled ? "Tắt model" : "Bật model", "secondary");
      bToggle.type = "button";
      bToggle.style.marginTop = "0";
      bToggle.addEventListener("click", async () => {
        try {
          await apiFetch("/api/v321/model-controls", {
            method: "PUT",
            body: JSON.stringify({
              source_key: src.source_key,
              model_id: m.model_id,
              enabled: !m.admin_enabled,
              disabled_reason: m.admin_enabled ? "Tắt thủ công tại Agent Control Center" : "",
            }),
          });
          toast(`Đã ${m.admin_enabled ? "tắt" : "bật"} model ${m.model_id}.`);
          await loadAgentControlCenter();
        } catch (e) { toast("Lỗi cập nhật model: " + e.message); }
      });
      tdAct.append(bToggle);

      if (src.group !== "simulation") {
        const bExh = el("button", qst === "exhausted" ? "Xác nhận phục hồi quota" : "Đánh dấu hết quota", "secondary");
        bExh.type = "button";
        bExh.style.marginTop = "0";
        bExh.addEventListener("click", async () => {
          const markExhausted = qst !== "exhausted";
          let resetIso = null;
          if (markExhausted) {
            const r = window.prompt(`Nhập thời điểm reset dự kiến (ISO hoặc để trống nếu chưa rõ) cho ${m.model_id}:`, "");
            if (r === null) return;
            if (r.trim()) {
              const parsed = Date.parse(r.trim());
              if (!Number.isFinite(parsed)) { toast("Định dạng thời gian không hợp lệ."); return; }
              resetIso = new Date(parsed).toISOString();
            }
          }
          try {
            await apiFetch("/api/v2/quotas", {
              method: "POST",
              body: JSON.stringify({
                provider: src.provider_key,
                auth_mode: m.auth_mode || "subscription_cli",
                account_bucket: "model:" + m.model_id,
                quota_kind: src.group === "metered_api" ? "api_budget" : "subscription_quota",
                unit: "availability_flag",
                numerator: markExhausted ? 1 : 0,
                denominator: 1,
                reset_at: resetIso,
                source: "manual_user_confirmation",
                confounded: false,
                observed_at: new Date().toISOString(),
              }),
            });
            toast(markExhausted ? `Đã đánh dấu hết quota: ${m.model_id}` : `Đã xác nhận phục hồi quota: ${m.model_id}`);
            await loadAgentControlCenter();
          } catch (e) { toast("Lỗi cập nhật quota: " + e.message); }
        });
        tdAct.append(bExh);
      }
      tr.append(tdAct);
      tbl.append(tr);
    }
    tblBox.append(tbl);
  }

  async function loadHostHealthCard() {
    const body = $("agent-host-health-body");
    if (!body) return;
    try {
      const st = await apiFetch("/api/status");
      body.replaceChildren();
      const row = el("div", null, "row-actions");
      row.style.flexWrap = "wrap";
      row.style.gap = "8px";
      const brOk = Boolean(st.bridge?.ready);
      const cbOk = Boolean(st.codebase?.ready);
      const hbOk = Boolean(st.host_brain?.ready);
      row.append(
        el("span", `Memory Bridge: ${brOk ? "Sẵn sàng (v" + (st.bridge?.schema_version || "?") + ")" : "Chưa sẵn sàng"}`, `badge ${brOk ? "ok" : "err"}`),
        el("span", `Codebase: ${cbOk ? "Sẵn sàng" : "Chưa sẵn sàng"}`, `badge ${cbOk ? "ok" : "err"}`),
        el("span", `Host Brain Agent: ${st.host_brain?.label || (hbOk ? "Đang hoạt động" : "Chưa kết nối")}`, `badge ${hbOk ? "ok" : "err"}`)
      );
      if (st.host_brain?.checked_at) {
        row.append(el("small", `Kiểm tra lúc: ${vnTime(st.host_brain.checked_at)}`, "trace-muted"));
      }
      body.append(row);
      body.append(el("p", st.host_brain?.detail || "", "trace-muted"));

      const actions = el("div", null, "row-actions");
      actions.style.flexWrap = "wrap";
      const cmdAll = st.host_brain?.recommended_command || "powershell -ExecutionPolicy Bypass -File scripts\\Start-AIMemoryHub.ps1";
      const cmdHost = st.host_brain?.fallback_command || "powershell -ExecutionPolicy Bypass -File scripts\\start-host-brain-agent.ps1";
      const bAll = el("button", "Copy lệnh khởi động hợp nhất (Start-AIMemoryHub.ps1)", "secondary");
      bAll.type = "button";
      bAll.style.marginTop = "0";
      bAll.addEventListener("click", () => { navigator.clipboard?.writeText(cmdAll); toast("Đã sao chép lệnh Start-AIMemoryHub.ps1"); });
      const bHost = el("button", "Copy lệnh bật Host Brain Agent", "secondary");
      bHost.type = "button";
      bHost.style.marginTop = "0";
      bHost.addEventListener("click", () => { navigator.clipboard?.writeText(cmdHost); toast("Đã sao chép lệnh start-host-brain-agent.ps1"); });
      actions.append(bAll, bHost);
      actions.append(el("small", "Dashboard chạy trong Docker nên không tự chạy lệnh shell trên Windows host (chạy lệnh trên tại PowerShell Windows).", "trace-muted"));
      body.append(actions);
    } catch (e) {
      body.textContent = "Lỗi kiểm tra trạng thái hệ thống: " + e.message;
    }
  }

  async function loadAgentControlCenter() {
    loadHostHealthCard();
    const tblBox = $("agent-controls-table");
    if (!tblBox) return;
    try {
      const pid = $("agents-proj")?.value || $("chat-proj-sel")?.value || "";
      const params = new URLSearchParams({ probe_host: "true" });
      if (pid) params.set("project_id", pid);
      const data = await apiFetch("/api/v321/execution-sources?" + params);
      accSourcesCache = data.sources || [];

      const tbl = document.createElement("table");
      const thead = document.createElement("tr");
      ["Agent / Nguồn thực thi", "Nhóm gói", "Trạng thái Admin", "Runtime / CLI", "Chế độ hỗ trợ", "Model khả dụng", "Thao tác"].forEach(h => thead.append(el("th", h)));
      tbl.append(thead);

      for (const s of accSourcesCache) {
        const tr = document.createElement("tr");
        const caps = [];
        if (s.capabilities?.chat) caps.push("Chat");
        if (s.capabilities?.task) caps.push("Task");
        if (s.capabilities?.plan_dag) caps.push("Plan&DAG");
        if (s.capabilities?.manual_handoff) caps.push("IDE Handoff");
        tr.append(
          el("td", s.display_name),
          el("td", s.billing_label),
          el("td", s.admin_enabled ? "Đang bật" : `Đã tắt${s.disabled_reason ? " (" + s.disabled_reason + ")" : ""}`),
          el("td", `${s.runtime_status}${s.cli_version ? " · " + s.cli_version : ""}`),
          el("td", caps.join(", ") || "—"),
          el("td", `${s.eligible_model_count} / ${(s.models || []).length}`)
        );
        const tdAct = el("td", null, "row-actions");
        const btn = el("button", s.admin_enabled ? "Tắt Agent" : "Bật Agent", s.admin_enabled ? "secondary" : "primary");
        btn.type = "button";
        btn.style.marginTop = "0";
        btn.addEventListener("click", async () => {
          try {
            await apiFetch(`/api/v321/agent-controls/${encodeURIComponent(s.source_key)}`, {
              method: "PUT",
              body: JSON.stringify({
                enabled: !s.admin_enabled,
                disabled_reason: s.admin_enabled ? "Tắt thủ công tại Agent Control Center" : "",
              }),
            });
            toast(`Đã ${s.admin_enabled ? "tắt" : "bật"} Agent ${s.display_name}.`);
            await loadAgentControlCenter();
          } catch (e) { toast("Lỗi cập nhật Agent: " + e.message); }
        });
        tdAct.append(btn);
        tr.append(tdAct);
        tbl.append(tr);
      }
      tblBox.replaceChildren(tbl);

      const fSel = $("agent-model-source-filter");
      if (fSel) {
        const cur = fSel.value;
        fSel.replaceChildren(new Option("Tất cả Agent / Nguồn thực thi", ""), ...accSourcesCache.map(s => new Option(s.display_name, s.source_key)));
        if (cur && accSourcesCache.some(s => s.source_key === cur)) fSel.value = cur;
      }
      renderAgentModelsTable();
    } catch (e) {
      tblBox.textContent = "Lỗi tải Agent Control Center: " + e.message;
    }
  }

  window.addEventListener("hub-ready", () => {
    $("agent-controls-refresh")?.addEventListener("click", loadAgentControlCenter);
    $("agent-host-health-refresh")?.addEventListener("click", loadHostHealthCard);
    $("agent-model-source-filter")?.addEventListener("change", renderAgentModelsTable);
  });

  window.addEventListener("hub-tab", ev => {
    if (ev.detail === "chat") initChatSelectors();
    if (ev.detail === "agents") loadAgentControlCenter();
  });
})();
