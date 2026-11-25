/* V3.2.1 P14-P19 Dashboard UI: AI Providers, Project Egress Policy, Hub-managed API Agent & Hybrid Control.
 * All dynamic data rendered strictly via textContent / DOM APIs (zero API keys in browser state). */
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

  let cachedProviders = [];
  let cachedProjects = [];
  let cachedBusinesses = [];

  async function loadAllProviderState() {
    const provBox = $("prov-list");
    if (!provBox) return;
    provBox.textContent = "Đang tải danh sách AI Provider...";
    try {
      const [provs, projs, bizs, runs] = await Promise.all([
        apiFetch("/api/v321/providers"),
        apiFetch("/api/projects"),
        apiFetch("/api/v31/businesses"),
        apiFetch("/api/v321/agent-runs?limit=20"),
      ]);
      cachedProviders = provs.items || [];
      cachedProjects = projs.items || [];
      cachedBusinesses = (bizs.items || []).filter(b => !b.archived);

      // Populate selectors
      for (const sid of ["egress-proj-sel", "run-proj-sel"]) {
        const s = $(sid);
        if (s) {
          const prev = s.value;
          s.replaceChildren(...cachedProjects.map(p => new Option(`${p.name} (${p.project_id.slice(0, 8)})`, p.project_id)));
          if (prev && cachedProjects.some(p => p.project_id === prev)) s.value = prev;
        }
      }
      const bSel = $("run-biz-sel");
      if (bSel) {
        const prev = bSel.value;
        bSel.replaceChildren(...cachedBusinesses.map(b => new Option(b.name, b.id)));
        if (prev && cachedBusinesses.some(b => b.id === prev)) bSel.value = prev;
      }
      const pSel = $("run-prov-sel");
      if (pSel) {
        const prev = pSel.value;
        pSel.replaceChildren(...cachedProviders.map(p => new Option(`${p.display_name} [${p.billing_mode} · ${p.selected_model}]${p.enabled ? "" : " (Tắt)"}`, p.id)));
        if (prev && cachedProviders.some(p => p.id === prev)) pSel.value = prev;
      }

      // Render providers table
      provBox.replaceChildren();
      const tbl = el("table", null, "trace-table");
      const hr = el("tr");
      for (const h of ["Provider", "Loại tính phí", "Model & Thinking", "API Key Ref (Masked)", "Trạng thái", "Thao tác"]) hr.append(el("th", h));
      tbl.append(hr);
      for (const p of cachedProviders) {
        const tr = el("tr");
        tr.append(
          el("td", `${p.display_name} (${p.provider_key})`),
          el("td", p.billing_mode),
          el("td", `${p.selected_model || "—"} · think=${p.thinking_level} · maxOut=${p.max_output_tokens}`),
          el("td", p.credential_masked),
          el("td", p.enabled ? "Đang bật" : "Đã tắt")
        );
        const tdA = el("td");
        const acts = el("div", null, "row-actions");
        const bTog = el("button", p.enabled ? "Tắt" : "Bật", "secondary");
        bTog.type = "button";
        bTog.addEventListener("click", async () => {
          await apiFetch(`/api/v321/providers/${encodeURIComponent(p.id)}`, { method: "PUT", body: JSON.stringify({ enabled: !p.enabled }) });
          toast(`Đã ${p.enabled ? "tắt" : "bật"} ${p.display_name}.`);
          loadAllProviderState();
        });
        const bTest = el("button", "Kiểm tra cấu hình (0 phí)", "secondary");
        bTest.type = "button";
        bTest.addEventListener("click", async () => {
          const res = await apiFetch(`/api/v321/providers/${encodeURIComponent(p.id)}/test`, { method: "POST", body: JSON.stringify({ allow_paid_probe: false }) });
          toast(`${p.display_name}: ${res.status} (cred=${res.credential_present}, endpoint=${res.endpoint_valid})`);
        });
        acts.append(bTog, bTest);
        tdA.append(acts);
        tr.append(tdA);
        tbl.append(tr);
      }
      provBox.append(tbl, el("small", provs.budget_disclosure || "", "trace-muted"));

      // Render runs list
      renderRuns(runs.items || []);
      await loadEgressForSelectedProject();
    } catch (e) {
      provBox.textContent = "Lỗi tải AI Providers: " + e.message;
    }
  }

  async function loadEgressForSelectedProject() {
    const pid = $("egress-proj-sel")?.value;
    if (!pid) return;
    try {
      const eg = await apiFetch(`/api/v321/projects/${encodeURIComponent(pid)}/egress-policy`);
      $("egress-allow-paid").checked = Boolean(eg.allow_external_paid_api);
      $("egress-allowed-keys").value = (eg.allowed_provider_keys || []).join(", ");
      $("egress-max-task").value = String(eg.max_task_budget_usd ?? 0);
      $("egress-status").textContent = eg.default_fail_closed
        ? "Đang áp dụng mặc định an toàn (fail-closed: chặn API trả phí bên ngoài)."
        : `Đã lưu chính sách lúc ${vnTime(eg.updated_at)}.`;
    } catch (e) {
      $("egress-status").textContent = "Lỗi tải chính sách: " + e.message;
    }
  }

  function renderRuns(items) {
    const box = $("runs-list");
    if (!box) return;
    box.replaceChildren();
    if (!items.length) {
      box.append(el("p", "Chưa có phiên Hub-managed API Agent nào.", "trace-muted"));
      return;
    }
    const tbl = el("table", null, "trace-table");
    const hr = el("tr");
    for (const h of ["Thời gian (VN)", "Chế độ", "Model / Thinking", "Trạng thái", "Chi tiêu / Ngân sách (USD)", "Vòng / Lượt gọi", "Kết quả / Lý do dừng", "Thao tác"]) hr.append(el("th", h));
    tbl.append(hr);
    for (const r of items) {
      const tr = el("tr");
      tr.append(
        el("td", vnTime(r.created_at)),
        el("td", r.execution_mode),
        el("td", `${r.requested_model} (think=${r.thinking_level})`),
        el("td", r.status),
        el("td", `$${Number(r.spent_budget_usd || 0).toFixed(5)} / $${Number(r.max_budget_usd || 0).toFixed(2)}`),
        el("td", `${r.rounds_used}/${r.max_rounds} · ${r.calls_used}/${r.max_calls}`),
        el("td", (r.stop_reason || r.result_summary || "—").slice(0, 120))
      );
      const tdA = el("td");
      if (r.status === "running" || r.status === "pending_approval") {
        const bCan = el("button", "Hủy", "secondary");
        bCan.type = "button";
        bCan.addEventListener("click", async () => {
          await apiFetch(`/api/v321/agent-runs/${encodeURIComponent(r.id)}/cancel`, { method: "POST" });
          toast("Đã hủy phiên API Agent.");
          loadAllProviderState();
        });
        tdA.append(bCan);
      } else {
        tdA.append(el("span", r.handoff_id ? `Handoff: ${r.handoff_id.slice(0, 8)}` : "—", "trace-muted"));
      }
      tr.append(tdA);
      tbl.append(tr);
    }
    box.append(tbl);
  }

  window.addEventListener("hub-ready", () => {
    $("prov-refresh-btn")?.addEventListener("click", loadAllProviderState);
    $("egress-proj-sel")?.addEventListener("change", loadEgressForSelectedProject);
    $("egress-save-btn")?.addEventListener("click", async () => {
      const pid = $("egress-proj-sel")?.value;
      if (!pid) return;
      try {
        const keys = ($("egress-allowed-keys")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
        await apiFetch(`/api/v321/projects/${encodeURIComponent(pid)}/egress-policy`, {
          method: "PUT",
          body: JSON.stringify({
            allow_external_paid_api: Boolean($("egress-allow-paid")?.checked),
            allowed_provider_keys: keys,
            allow_redacted_only: true,
            max_task_budget_usd: Number($("egress-max-task")?.value) || 0,
            max_daily_budget_usd: (Number($("egress-max-task")?.value) || 0) * 10,
          }),
        });
        toast("Đã lưu chính sách Egress & Ngân sách dự án.");
        loadEgressForSelectedProject();
      } catch (e) { toast("Lỗi lưu Egress: " + e.message); }
    });

    $("run-preview-btn")?.addEventListener("click", async () => {
      try {
        const res = await apiFetch("/api/v321/agent-runs/preview", {
          method: "POST",
          body: JSON.stringify({
            project_id: $("run-proj-sel").value,
            business_id: $("run-biz-sel").value,
            execution_mode: $("run-mode-sel").value,
            provider_profile_id: $("run-prov-sel").value,
            thinking_level: $("run-think-sel").value,
            write_mode: "read_only",
            objective: ($("run-obj-inp").value || "").trim() || "Kiểm tra ngữ cảnh dự án",
            max_budget_usd: Number($("run-budget-inp").value) || 0,
          }),
        });
        $("run-preview-out").textContent = JSON.stringify({
          provider: res.provider_key,
          model: res.requested_model,
          egress_allowed: res.egress_allowed,
          memories: (res.memories || []).length,
          skills: (res.skills || []).map(s => s.slug),
          selected_tools: (res.selected_tools || []).map(t => `${t.server_name}.${t.tool_name}`),
          estimated_single_call_max_usd: res.estimated_single_call_max_usd,
        }, null, 2);
      } catch (e) { toast("Lỗi xem trước: " + e.message); }
    });

    $("run-start-btn")?.addEventListener("click", async () => {
      const obj = ($("run-obj-inp").value || "").trim();
      if (obj.length < 3) { toast("Nhập mục tiêu nhiệm vụ (>= 3 ký tự)."); return; }
      try {
        const res = await apiFetch("/api/v321/agent-runs", {
          method: "POST",
          body: JSON.stringify({
            project_id: $("run-proj-sel").value,
            business_id: $("run-biz-sel").value,
            execution_mode: $("run-mode-sel").value,
            provider_profile_id: $("run-prov-sel").value,
            thinking_level: $("run-think-sel").value,
            write_mode: "read_only",
            objective: obj,
            max_budget_usd: Number($("run-budget-inp").value) || 0,
            egress_consent_confirmed: Boolean($("run-consent-chk")?.checked),
            execute_now: true,
          }),
        });
        toast(`API Agent hoàn tất: status=${res.status}, chi tiêu=$${Number(res.spent_budget_usd || 0).toFixed(5)}`);
        loadAllProviderState();
      } catch (e) { toast("Bị chặn / Lỗi chạy API Agent: " + e.message); }
    });
  });

  window.addEventListener("hub-tab", ev => {
    if (ev.detail === "providers") loadAllProviderState();
  });
})();
