/* V2.2.4 Unified Execution Map Workspace: deterministic layout of observed & planned records. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const unknown = v => (v == null || v === "" ? "Unknown" : String(v));
  const timeMs = v => {
    if (!v || !Number.isFinite(Date.parse(v))) return "Unknown";
    const d = new Date(v);
    const base = d.toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" });
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    return `${base}.${ms}`;
  };
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const colors = {
    user_task: "#64748b",
    planner: "#7c3aed",
    agent_step: "#b45309",
    mcp_tool_call: "#2563eb",
    ai_memory: "#0f766e",
    llm_call: "#9333ea",
    code_search: "#0369a1",
    qa_test: "#15803d",
    error: "#b91c1c",
    handoff: "#0e7490",
    save_memory: "#0f766e",
    local_rule: "#475569",
    session_usage: "#6d28d9",
  };
  const TOKEN_STATUS_LABEL = {
    measured: "Measured",
    estimated: "Estimated",
    pending: "Pending",
    unsupported: "Unsupported",
    collection_error: "Collection Error",
    not_applicable: "Not applicable (0 LLM)",
    unknown: "Unknown",
  };

  let rows = [], plannedSteps = [], groups = [], summary = {}, metrics = {};
  let offset = 0, next = null, total = 0, epoch = 0, busy = false, lastFingerprint = "";
  let selectedId = null, selectedRow = null, detailTab = "overview";
  let zoom = 1.0, compactMode = false, hideEmptyLanes = true, detailOpen = true;
  let collapsedGroups = new Set(), hiddenCols = new Set(["cache_read_tokens", "reasoning_tokens", "provider"]);
  let timeZoomScale = 1.0;

  let selectedExternalPlanId = "";
  let scopePlansMeta = {};

  function filters() {
    const q = {
      project_id: $("trace-proj").value,
      worktree_id: $("trace-wt").value,
      plan_id: selectedExternalPlanId ? "" : $("trace-plan").value.trim(),
      external_plan_id: selectedExternalPlanId,
      task_id: $("trace-task").value.trim(),
      attempt_id: ($("trace-attempt")?.value || "").trim(),
      session_id: $("trace-session").value.trim(),
      actor_id: $("trace-agent").value.trim(),
      model_id: $("trace-model").value.trim(),
      status: $("trace-status").value,
      search: $("trace-search").value.trim(),
      sort: $("trace-sort").value,
    };
    if ($("trace-descendants")?.checked && q.task_id) q.include_descendants = "true";
    for (const [key, id] of [["from_time", "trace-from"], ["to_time", "trace-to"]]) {
      const v = $(id).value;
      if (v) q[key] = new Date(v + "+07:00").toISOString();
    }
    return Object.fromEntries(Object.entries(q).filter(([, v]) => v));
  }

  let scopeEpoch = 0;
  function updateBreadcrumb() {
    const bc = $("trace-breadcrumb");
    if (!bc) return;
    const parts = ["Dự án"];
    if (selectedExternalPlanId) {
      const meta = scopePlansMeta[selectedExternalPlanId];
      parts.push(`External Plan [${meta?.source || "external"} · Monitor-only] ${selectedExternalPlanId.slice(0, 8)}`);
    } else if ($("trace-plan").value.trim()) {
      parts.push(`Plan ${$("trace-plan").value.trim().slice(0, 8)}`);
    }
    if ($("trace-task").value.trim()) parts.push(`Task ${$("trace-task").value.trim().slice(0, 8)}`);
    if ($("trace-attempt")?.value.trim()) parts.push(`Attempt ${$("trace-attempt").value.trim().slice(0, 8)}`);
    bc.textContent = "Phạm vi: " + parts.join(" → ");
  }

  async function syncScopeOptions() {
    const pid = $("trace-proj")?.value;
    const planSel = $("trace-plan-sel");
    const taskSel = $("trace-task-sel");
    const attSel = $("trace-attempt-sel");
    if (!pid || !planSel || !taskSel || !attSel) return;
    const myEpoch = ++scopeEpoch;
    const params = new URLSearchParams({ project_id: pid });
    const src = $("trace-source-sel")?.value || "all";
    params.set("source", src);
    const curPlan = selectedExternalPlanId || $("trace-plan").value.trim();
    const curTask = $("trace-task").value.trim();
    const curAtt = ($("trace-attempt")?.value || "").trim();
    if (!selectedExternalPlanId && $("trace-plan").value.trim()) params.set("plan_id", $("trace-plan").value.trim());
    if (curTask) params.set("task_id", curTask);
    try {
      const data = await apiFetch("/api/v2/traces/scope-options?" + params);
      if (myEpoch !== scopeEpoch) return; // Discard stale response on rapid switching
      scopePlansMeta = {};
      for (const p of data.plans || []) scopePlansMeta[p.id] = p;
      planSel.replaceChildren(
        new Option("Tất cả Plan", ""),
        ...(data.plans || []).map(p => {
          const tag = p.is_monitor_only ? `[${p.source} · Monitor-only · rev ${p.revision_no || 1}]` : `[Hub · ${p.status}]`;
          return new Option(`${tag} ${(p.title || p.objective || p.id).slice(0, 52)}`, p.id);
        })
      );
      if (curPlan && scopePlansMeta[curPlan]) planSel.value = curPlan;
      taskSel.replaceChildren(
        new Option("Tất cả Task", ""),
        ...(data.tasks || []).map(t => new Option(`[${t.state}] ${t.task_key}: ${(t.title || t.id).slice(0, 40)}`, t.id))
      );
      if (curTask) taskSel.value = curTask;
      attSel.replaceChildren(
        new Option("Tất cả Attempt", ""),
        ...(data.attempts || []).map(a => new Option(`#${a.attempt_number} [${a.status}] (${a.id.slice(0, 8)})`, a.id))
      );
      if (curAtt) attSel.value = curAtt;
      updateBreadcrumb();
    } catch (_) {
      // Non-fatal if scope options cannot be loaded
    }
  }

  function sync() {
    const old = $("trace-wt").value;
    const wts = (window.worktreesByProject || {})[$("trace-proj").value] || [];
    $("trace-wt").replaceChildren(new Option("Tất cả worktree", ""), ...wts.map(w => new Option(w.branch || w.worktree_id, w.worktree_id)));
    if (wts.some(w => w.worktree_id === old)) $("trace-wt").value = old;
  }

  function formatDuration(r) {
    if (r.duration_ms != null) return `${r.duration_ms} ms`;
    if (r.status === "running" && r.started_at && Number.isFinite(Date.parse(r.started_at))) {
      const elapsed = Math.max(0, Date.now() - Date.parse(r.started_at));
      return `Running (${Math.round(elapsed / 1000)}s)`;
    }
    return "Unknown";
  }

  function tokenBrief(r) {
    const ts = r.token_status || "unknown";
    if (ts === "not_applicable") return "Token: N/A (0 LLM)";
    if (ts === "measured") {
      return `Token: in=${unknown(r.input_tokens)} / out=${unknown(r.output_tokens)}`;
    }
    return `Token: ${TOKEN_STATUS_LABEL[ts] || ts}`;
  }

  function seqLabel(r) {
    const s = r.execution_sequence != null ? `#${String(r.execution_sequence).padStart(2, "0")}` : "#?";
    return r.timestamp_tie ? `${s}≈` : s;
  }

  function relatedIds(id) {
    const out = new Set();
    if (!id) return out;
    const row = rows.find(x => x.trace_id === id);
    if (!row) return out;
    if (row.parent_trace_id) out.add(row.parent_trace_id);
    if (row.relationships?.retry_of) out.add(row.relationships.retry_of);
    for (const d of row.relationships?.dependency_trace_ids || []) out.add(d);
    for (const x of rows) {
      if (x.parent_trace_id === id || x.relationships?.retry_of === id || (x.relationships?.dependency_trace_ids || []).includes(id)) {
        out.add(x.trace_id);
      }
    }
    return out;
  }

  function nodeCard(r) {
    const rel = relatedIds(selectedId);
    const cls = ["trace-node"];
    if (r.trace_id === selectedId) cls.push("is-selected");
    else if (rel.has(r.trace_id)) cls.push("is-related");
    if (r.status === "running") cls.push("is-running");
    if (r.status === "failed" || r.status === "blocked") cls.push("is-error");

    const b = el("button", null, cls.join(" "));
    b.type = "button";
    b.dataset.traceId = r.trace_id;
    b.style.setProperty("--trace-color", colors[r.step_type] || colors.local_rule);

    const head = el("div", null, "trace-node-head");
    head.append(el("span", seqLabel(r), "trace-seq"), el("small", `${r.status}${r.trace_id === metrics.slowest_trace_id ? " · chậm nhất" : ""}${r.relationships?.retry_of ? " · retry" : ""}`));
    b.append(head);
    b.append(el("strong", r.step_name));
    b.append(el("small", `${r.step_type} · ${unknown(r.actor_id)} · ${formatDuration(r)}`));
    b.append(el("small", `${r.tool_name || r.model_id || "—"} · ${tokenBrief(r)}`));

    const ss = r.step_summary || {};
    if (ss.purpose || ss.result) {
      const sumBox = el("div", null, "trace-node-summary");
      if (ss.purpose) sumBox.append(el("div", `Mục đích: ${ss.purpose}`));
      if (ss.result) sumBox.append(el("div", `Kết quả: ${ss.result}`));
      b.append(sumBox);
    }

    b.addEventListener("click", async ev => {
      ev.stopPropagation();
      selectedId = r.trace_id;
      selectedRow = r;
      if (!detailOpen) setDetailOpen(true);
      renderViewOnly();
      renderDetail(r);
      try {
        const fresh = await apiFetch("/api/v2/traces/" + encodeURIComponent(r.trace_id) + "?" + new URLSearchParams({ project_id: r.project_id }));
        selectedRow = { ...r, ...fresh };
        if (selectedId === r.trace_id) renderDetail(selectedRow);
      } catch (e) {
        // Keep enriched local row visible if detail fetch fails
      }
    });
    return b;
  }

  function plannedCard(p) {
    const b = el("div", null, "trace-node is-planned");
    b.style.setProperty("--trace-color", "#475569");
    const head = el("div", null, "trace-node-head");
    head.append(el("span", `Planned #${p.planned_order}`, "trace-seq"), el("small", "chưa chạy"));
    b.append(head, el("strong", `${p.task_key}: ${p.title}`), el("small", `${unknown(p.assigned_provider)} / ${unknown(p.assigned_model_id)}`));
    return b;
  }

  function actionBtn(label, fn) {
    const b = el("button", label, "secondary");
    b.type = "button";
    b.addEventListener("click", async () => {
      try {
        await fn();
      } catch (e) {
        const st = $("trace-link-status");
        if (st) st.textContent = e.message;
      }
    });
    return b;
  }

  function fieldsDl(pairs) {
    const dl = el("dl", null, "trace-fields");
    for (const [k, v] of pairs) dl.append(el("dt", k), el("dd", unknown(v)));
    return dl;
  }

  function renderDetail(r) {
    const panel = $("trace-detail");
    if (!r) {
      panel.textContent = "Chọn step để xem bằng chứng.";
      return;
    }
    panel.replaceChildren();
    const header = el("div", null, "trace-page-head");
    header.append(el("h3", `${seqLabel(r)} ${r.step_name}`), actionBtn("Copy ID", () => navigator.clipboard?.writeText(r.trace_id)));
    panel.append(header);

    const tabs = el("div", null, "trace-detail-tabs");
    const tabDefs = [
      ["overview", "Overview"],
      ["io", "Input/Output"],
      ["rel", "Relationships"],
      ["tokens", "Token & Cost"],
      ["evidence", "Evidence"],
      ["error", "Error/Retry"],
    ];
    for (const [key, label] of tabDefs) {
      const tb = el("button", label, `secondary${detailTab === key ? " active" : ""}`);
      tb.type = "button";
      tb.addEventListener("click", () => {
        detailTab = key;
        renderDetail(r);
      });
      tabs.append(tb);
    }
    panel.append(tabs);

    const body = el("div");
    const mp = r.model_provenance || {};
    const rel = r.relationships || {};
    if (detailTab === "overview") {
      body.append(fieldsDl([
        ["Sequence", `${seqLabel(r)} (${r.order_basis || "observed_started_at"})`],
        ["Trạng thái", r.status],
        ["Loại / Lane", `${r.step_type} / ${r.lane || "system"}`],
        ["Actor", `${unknown(r.actor_type)} · ${unknown(r.actor_id)}`],
        ["Tool", r.tool_name],
        ["Nguồn dữ liệu", r.data_source_kind],
        ["Bắt đầu (VN)", timeMs(r.started_at)],
        ["Kết thúc (VN)", timeMs(r.ended_at)],
        ["Thời lượng", formatDuration(r)],
        ["MCP / Memory / LLM", `${unknown(r.used_mcp)} / ${unknown(r.used_memory)} / ${unknown(r.used_llm)}`],
        ["Late arrival", r.late_arrival ? "Có (đến sau thứ tự thời gian)" : "Không"],
      ]));
    } else if (detailTab === "io") {
      body.append(fieldsDl([
        ["Mục đích", r.step_summary?.purpose || r.metadata_json?.why],
        ["Input summary", r.input_summary],
        ["Output summary", r.output_summary],
        ["Kết quả rút gọn", r.step_summary?.result],
      ]));
    } else if (detailTab === "rel") {
      body.append(fieldsDl([
        ["Trace ID", r.trace_id],
        ["Parent Trace", r.parent_trace_id],
        ["Root Trace", r.root_trace_id],
        ["Retry of", rel.retry_of],
        ["Dependency Tasks", (rel.dependency_task_ids || []).join(", ") || "Không có"],
        ["Plan ID", r.plan_id],
        ["Task ID", r.task_id],
        ["Session ID", r.session_id],
        ["Attempt ID", r.attempt_id],
      ]));
      const links = el("div", null, "trace-links");
      if (r.parent_trace_id) links.append(actionBtn("Mở Parent Trace", async () => {
        const d = await apiFetch("/api/v2/traces/" + encodeURIComponent(r.parent_trace_id) + "?" + new URLSearchParams({ project_id: r.project_id }));
        selectedId = d.trace_id;
        selectedRow = d;
        renderDetail(d);
      }));
      if (r.plan_id) links.append(actionBtn("Mở Kế hoạch", () => {
        $("plans-proj").value = r.project_id;
        syncWorktrees("plans-proj", "plans-wt");
        showTab("plans", document.querySelector("nav button:nth-child(2)"));
        return window.openTracePlan(r.plan_id);
      }));
      if (r.session_id) links.append(actionBtn("Chi tiết Session", async () => {
        const d = await apiFetch("/api/v2/session-detail?" + new URLSearchParams({ project_id: r.project_id, session_id: r.session_id }));
        $("trace-link-status").replaceChildren(el("pre", JSON.stringify(d, null, 2)));
      }));
      if (r.step_type === "code_search" || r.metadata_json?.code_path) links.append(actionBtn("Codebase Explorer", () => {
        $("code-proj").value = r.project_id;
        syncWorktrees("code-proj", "code-wt");
        $("code-wt").value = r.worktree_id;
        showTab("code", Array.from(document.querySelectorAll("nav button")).find(b => b.textContent.trim().includes("Codebase")));
      }));
      body.append(links, el("div", null, "trace-muted"));
      body.lastChild.id = "trace-link-status";
    } else if (detailTab === "tokens") {
      body.append(fieldsDl([
        ["Trạng thái Token", TOKEN_STATUS_LABEL[r.token_status] || unknown(r.token_status)],
        ["Provider", r.provider],
        ["Requested Model", mp.requested_model],
        ["Client-Declared Model", mp.client_declared_model],
        ["Provider-Reported Model", mp.provider_reported_model],
        ["Input tokens", r.input_tokens],
        ["Output tokens", r.output_tokens],
        ["Cache read tokens", r.cache_read_tokens],
        ["Cache write tokens", r.cache_write_tokens],
        ["Reasoning counter", r.reasoning_tokens],
        ["Cost USD (đo được)", r.cost_usd],
        ["Measurement kind", r.metadata_json?.measurement_kind],
        ["Coverage", r.metadata_json?.coverage],
      ]));
    } else if (detailTab === "evidence") {
      body.append(fieldsDl([
        ["Source", r.metadata_json?.source],
        ["Evidence ref", r.metadata_json?.evidence_ref],
        ["Memory ID", r.metadata_json?.memory_id],
        ["Handoff ID", r.metadata_json?.handoff_id],
        ["QA evidence", r.metadata_json?.test_evidence_ref],
        ["Code path", r.metadata_json?.code_path],
      ]));
      const d = el("details");
      d.open = true;
      d.append(el("summary", "Evidence JSON (đã lọc)"), el("pre", JSON.stringify(r, null, 2)));
      body.append(d);
    } else {
      body.append(fieldsDl([
        ["Trạng thái", r.status],
        ["Error code", r.error_code],
        ["Error summary", r.error_summary],
        ["Retry of", rel.retry_of],
      ]));
    }
    panel.append(body);
  }

  function renderSummary() {
    const box = $("trace-summary");
    box.replaceChildren();
    const badge = $("trace-live-badge");
    const st = summary.overall_status || "empty";
    badge.textContent = st === "running" ? "Đang chạy (Live)" : st;
    badge.className = `badge ${st === "running" ? "ok" : st === "failed" || st === "blocked" ? "err" : "muted"}`;

    if (!summary.project_id) {
      box.append(el("p", "Chọn dự án để xem tổng quan."));
      return;
    }
    const sc = summary.status_counts || {};
    const tok = summary.tokens || {};
    const items = [
      ["Dự án / Worktree", `${summary.project_name || summary.project_id}${summary.worktree_branch ? " · " + summary.worktree_branch : ""}`],
      ["Mục tiêu", summary.objective || "Không có dữ liệu"],
      ["Agent hoạt động", (summary.active_agents || []).join(", ") || "Không có"],
      ["Model quan sát", (summary.models || []).join(", ") || "Unknown"],
      ["Đang chạy / Gần nhất", summary.current_step || summary.latest_completed_step || "—"],
      ["Step quan sát / Dự kiến", `${summary.observed_steps ?? 0} / ${summary.planned_steps_count ?? "Không rõ"}${summary.progress_pct != null ? ` (${summary.progress_pct}%)` : ""}`],
      ["Thành công / Chạy / Lỗi / Chặn", `${sc.succeeded ?? 0} / ${sc.running ?? 0} / ${sc.failed ?? 0} / ${sc.blocked ?? 0}`],
      ["Wall-clock / Union / Sum", `${unknown(summary.wall_span_ms)} / ${unknown(summary.observed_union_ms)} / ${unknown(summary.summed_step_ms)} ms`],
      ["Memory / MCP / LLM", `${summary.memory_ops ?? 0} / ${summary.mcp_ops ?? 0} / ${summary.llm_calls ?? 0}`],
      ["Token đo được (in / out)", `${unknown(tok.input_tokens)} / ${unknown(tok.output_tokens)}`],
      ["Kết quả gần nhất", summary.latest_result || "—"],
      ["Cập nhật lúc (VN)", timeMs(summary.updated_at)],
    ];
    for (const [k, v] of items) {
      const card = el("div", null, "trace-summary-item");
      card.append(el("small", k), el("strong", String(v)));
      box.append(card);
    }
  }

  function renderGroups() {
    const box = $("trace-groups");
    box.replaceChildren();
    const visible = hideEmptyLanes ? groups.filter(g => g.steps > 0) : groups;
    if (!visible.length) {
      box.append(el("p", "Chưa có nhóm hoạt động nào.", "trace-muted"));
      return;
    }
    for (const g of visible) {
      const card = el("div", null, `trace-group-card${g.failed || g.blocked ? " has-error" : ""}${g.running ? " is-running" : ""}`);
      card.append(
        el("strong", `${g.label} (${g.steps} step)`),
        el("div", `OK: ${g.succeeded} · Running: ${g.running} · Lỗi/Chặn: ${g.failed + g.blocked} · Retry: ${g.retries}`),
        el("div", `Latency TB/Max: ${unknown(g.avg_latency_ms)} / ${unknown(g.max_latency_ms)} ms`),
        el("small", g.latest_result ? `Gần nhất: ${g.latest_result}` : "Chưa có kết quả", "trace-muted")
      );
      box.append(card);
    }
  }

  function renderMetrics() {
    const target = $("trace-metrics");
    target.replaceChildren();
    if (metrics.steps == null) return;
    const values = [
      ["Step", metrics.steps],
      ["MCP (step)", metrics.used_mcp],
      ["Memory (step)", metrics.used_memory],
      ["LLM call", metrics.llm_calls],
      ["Retry", metrics.retries],
      ["Lỗi / chặn", metrics.errors],
      ["Input token", metrics.input_tokens],
      ["Output token", metrics.output_tokens],
      ["Cache read", metrics.cache_read_tokens],
      ["Reasoning", metrics.reasoning_tokens],
      ["USD đo được", metrics.cost_usd],
      ["Wall span (ms)", metrics.wall_span_ms],
      ["Interval union (ms)", metrics.observed_union_ms],
      ["Sum step (ms)", metrics.summed_step_ms],
    ];
    for (const [k, v] of values) {
      const b = el("div", null, "card");
      b.append(el("small", k), el("strong", unknown(v)));
      target.append(b);
    }
    target.append(el("p", "Chỉ số theo bộ lọc" + (metrics.partial ? " (giới hạn 5.000 step)" : "") + ". Không cộng trùng session_usage với llm_call đã liên kết; không dùng tổng step song song làm wall-clock.", "trace-metric-note"));
  }

  function renderGraph(target) {
    const legend = el("div", null, "trace-legend");
    legend.append(
      el("span", "━━ Parent-Child (có bằng chứng)"),
      el("span", "┄┄ Retry"),
      el("span", "□ Viền đứt: Planned (chưa chạy)"),
      el("span", "≈ Trùng timestamp"),
      el("span", "Viền vàng: Liên quan node đang chọn")
    );
    target.append(legend);

    const by = new Map(rows.map(r => [r.trace_id, r]));
    const children = new Map();
    for (const r of rows) {
      if (r.parent_trace_id && by.has(r.parent_trace_id)) {
        if (!children.has(r.parent_trace_id)) children.set(r.parent_trace_id, []);
        children.get(r.parent_trace_id).push(r);
      }
    }
    const visited = new Set();
    function tree(r, depth = 0) {
      const wrap = el("div", null, "trace-tree");
      if (visited.has(r.trace_id) || depth > 80) return wrap;
      visited.add(r.trace_id);
      if (r.parent_trace_id && !by.has(r.parent_trace_id)) {
        const pBtn = actionBtn("↑ Mở Parent ngoài bộ lọc", async () => {
          const d = await apiFetch("/api/v2/traces/" + encodeURIComponent(r.parent_trace_id) + "?" + new URLSearchParams({ project_id: r.project_id }));
          selectedId = d.trace_id;
          selectedRow = d;
          renderDetail(d);
        });
        wrap.append(pBtn);
      }
      wrap.append(nodeCard(r));
      const kids = children.get(r.trace_id) || [];
      if (kids.length) {
        const key = `tree:${r.trace_id}`;
        const isCol = collapsedGroups.has(key);
        const toggle = el("button", isCol ? `+ Mở ${kids.length} bước con` : `− Thu gọn ${kids.length} bước con`, "secondary");
        toggle.type = "button";
        toggle.style.margin = "4px 0 4px 12px";
        toggle.addEventListener("click", () => {
          if (collapsedGroups.has(key)) collapsedGroups.delete(key);
          else collapsedGroups.add(key);
          renderViewOnly();
        });
        wrap.append(toggle);
        if (!isCol) {
          const branch = el("div", null, `trace-children${kids.some(k => k.relationships?.retry_of) ? " is-retry" : ""}`);
          for (const ch of kids) branch.append(tree(ch, depth + 1));
          wrap.append(branch);
        }
      }
      return wrap;
    }
    for (const r of rows.filter(x => !x.parent_trace_id || !by.has(x.parent_trace_id))) target.append(tree(r));
    for (const r of rows) if (!visited.has(r.trace_id)) target.append(tree(r));
    if (plannedSteps.length) {
      const pWrap = el("div", null, "trace-tree");
      pWrap.append(el("h4", "Các bước dự kiến từ DAG (Planned — chưa quan sát)"));
      for (const p of plannedSteps) pWrap.append(plannedCard(p));
      target.append(pWrap);
    }
  }

  function renderSwimlane(target) {
    const activeLanes = (hideEmptyLanes ? groups.filter(g => g.steps > 0) : groups);
    if (!activeLanes.length) {
      target.append(el("p", "Không có lane nào có dữ liệu."));
      return;
    }
    const colsTemplate = `70px repeat(${activeLanes.length}, minmax(260px, 1fr))`;
    const wrap = el("div", null, "trace-swim-wrap");

    const head = el("div", null, "trace-swim-head");
    head.style.gridTemplateColumns = colsTemplate;
    head.append(el("div", "Seq", "trace-lane-head"));
    for (const g of activeLanes) {
      const lh = el("div", null, "trace-lane-head");
      const key = `lane:${g.lane}`;
      const colBtn = el("button", collapsedGroups.has(key) ? "+" : "−", "secondary");
      colBtn.type = "button";
      colBtn.addEventListener("click", () => {
        if (collapsedGroups.has(key)) collapsedGroups.delete(key);
        else collapsedGroups.add(key);
        renderViewOnly();
      });
      const top = el("div", null, "trace-page-head");
      top.append(el("strong", `${g.label} (${g.steps})`), colBtn);
      lh.append(top, el("small", `OK ${g.succeeded} · Err ${g.failed + g.blocked} · Run ${g.running}`));
      head.append(lh);
    }
    wrap.append(head);

    // Shared execution sequence axis: group concurrent/same-timestamp steps on the same row.
    const ordered = [...rows].sort((a, b) => (a.execution_sequence || 0) - (b.execution_sequence || 0));
    const body = el("div", null, "trace-swim-body");
    for (const r of ordered) {
      const rowEl = el("div", null, "trace-swim-row");
      rowEl.style.gridTemplateColumns = colsTemplate;
      rowEl.append(el("div", seqLabel(r), "trace-seq"));
      for (const g of activeLanes) {
        const cell = el("div", null, "trace-swim-cell");
        if ((r.lane || "system") === g.lane && !collapsedGroups.has(`lane:${g.lane}`)) {
          cell.append(nodeCard(r));
        }
        rowEl.append(cell);
      }
      body.append(rowEl);
    }
    wrap.append(body);
    target.append(wrap);
  }

  function renderTimeline(target) {
    const valid = rows.filter(r => Number.isFinite(Date.parse(r.started_at)));
    const lo = valid.length ? Math.min(...valid.map(r => Date.parse(r.started_at))) : 0;
    const hi = valid.length
      ? Math.max(...valid.map(r => (r.ended_at && Number.isFinite(Date.parse(r.ended_at)) ? Date.parse(r.ended_at) : Date.now())))
      : 1;
    const span = Math.max(1, hi - lo);

    const head = el("div", null, "trace-time-head");
    head.append(el("div", "Seq · Step (Sticky)"), el("div", `Trục thời gian chung · Wall-clock: ${unknown(summary.wall_span_ms)} ms · Union: ${unknown(summary.observed_union_ms)} ms`), el("div", "Bắt đầu / Duration"));
    target.append(head);

    const ordered = [...rows].sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
    for (const r of ordered) {
      const line = el("div", null, "trace-time-row");
      const labelWrap = el("div", null, "trace-time-label");
      labelWrap.append(nodeCard(r));

      const track = el("div", null, "trace-time-track");
      track.style.minWidth = `${Math.round(420 * timeZoomScale)}px`;
      if (Number.isFinite(Date.parse(r.started_at))) {
        const sMs = Date.parse(r.started_at);
        const eMs = r.ended_at && Number.isFinite(Date.parse(r.ended_at)) ? Date.parse(r.ended_at) : (r.status === "running" ? Date.now() : sMs);
        const bar = el("div", null, `trace-time-bar${r.status === "running" ? " is-running" : ""}`);
        bar.style.left = `${((sMs - lo) / span) * 100}%`;
        bar.style.width = `${Math.max(0.6, ((Math.max(sMs, eMs) - sMs) / span) * 100)}%`;
        bar.style.background = colors[r.step_type] || colors.local_rule;
        bar.title = `${seqLabel(r)} ${r.step_name} (${formatDuration(r)})`;
        track.append(bar);
      } else {
        track.append(el("small", "Unknown"));
      }
      const meta = el("div");
      meta.append(el("div", timeMs(r.started_at), "trace-muted"), el("strong", formatDuration(r)));
      line.append(labelWrap, track, meta);
      target.append(line);
    }
  }

  function renderTable(target) {
    const allCols = [
      ["execution_sequence", "Seq", r => seqLabel(r), "sticky-col-1"],
      ["step_name", "Step Name", r => r.step_name, "sticky-col-2"],
      ["step_type", "Type", r => r.step_type],
      ["lane", "Lane", r => r.lane],
      ["actor_id", "Actor", r => unknown(r.actor_id)],
      ["provider", "Provider", r => unknown(r.provider)],
      ["requested_model", "Requested Model", r => unknown(r.model_provenance?.requested_model)],
      ["client_declared_model", "Client Model", r => unknown(r.model_provenance?.client_declared_model)],
      ["provider_reported_model", "Reported Model", r => unknown(r.model_provenance?.provider_reported_model)],
      ["tool_name", "Tool", r => unknown(r.tool_name)],
      ["purpose", "Mục đích", r => unknown(r.step_summary?.purpose)],
      ["result", "Kết quả", r => unknown(r.step_summary?.result)],
      ["started_at", "Bắt đầu (VN)", r => timeMs(r.started_at)],
      ["ended_at", "Kết thúc (VN)", r => timeMs(r.ended_at)],
      ["duration_ms", "Duration", r => formatDuration(r)],
      ["input_tokens", "Input Tok", r => unknown(r.input_tokens)],
      ["output_tokens", "Output Tok", r => unknown(r.output_tokens)],
      ["cache_read_tokens", "Cache Tok", r => unknown(r.cache_read_tokens)],
      ["reasoning_tokens", "Reasoning", r => unknown(r.reasoning_tokens)],
      ["cost_usd", "Cost USD", r => unknown(r.cost_usd)],
      ["status", "Status", r => r.status],
      ["token_status", "Coverage / Token", r => TOKEN_STATUS_LABEL[r.token_status] || unknown(r.token_status)],
    ];

    const tools = el("div", null, "trace-table-tools");
    tools.append(el("strong", "Cột hiển thị:"));
    for (const [key, label] of allCols.slice(2)) {
      const lb = el("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !hiddenCols.has(key);
      cb.addEventListener("change", () => {
        if (cb.checked) hiddenCols.delete(key);
        else hiddenCols.add(key);
        renderViewOnly();
      });
      lb.append(cb, " " + label);
      tools.append(lb);
    }
    target.append(tools);

    const visibleCols = allCols.filter(([k]) => !hiddenCols.has(k));
    const t = el("table", null, "trace-table");
    const hr = el("tr");
    for (const [, label, , cls] of visibleCols) hr.append(el("th", label, cls || ""));
    t.append(hr);

    for (const r of rows) {
      const tr = el("tr", null, r.trace_id === selectedId ? "is-selected" : "");
      for (const [, , fn, cls] of visibleCols) {
        const val = String(fn(r));
        const td = el("td", val, cls || "");
        td.title = val;
        tr.append(td);
      }
      tr.addEventListener("click", () => {
        selectedId = r.trace_id;
        selectedRow = r;
        if (!detailOpen) setDetailOpen(true);
        renderViewOnly();
        renderDetail(r);
      });
      t.append(tr);
    }
    target.append(t);
  }

  function applyZoom() {
    const stage = $("trace-view");
    stage.style.transform = zoom === 1 ? "" : `scale(${zoom})`;
    $("trace-zoom-label").textContent = `${Math.round(zoom * 100)}%`;
  }

  function setDetailOpen(open) {
    detailOpen = open;
    $("trace-layout").classList.toggle("detail-closed", !open);
  }

  function renderViewOnly() {
    const canvas = $("trace-canvas-wrap");
    const prevLeft = canvas.scrollLeft;
    const prevTop = canvas.scrollTop;

    const target = $("trace-view");
    target.replaceChildren();
    const mode = $("trace-mode").value;
    document.querySelectorAll(".trace-mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));

    if (!rows.length && !plannedSteps.length) {
      target.append(el("p", "Chưa có trace phù hợp. Hệ thống không tạo dữ liệu minh họa."));
      return;
    }
    if (mode === "swim") renderSwimlane(target);
    else if (mode === "timeline") renderTimeline(target);
    else if (mode === "table") renderTable(target);
    else renderGraph(target);

    applyZoom();
    canvas.scrollLeft = prevLeft;
    canvas.scrollTop = prevTop;
  }

  function render() {
    renderSummary();
    renderGroups();
    renderMetrics();
    $("trace-count").textContent = `${rows.length} / ${total} step (UTC+7)`;
    $("trace-prev").disabled = offset === 0;
    $("trace-next").disabled = next == null;
    renderViewOnly();
  }

  async function loadInspector() {
    const pid = $("trace-proj").value;
    if (!pid) return;
    const body = $("trace-inspector-body");
    body.textContent = "Đang tải Token Telemetry Inspector...";
    try {
      const qs = new URLSearchParams({ project_id: pid });
      if ($("trace-wt").value) qs.set("worktree_id", $("trace-wt").value);
      const data = await apiFetch("/api/v2/traces/telemetry/inspector?" + qs);
      body.replaceChildren();
      body.append(el("h4", "Khả năng thu thập theo Client"));
      const ct = el("table", null, "trace-table");
      ct.append(el("tr"));
      for (const h of ["Client", "Provider", "Counter Mode", "Trường hỗ trợ", "Trường chưa hỗ trợ", "Trạng thái Collector"]) ct.firstChild.append(el("th", h));
      for (const c of data.client_capabilities || []) {
        const tr = el("tr");
        for (const v of [c.client, c.provider, c.counter_mode, (c.supported_fields || []).join(", "), (c.unsupported_fields || []).join(", "), c.collector_mode]) tr.append(el("td", unknown(v)));
        ct.append(tr);
      }
      body.append(ct, el("h4", "Trạng thái Telemetry theo Session"));
      const st = el("table", null, "trace-table");
      st.append(el("tr"));
      for (const h of ["Session", "Provider / Model", "Trạng thái", "Events", "Nguồn", "Nguyên nhân & Hướng xử lý"]) st.firstChild.append(el("th", h));
      for (const s of data.sessions || []) {
        const tr = el("tr");
        for (const v of [s.session_id.slice(0, 8), `${s.provider} / ${s.client_declared_model}`, s.telemetry_status, s.usage_event_count, (s.usage_sources || []).join(", ") || "—", `${s.reason} ${s.guidance}`]) tr.append(el("td", unknown(v)));
        st.append(tr);
      }
      body.append(st);
    } catch (e) {
      body.textContent = "Lỗi tải Telemetry Inspector: " + e.message;
    }
  }

  async function load(reset = true) {
    if (reset) offset = 0;
    const q = filters();
    if (!q.project_id) {
      $("trace-view").textContent = "Chọn dự án";
      return;
    }
    const request = ++epoch;
    busy = true;
    try {
      const data = await apiFetch("/api/v2/traces?" + new URLSearchParams({ ...q, limit: "100", offset: String(offset) }));
      if (request !== epoch) return;
      const fp = JSON.stringify([data.total, data.next_offset, (data.items || []).map(x => [x.trace_id, x.status, x.ended_at, x.input_tokens, x.output_tokens])]);
      rows = data.items || [];
      total = data.total || 0;
      next = data.next_offset;
      metrics = data.metrics || {};
      summary = data.summary || {};
      groups = data.groups || [];
      plannedSteps = data.planned_steps || [];
      if (fp !== lastFingerprint || reset) {
        lastFingerprint = fp;
        render();
      } else {
        renderSummary();
      }
      if (selectedId) {
        const updated = rows.find(r => r.trace_id === selectedId);
        if (updated && !$("trace-detail").contains(document.activeElement)) {
          selectedRow = updated;
          renderDetail(updated);
        }
      }
    } catch (e) {
      if (request === epoch) {
        rows = []; total = 0; next = null; metrics = {}; summary = {}; groups = []; plannedSteps = [];
        render();
        $("trace-view").textContent = e.message;
      }
    } finally {
      if (request === epoch) busy = false;
    }
  }

  function setupInteractions() {
    // Mouse pan without conflicting with node clicks
    const canvas = $("trace-canvas-wrap");
    let panning = false, startX = 0, startY = 0, sLeft = 0, sTop = 0;
    canvas.addEventListener("mousedown", e => {
      if (e.button !== 0 || e.target.closest("button,input,select,a,tr,summary")) return;
      panning = true;
      startX = e.clientX; startY = e.clientY;
      sLeft = canvas.scrollLeft; sTop = canvas.scrollTop;
      canvas.classList.add("is-panning");
    });
    window.addEventListener("mousemove", e => {
      if (!panning) return;
      canvas.scrollLeft = sLeft - (e.clientX - startX);
      canvas.scrollTop = sTop - (e.clientY - startY);
    });
    window.addEventListener("mouseup", () => {
      if (!panning) return;
      panning = false;
      canvas.classList.remove("is-panning");
    });

    // Resizable detail panel
    const resizer = $("trace-resizer");
    let resizing = false;
    resizer.addEventListener("mousedown", () => { resizing = true; });
    window.addEventListener("mousemove", e => {
      if (!resizing) return;
      const rect = $("trace-layout").getBoundingClientRect();
      const w = Math.max(240, Math.min(680, rect.right - e.clientX));
      $("trace-layout").style.setProperty("--detail-width", `${w}px`);
    });
    window.addEventListener("mouseup", () => { resizing = false; });

    // Toolbar controls
    document.querySelectorAll(".trace-mode-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        $("trace-mode").value = btn.dataset.mode;
        renderViewOnly();
      });
    });
    $("trace-mode").addEventListener("change", renderViewOnly);
    $("trace-zoom-in").addEventListener("click", () => { zoom = Math.min(2.0, +(zoom + 0.1).toFixed(2)); applyZoom(); });
    $("trace-zoom-out").addEventListener("click", () => { zoom = Math.max(0.5, +(zoom - 0.1).toFixed(2)); applyZoom(); });
    $("trace-zoom-reset").addEventListener("click", () => { zoom = 1.0; timeZoomScale = 1.0; applyZoom(); renderViewOnly(); });
    $("trace-fit").addEventListener("click", () => {
      const cw = Math.max(1, canvas.clientWidth - 24);
      const sw = Math.max(1, $("trace-view").scrollWidth);
      zoom = Math.max(0.5, Math.min(1.0, +(cw / sw).toFixed(2)));
      applyZoom();
    });
    $("trace-density").addEventListener("click", () => {
      compactMode = !compactMode;
      $("trace-workspace").classList.toggle("is-compact", compactMode);
      $("trace-density").textContent = compactMode ? "Detailed" : "Compact";
    });
    $("trace-hide-empty").addEventListener("click", () => {
      hideEmptyLanes = !hideEmptyLanes;
      $("trace-hide-empty").textContent = hideEmptyLanes ? "Hiện lane trống" : "Ẩn lane trống";
      renderGroups();
      renderViewOnly();
    });
    $("trace-collapse-all").addEventListener("click", () => {
      if (collapsedGroups.size) collapsedGroups.clear();
      else {
        for (const r of rows) collapsedGroups.add(`tree:${r.trace_id}`);
        for (const g of groups) collapsedGroups.add(`lane:${g.lane}`);
      }
      renderViewOnly();
    });
    $("trace-fullscreen").addEventListener("click", () => {
      const exp = $("trace-workspace").classList.toggle("is-expanded");
      $("trace-fullscreen").textContent = exp ? "Thu nhỏ" : "Mở rộng";
    });
    $("trace-detail-toggle").addEventListener("click", () => setDetailOpen(!detailOpen));
    $("trace-toggle-filters").addEventListener("click", () => { $("trace-filter-card").hidden = !$("trace-filter-card").hidden; });
    $("trace-toggle-inspector").addEventListener("click", () => {
      const card = $("trace-inspector-card");
      card.hidden = !card.hidden;
      if (!card.hidden) loadInspector();
    });
    $("trace-inspector-close").addEventListener("click", () => { $("trace-inspector-card").hidden = true; });

    for (const [id, fmt] of [["trace-export-json", "json"], ["trace-export-csv", "csv"]]) {
      $(id).addEventListener("click", () => {
        const q = filters();
        if (!q.project_id) return;
        window.open("/api/v2/traces/export?" + new URLSearchParams({ ...q, format: fmt }), "_blank");
      });
    }
  }

  window.openExecutionMap = (project, plan = "", task = "", session = "", attempt = "") => {
    $("trace-proj").value = project;
    sync();
    $("trace-wt").value = "";
    $("trace-plan").value = plan;
    $("trace-task").value = task;
    if ($("trace-attempt")) $("trace-attempt").value = attempt;
    $("trace-session").value = session;
    for (const id of ["trace-agent", "trace-model", "trace-status", "trace-from", "trace-to", "trace-search"]) $(id).value = "";
    syncScopeOptions();
    showTab("traces", $("trace-nav"));
  };

  window.addEventListener("hub-ready", () => {
    sync();
    setupInteractions();
    $("trace-proj").addEventListener("change", () => {
      sync();
      for (const id of ["trace-plan", "trace-task", "trace-attempt", "trace-session"]) { if ($(id)) $(id).value = ""; }
      syncScopeOptions();
      load();
    });
    $("trace-source-sel")?.addEventListener("change", () => {
      selectedExternalPlanId = "";
      $("trace-plan").value = "";
      $("trace-task").value = "";
      if ($("trace-attempt")) $("trace-attempt").value = "";
      syncScopeOptions();
      load();
    });
    $("trace-plan-sel")?.addEventListener("change", () => {
      const val = $("trace-plan-sel").value;
      const meta = scopePlansMeta[val];
      if (meta?.is_monitor_only) {
        selectedExternalPlanId = val;
        $("trace-plan").value = "";
      } else {
        selectedExternalPlanId = "";
        $("trace-plan").value = val;
      }
      $("trace-task").value = "";
      if ($("trace-attempt")) $("trace-attempt").value = "";
      syncScopeOptions();
      load();
    });
    $("trace-task-sel")?.addEventListener("change", () => {
      $("trace-task").value = $("trace-task-sel").value;
      if ($("trace-attempt")) $("trace-attempt").value = "";
      syncScopeOptions();
      load();
    });
    $("trace-attempt-sel")?.addEventListener("change", () => {
      if ($("trace-attempt")) $("trace-attempt").value = $("trace-attempt-sel").value;
      updateBreadcrumb();
      load();
    });
    $("trace-descendants")?.addEventListener("change", () => load());
    $("trace-scope-clear")?.addEventListener("click", () => {
      selectedExternalPlanId = "";
      if ($("trace-source-sel")) $("trace-source-sel").value = "all";
      for (const id of ["trace-plan", "trace-task", "trace-attempt", "trace-session"]) { if ($(id)) $(id).value = ""; }
      if ($("trace-descendants")) $("trace-descendants").checked = false;
      syncScopeOptions();
      load();
    });
    $("trace-refresh").addEventListener("click", () => { syncScopeOptions(); load(); });
    $("trace-sort").addEventListener("change", () => load());
    $("trace-search").addEventListener("keydown", e => { if (e.key === "Enter") load(); });
    $("trace-prev").addEventListener("click", () => { offset = Math.max(0, offset - 100); load(false); });
    $("trace-next").addEventListener("click", () => { if (next != null) { offset = next; load(false); } });
    window.addEventListener("hub-tab", e => { if (e.detail === "traces") { sync(); syncScopeOptions(); load(); } });
    setInterval(() => {
      if (!document.hidden && !busy && $("tab-traces").classList.contains("active")) load(false);
    }, 15000);
  });
})();
