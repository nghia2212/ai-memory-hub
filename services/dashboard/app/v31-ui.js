/* V3.1 Dashboard UI: Quản lý nghiệp vụ (Business Workspace) & Kho MCP & Công cụ (Dynamic MCP Gateway).
 * All untrusted values rendered strictly via textContent / DOM APIs (no unsafe innerHTML interpolation). */
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

  let selectedBizId = null;
  let activeDetailTab = "overview";
  let cachedBizDetail = null;
  let cachedMcpServers = [];
  let cachedProjects = [];

  async function refreshProjectCache() {
    try {
      const r = await apiFetch("/api/projects");
      cachedProjects = r.items || [];
    } catch (_) {
      cachedProjects = [];
    }
  }

  // ==================== P1: QUẢN LÝ NGHIỆP VỤ ====================
  async function loadBusinesses() {
    const listBox = $("biz-list");
    if (!listBox) return;
    listBox.textContent = "Đang tải danh sách nghiệp vụ...";
    const params = new URLSearchParams();
    const q = ($("biz-search")?.value || "").trim();
    if (q) params.set("search", q);
    if ($("biz-show-archived")?.checked) params.set("include_archived", "true");
    if ($("biz-enabled-only")?.checked) params.set("enabled_only", "true");
    try {
      const [res, tpls] = await Promise.all([
        apiFetch("/api/v31/businesses?" + params),
        apiFetch("/api/v31/businesses/templates"),
      ]);
      // Populate template selector
      const tplSel = $("biz-template-sel");
      if (tplSel) {
        tplSel.replaceChildren(
          new Option("-- Tạo từ Template --", ""),
          ...(tpls.items || []).map(t => new Option(`${t.name}${t.is_builtin ? " (Mẫu chuẩn)" : ""}`, t.template_key))
        );
      }
      // Populate routing business selector in MCP tab
      const routeBiz = $("v31-route-biz");
      const invBiz = $("v31-inv-biz");
      const activeItems = (res.items || []).filter(b => !b.archived);
      if (routeBiz) {
        const cur = routeBiz.value;
        routeBiz.replaceChildren(...activeItems.map(b => new Option(`${b.name} (rev ${b.revision})`, b.id)));
        if (cur && activeItems.some(b => b.id === cur)) routeBiz.value = cur;
      }
      if (invBiz) {
        const cur = invBiz.value;
        invBiz.replaceChildren(new Option("Tất cả nghiệp vụ", ""), ...activeItems.map(b => new Option(b.name, b.id)));
        if (cur) invBiz.value = cur;
      }

      const items = res.items || [];
      listBox.replaceChildren();
      if (!items.length) {
        listBox.append(el("p", "Không có nghiệp vụ nào khớp bộ lọc.", "trace-muted"));
        return;
      }

      const tbl = el("table", null, "trace-table");
      const hdr = el("tr");
      for (const h of ["Nghiệp vụ", "Mô tả", "Dự án", "MCP (Online/Offline/Tổng)", "Model mặc định", "Trạng thái", "Cập nhật", "Thao tác"]) {
        hdr.append(el("th", h));
      }
      tbl.append(hdr);

      for (const b of items) {
        const tr = el("tr");
        const tdName = el("td");
        tdName.append(el("strong", `[${b.icon}] ${b.name}`), el("br"), el("small", `slug: ${b.slug || "—"} · rev ${b.revision}`, "trace-muted"));
        tr.append(
          tdName,
          el("td", (b.description || "").slice(0, 120)),
          el("td", String(b.project_count)),
          el("td", `${b.mcp_online_count} online / ${b.mcp_offline_count} offline / ${b.mcp_configured_count}`),
          el("td", b.default_model || "Chưa đặt")
        );
        const tdSt = el("td");
        const stLabel = b.archived ? "Đã lưu trữ" : b.enabled ? "Đang bật" : "Đang tắt";
        const stCls = b.archived ? "badge muted" : b.enabled ? "badge ok" : "badge warn";
        tdSt.append(el("span", stLabel, stCls));
        tr.append(tdSt, el("td", vnTime(b.updated_at)));

        const tdAct = el("td");
        const acts = el("div", null, "row-actions");
        const bOpen = el("button", "Chi tiết", "secondary");
        bOpen.type = "button";
        bOpen.addEventListener("click", () => openBusinessDetail(b.id));
        const bDup = el("button", "Nhân bản", "secondary");
        bDup.type = "button";
        bDup.addEventListener("click", async () => {
          const nn = prompt("Tên nghiệp vụ nhân bản:", `${b.name} (Copy)`);
          if (!nn) return;
          await apiFetch(`/api/v31/businesses/${encodeURIComponent(b.id)}/duplicate`, {
            method: "POST",
            body: JSON.stringify({ new_name: nn }),
          });
          toast("Đã nhân bản nghiệp vụ.");
          loadBusinesses();
        });
        const bExp = el("button", "Export", "secondary");
        bExp.type = "button";
        bExp.addEventListener("click", async () => {
          const exp = await apiFetch(`/api/v31/businesses/${encodeURIComponent(b.id)}/export`);
          const blob = new Blob([JSON.stringify(exp, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `business-${b.slug || b.id.slice(0, 8)}.json`;
          a.click();
        });
        acts.append(bOpen, bDup, bExp);

        if (!b.archived) {
          const bToggle = el("button", b.enabled ? "Tắt" : "Bật", "secondary");
          bToggle.type = "button";
          bToggle.addEventListener("click", async () => {
            await apiFetch(`/api/v31/businesses/${encodeURIComponent(b.id)}`, {
              method: "PUT",
              body: JSON.stringify({ enabled: !b.enabled }),
            });
            toast(b.enabled ? "Đã tắt nghiệp vụ." : "Đã bật nghiệp vụ.");
            loadBusinesses();
          });
          const bArch = el("button", "Xóa (Lưu trữ)", "secondary");
          bArch.type = "button";
          bArch.addEventListener("click", async () => {
            if (!confirm(`Lưu trữ nghiệp vụ "${b.name}"? (Không xóa MCP, Dự án, Bộ nhớ, Hội thoại hay Trace lịch sử)`)) return;
            const r = await apiFetch(`/api/v31/businesses/${encodeURIComponent(b.id)}/archive`, { method: "POST" });
            toast(`Đã lưu trữ nghiệp vụ (dự án giữ nguyên: ${r.dependencies?.linked_projects ?? 0}, phiên đang chạy: ${r.dependencies?.active_running_sessions ?? 0}).`);
            if (selectedBizId === b.id) $("biz-detail-card").style.display = "none";
            loadBusinesses();
          });
          acts.append(bToggle, bArch);
        } else {
          const bRest = el("button", "Khôi phục", "primary");
          bRest.type = "button";
          bRest.addEventListener("click", async () => {
            await apiFetch(`/api/v31/businesses/${encodeURIComponent(b.id)}/restore`, { method: "POST" });
            toast("Đã khôi phục nghiệp vụ.");
            loadBusinesses();
          });
          acts.append(bRest);
        }
        tdAct.append(acts);
        tr.append(tdAct);
        tbl.append(tr);
      }
      listBox.append(tbl);
    } catch (e) {
      listBox.textContent = "Lỗi tải nghiệp vụ: " + e.message;
    }
  }

  async function openBusinessDetail(bizId) {
    selectedBizId = bizId;
    const card = $("biz-detail-card");
    if (!card) return;
    card.style.display = "block";
    $("biz-detail-body").textContent = "Đang tải chi tiết nghiệp vụ...";
    try {
      await refreshProjectCache();
      const [detail, srvs] = await Promise.all([
        apiFetch(`/api/v31/businesses/${encodeURIComponent(bizId)}`),
        apiFetch("/api/v31/mcp/servers"),
      ]);
      cachedBizDetail = detail;
      cachedMcpServers = srvs.items || [];
      $("biz-detail-title").textContent = `[${detail.icon}] ${detail.name} (Phiên bản cấu hình #${detail.revision})`;
      renderBusinessDetailTab(activeDetailTab);
    } catch (e) {
      $("biz-detail-body").textContent = "Lỗi tải chi tiết: " + e.message;
    }
  }

  async function renderBusinessDetailTab(tabName) {
    activeDetailTab = tabName;
    document.querySelectorAll("#biz-detail-tabs button").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.biztab === tabName);
    });
    const body = $("biz-detail-body");
    const d = cachedBizDetail;
    if (!body || !d) return;
    body.replaceChildren();

    if (tabName === "overview") {
      const fName = el("input"); fName.value = d.name;
      const fIcon = el("input"); fIcon.value = d.icon || "briefcase";
      const fDesc = el("textarea"); fDesc.rows = 3; fDesc.value = d.description || "";
      const bSave = el("button", "Lưu thay đổi Tổng quan", "primary");
      bSave.type = "button";
      bSave.addEventListener("click", async () => {
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}`, {
          method: "PUT",
          body: JSON.stringify({ name: fName.value.trim(), icon: fIcon.value.trim(), description: fDesc.value.trim() }),
        });
        toast("Đã cập nhật nghiệp vụ (tăng revision).");
        await openBusinessDetail(d.id);
        loadBusinesses();
      });
      body.append(
        el("label", "Tên nghiệp vụ"), fName,
        el("label", "Biểu tượng (Icon)"), fIcon,
        el("label", "Mô tả"), fDesc,
        el("p", `Trạng thái: ${d.archived ? "Đã lưu trữ" : d.enabled ? "Đang bật" : "Đang tắt"} · Phiên đang chạy gắn nghiệp vụ: ${(d.active_sessions || []).length}`, "trace-muted"),
        bSave
      );
    } else if (tabName === "projects") {
      const sel = el("select");
      for (const p of cachedProjects) sel.append(new Option(`${p.name} (${p.project_id.slice(0, 8)})`, p.project_id));
      const bAdd = el("button", "Gắn dự án vào nghiệp vụ", "primary");
      bAdd.type = "button";
      bAdd.addEventListener("click", async () => {
        if (!sel.value) return;
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/projects`, {
          method: "POST",
          body: JSON.stringify({ project_id: sel.value }),
        });
        toast("Đã gắn dự án.");
        await openBusinessDetail(d.id);
        loadBusinesses();
      });
      body.append(el("label", "Chọn dự án để gắn"), sel, bAdd);
      const ul = el("ul");
      for (const p of d.projects || []) {
        const li = el("li");
        li.append(el("span", `${p.name} (${p.canonical_identity}) `));
        const bRm = el("button", "Gỡ", "secondary");
        bRm.type = "button";
        bRm.addEventListener("click", async () => {
          await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/projects/${encodeURIComponent(p.project_id)}`, { method: "DELETE" });
          toast("Đã gỡ dự án khỏi nghiệp vụ.");
          await openBusinessDetail(d.id);
          loadBusinesses();
        });
        li.append(bRm);
        ul.append(li);
      }
      body.append(el("h4", "Dự án đã liên kết"), ul);
    } else if (tabName === "mcps") {
      const sel = el("select");
      for (const s of cachedMcpServers) sel.append(new Option(`${s.name} [${s.transport} · ${s.approval_state} · ${s.health_status}]`, s.id));
      const bBind = el("button", "Thêm MCP vào nghiệp vụ", "primary");
      bBind.type = "button";
      bBind.addEventListener("click", async () => {
        if (!sel.value) return;
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/mcps`, {
          method: "POST",
          body: JSON.stringify({ server_id: sel.value, enabled: true, required: false, priority: 50 }),
        });
        toast("Đã gắn MCP vào nghiệp vụ.");
        await openBusinessDetail(d.id);
        loadBusinesses();
      });
      body.append(el("label", "Thêm MCP từ Kho công cụ"), sel, bBind);

      for (const m of d.mcp_bindings || []) {
        const mc = el("div", null, "card");
        mc.style.marginTop = "10px";
        const top = el("div", null, "row-actions");
        top.append(
          el("strong", `${m.server_name} (${m.transport})`),
          el("span", `${m.approval_state} · ${m.health_status}`, `badge ${m.health_status === "ready" ? "ok" : "warn"}`)
        );
        const bTog = el("button", m.enabled ? "Tắt trong nghiệp vụ" : "Bật trong nghiệp vụ", "secondary");
        bTog.type = "button";
        bTog.addEventListener("click", async () => {
          await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/mcps`, {
            method: "POST",
            body: JSON.stringify({ server_id: m.server_id, enabled: !m.enabled, required: m.required, priority: m.priority }),
          });
          toast("Đã cập nhật trạng thái MCP trong nghiệp vụ.");
          await openBusinessDetail(d.id);
          loadBusinesses();
        });
        const bUnbind = el("button", "Gỡ khỏi nghiệp vụ", "secondary");
        bUnbind.type = "button";
        bUnbind.addEventListener("click", async () => {
          await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/mcps/${encodeURIComponent(m.server_id)}`, { method: "DELETE" });
          toast("Đã gỡ MCP khỏi nghiệp vụ này (không ảnh hưởng nghiệp vụ khác).");
          await openBusinessDetail(d.id);
          loadBusinesses();
        });
        top.append(bTog, bUnbind);
        mc.append(top);

        if ((m.tools || []).length) {
          const tbl = el("table", null, "trace-table");
          const hr = el("tr");
          for (const h of ["Tool", "Mức tác động", "Quyền thực thi (Backend Enforced)"]) hr.append(el("th", h));
          tbl.append(hr);
          for (const t of m.tools) {
            const tr = el("tr");
            tr.append(el("td", `${t.tool_name} — ${t.description}`), el("td", t.side_effect_level));
            const tdP = el("td");
            const psel = el("select");
            psel.style.width = "180px";
            psel.append(new Option("Deny (Từ chối mặc định)", "deny"), new Option("Approval (Cần phê duyệt)", "approval"), new Option("Allow (Cho phép)", "allow"));
            psel.value = t.policy || "deny";
            psel.addEventListener("change", async () => {
              await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/tools/policy`, {
                method: "POST",
                body: JSON.stringify({ tool_id: t.tool_id, policy: psel.value }),
              });
              toast(`Đã cập nhật quyền ${t.tool_name} → ${psel.value}`);
            });
            tdP.append(psel);
            tr.append(tdP);
            tbl.append(tr);
          }
          mc.append(tbl);
        } else {
          mc.append(el("p", "Chưa có tool nào được discover trên server này.", "trace-muted"));
        }
        body.append(mc);
      }
    } else if (tabName === "model") {
      const taModel = el("textarea"); taModel.rows = 6; taModel.value = JSON.stringify(d.model_policy || {}, null, 2);
      const taBudget = el("textarea"); taBudget.rows = 6; taBudget.value = JSON.stringify(d.budget_policy || {}, null, 2);
      const bSave = el("button", "Lưu Chính sách Model & Ngân sách", "primary");
      bSave.type = "button";
      bSave.addEventListener("click", async () => {
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}`, {
          method: "PUT",
          body: JSON.stringify({ model_policy: JSON.parse(taModel.value), budget_policy: JSON.parse(taBudget.value) }),
        });
        toast("Đã lưu Model & Budget policy.");
        await openBusinessDetail(d.id);
        loadBusinesses();
      });
      body.append(el("label", "Model Policy (JSON)"), taModel, el("label", "Budget & Quota Policy (JSON)"), taBudget, bSave);
    } else if (tabName === "instructions") {
      const ta = el("textarea"); ta.rows = 8; ta.value = d.agent_instructions || "";
      const bSave = el("button", "Lưu Agent Instructions", "primary");
      bSave.type = "button";
      bSave.addEventListener("click", async () => {
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}`, {
          method: "PUT",
          body: JSON.stringify({ agent_instructions: ta.value }),
        });
        toast("Đã lưu Agent Instructions.");
        await openBusinessDetail(d.id);
      });
      body.append(el("label", "Hướng dẫn tác tử (Agent Instructions)"), ta, bSave);
    } else if (tabName === "workflow" || tabName === "memory" || tabName === "resources") {
      const fieldMap = { workflow: ["workflow_policy", "Workflow Policy (JSON)"], memory: ["memory_policy", "Memory Policy (JSON)"], resources: ["resource_policy", "Resource Limits (RAM/CPU/Concurrency JSON)"] };
      const [key, label] = fieldMap[tabName];
      const ta = el("textarea"); ta.rows = 8; ta.value = JSON.stringify(d[key] || {}, null, 2);
      const bSave = el("button", `Lưu ${label}`, "primary");
      bSave.type = "button";
      bSave.addEventListener("click", async () => {
        const payload = {};
        payload[key] = JSON.parse(ta.value);
        await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}`, { method: "PUT", body: JSON.stringify(payload) });
        toast("Đã lưu cấu hình.");
        await openBusinessDetail(d.id);
      });
      body.append(el("label", label), ta, bSave);
    } else if (tabName === "versions") {
      const vers = await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/versions`);
      const tbl = el("table", null, "trace-table");
      const hr = el("tr");
      for (const h of ["Revision", "Thời gian", "Tên / Trạng thái", "Thao tác"]) hr.append(el("th", h));
      tbl.append(hr);
      for (const v of vers.items || []) {
        const tr = el("tr");
        tr.append(el("td", `#${v.revision}`), el("td", vnTime(v.created_at)), el("td", `${v.snapshot?.name || "—"} · enabled=${v.snapshot?.enabled}`));
        const tdA = el("td");
        const bR = el("button", "Khôi phục phiên bản này", "secondary");
        bR.type = "button";
        bR.addEventListener("click", async () => {
          await apiFetch(`/api/v31/businesses/${encodeURIComponent(d.id)}/versions/${v.revision}/restore`, { method: "POST" });
          toast(`Đã khôi phục cấu hình từ revision #${v.revision}.`);
          await openBusinessDetail(d.id);
          loadBusinesses();
        });
        tdA.append(bR);
        tr.append(tdA);
        tbl.append(tr);
      }
      body.append(tbl);
    }
  }

  // ==================== P2 & P3 & P9: KHO MCP & CÔNG CỤ ====================
  async function loadGatewayMcp() {
    const monBox = $("v31-mcp-monitoring");
    const srvBox = $("v31-mcp-servers");
    const invBox = $("v31-mcp-invocations");
    if (!srvBox) return;
    srvBox.textContent = "Đang tải Kho MCP & Công cụ...";
    try {
      const invParams = new URLSearchParams({ limit: "30" });
      if ($("v31-inv-biz")?.value) invParams.set("business_id", $("v31-inv-biz").value);
      const [mon, srvs, invs] = await Promise.all([
        apiFetch("/api/v31/mcp/monitoring"),
        apiFetch("/api/v31/mcp/servers?include_archived=" + ($("v31-mcp-show-archived")?.checked ? "true" : "false")),
        apiFetch("/api/v31/mcp/invocations?" + invParams),
      ]);
      cachedMcpServers = srvs.items || [];

      if (monBox) {
        monBox.replaceChildren();
        const kpis = [
          ["Tổng MCP", mon.servers_total],
          ["Online (Ready)", mon.online],
          ["Offline / Lỗi", mon.offline],
          ["Tiến trình chạy", mon.running_processes],
          ["Chờ duyệt", mon.pending_approval],
          ["Chưa dùng", mon.unused],
          ["Lượt gọi (OK/Fail)", `${mon.invocations_total} (${mon.invocations_success}/${mon.invocations_failed})`],
        ];
        for (const [label, val] of kpis) {
          const c = el("div", null, "card");
          c.append(el("small", label, "trace-muted"), el("h3", String(val)));
          monBox.append(c);
        }
      }

      srvBox.replaceChildren();
      for (const s of cachedMcpServers) {
        const card = el("div", null, "card");
        card.style.marginBottom = "12px";
        const top = el("div", null, "trace-page-head");
        top.append(
          el("h3", `${s.name} (${s.version})`),
          el("span", `${s.transport} · ${s.approval_state} · ${s.health_status}`, `badge ${s.health_status === "ready" ? "ok" : s.approval_state === "pending_approval" ? "warn" : "muted"}`)
        );
        card.append(
          top,
          el("p", s.description || "Không có mô tả.", "trace-muted"),
          el("small", `Endpoint/Exec: ${s.endpoint_or_cmd} · Policy: ${s.activation_policy} · Timeout: ${s.timeout_sec}s · Concurrency: ${s.concurrency_limit} · RAM limit: ${s.ram_limit_mb}MB · Latency: ${s.last_latency_ms ?? "—"}ms · Checked: ${vnTime(s.last_checked_at)}`, "trace-muted")
        );
        if (s.last_error_code) {
          card.append(el("p", `Lỗi gần nhất: ${s.last_error_code} — ${s.last_error_message || ""}`, "badge err"));
        }
        const usedBy = (s.used_by_businesses || []).map(b => b.name).join(", ");
        card.append(el("p", `Nghiệp vụ đang dùng: ${usedBy || "Chưa gắn nghiệp vụ nào"} · Lượt gọi: ${s.metrics.invocation_count} (OK: ${s.metrics.success_count}, Lỗi: ${s.metrics.failure_count})`, "trace-muted"));

        const acts = el("div", null, "row-actions");
        const bHealth = el("button", "Kiểm tra kết nối (Health)", "secondary");
        bHealth.type = "button";
        bHealth.addEventListener("click", async () => {
          const r = await apiFetch(`/api/v31/mcp/servers/${encodeURIComponent(s.id)}/health`, { method: "POST" });
          toast(`Health ${s.name}: ${r.health_status} (${r.latency_ms ?? "?"}ms)`);
          loadGatewayMcp();
        });
        const bDisc = el("button", "Discovery Tools", "secondary");
        bDisc.type = "button";
        bDisc.addEventListener("click", async () => {
          try {
            const r = await apiFetch(`/api/v31/mcp/servers/${encodeURIComponent(s.id)}/discover`, { method: "POST" });
            toast(`Discovery ${s.name}: ${r.discovered_count} tools (+${r.added.length} mới, ~${r.changed.length} đổi, -${r.removed.length} gỡ)`);
            loadGatewayMcp();
          } catch (e) { toast("Lỗi Discovery: " + e.message); }
        });
        acts.append(bHealth, bDisc);

        if (s.trust_tier !== "builtin") {
          const dec = s.approval_state === "approved" ? "revoke" : "approve";
          const bApp = el("button", dec === "approve" ? "Phê duyệt" : "Thu hồi duyệt", "secondary");
          bApp.type = "button";
          bApp.addEventListener("click", async () => {
            await apiFetch(`/api/v3/mcp/servers/${encodeURIComponent(s.id)}/approval`, { method: "POST", body: JSON.stringify({ decision: dec }) });
            toast("Đã cập nhật phê duyệt MCP.");
            loadGatewayMcp();
          });
          const bArch = el("button", s.archived ? "Khôi phục" : "Vô hiệu hóa", "secondary");
          bArch.type = "button";
          bArch.addEventListener("click", async () => {
            await apiFetch(`/api/v31/mcp/servers/${encodeURIComponent(s.id)}/${s.archived ? "restore" : "archive"}`, { method: "POST" });
            toast(s.archived ? "Đã khôi phục MCP." : "Đã vô hiệu hóa MCP.");
            loadGatewayMcp();
          });
          acts.append(bApp, bArch);
        }
        if (s.process_status === "running") {
          const bStop = el("button", "Dừng tiến trình", "secondary");
          bStop.type = "button";
          bStop.addEventListener("click", async () => {
            await apiFetch(`/api/v31/mcp/servers/${encodeURIComponent(s.id)}/stop`, { method: "POST" });
            toast("Đã dừng tiến trình MCP do Hub sở hữu.");
            loadGatewayMcp();
          });
          acts.append(bStop);
        }
        card.append(acts);

        if ((s.tools || []).length) {
          const ul = el("ul");
          for (const t of s.tools) {
            ul.append(el("li", `${t.tool_name} [${t.side_effect_level}] ${t.removed ? "(ĐÃ GỠ) " : ""}— ${t.description} (hash: ${t.input_schema_hash || "—"})`));
          }
          card.append(ul);
        }
        srvBox.append(card);
      }

      if (invBox) {
        invBox.replaceChildren();
        const tbl = el("table", null, "trace-table");
        const hr = el("tr");
        for (const h of ["Thời gian (VN)", "Server", "Tool", "Trạng thái", "Độ trễ", "Actor", "Lỗi / Tóm tắt"]) hr.append(el("th", h));
        tbl.append(hr);
        for (const inv of invs.items || []) {
          const tr = el("tr");
          for (const v of [
            vnTime(inv.started_at),
            inv.server_id.slice(0, 18),
            inv.tool_name,
            inv.status,
            inv.latency_ms != null ? `${inv.latency_ms}ms` : "—",
            inv.actor,
            inv.error_code ? `${inv.error_code}: ${inv.error_message || ""}` : (inv.output_summary || "").slice(0, 90),
          ]) tr.append(el("td", v));
          tbl.append(tr);
        }
        invBox.append(tbl);
      }
    } catch (e) {
      srvBox.textContent = "Lỗi tải Kho MCP: " + e.message;
    }
  }

  window.addEventListener("hub-ready", () => {
    // Business tab wiring
    $("biz-refresh")?.addEventListener("click", loadBusinesses);
    $("biz-search")?.addEventListener("keydown", e => { if (e.key === "Enter") loadBusinesses(); });
    $("biz-show-archived")?.addEventListener("change", loadBusinesses);
    $("biz-enabled-only")?.addEventListener("change", loadBusinesses);

    $("biz-create-btn")?.addEventListener("click", async () => {
      const name = ($("biz-new-name")?.value || "").trim();
      if (!name) { toast("Nhập tên nghiệp vụ."); return; }
      try {
        await apiFetch("/api/v31/businesses", {
          method: "POST",
          body: JSON.stringify({
            name,
            icon: ($("biz-new-icon")?.value || "briefcase").trim(),
            description: ($("biz-new-desc")?.value || "").trim(),
          }),
        });
        $("biz-new-name").value = "";
        $("biz-new-desc").value = "";
        toast("Đã tạo nghiệp vụ mới.");
        loadBusinesses();
      } catch (e) { toast("Lỗi tạo nghiệp vụ: " + e.message); }
    });

    $("biz-from-tpl-btn")?.addEventListener("click", async () => {
      const tplKey = $("biz-template-sel")?.value;
      if (!tplKey) { toast("Chọn một template trước."); return; }
      const name = prompt("Tên nghiệp vụ mới từ template (để trống để tự sinh):") || null;
      try {
        await apiFetch("/api/v31/businesses/from-template", {
          method: "POST",
          body: JSON.stringify({ template_key: tplKey, name }),
        });
        toast("Đã tạo nghiệp vụ từ template.");
        loadBusinesses();
      } catch (e) { toast("Lỗi tạo từ template: " + e.message); }
    });

    $("biz-import-btn")?.addEventListener("click", async () => {
      const raw = prompt("Dán JSON cấu hình nghiệp vụ cần Import:");
      if (!raw) return;
      try {
        await apiFetch("/api/v31/businesses/import", {
          method: "POST",
          body: JSON.stringify({ payload: JSON.parse(raw) }),
        });
        toast("Đã import nghiệp vụ.");
        loadBusinesses();
      } catch (e) { toast("Lỗi import: " + e.message); }
    });

    document.querySelectorAll("#biz-detail-tabs button").forEach(btn => {
      btn.addEventListener("click", () => renderBusinessDetailTab(btn.dataset.biztab));
    });

    // MCP Gateway tab wiring
    $("v31-mcp-refresh")?.addEventListener("click", loadGatewayMcp);
    $("v31-mcp-show-archived")?.addEventListener("change", loadGatewayMcp);
    $("v31-inv-biz")?.addEventListener("change", loadGatewayMcp);

    $("v31-mcp-add-btn")?.addEventListener("click", async () => {
      const name = ($("v31-mcp-name")?.value || "").trim();
      const transport = $("v31-mcp-transport")?.value || "streamable_http";
      const ep = ($("v31-mcp-endpoint")?.value || "").trim();
      const exe = ($("v31-mcp-exec")?.value || "").trim();
      const argsRaw = ($("v31-mcp-args")?.value || "").trim();
      const args = argsRaw ? argsRaw.split(/\s+/) : [];
      if (!name) { toast("Nhập tên MCP server."); return; }
      try {
        await apiFetch("/api/v31/mcp/servers", {
          method: "POST",
          body: JSON.stringify({
            name,
            description: ($("v31-mcp-desc")?.value || "").trim(),
            adapter_transport: transport,
            endpoint: ep,
            executable: exe,
            args,
          }),
        });
        $("v31-mcp-name").value = "";
        toast("Đã đăng ký MCP mới (trạng thái: pending_approval).");
        loadGatewayMcp();
      } catch (e) { toast("Lỗi đăng ký MCP: " + e.message); }
    });

    $("v31-route-btn")?.addEventListener("click", async () => {
      const pid = $("mcp-proj")?.value;
      const bid = $("v31-route-biz")?.value;
      const caps = ($("v31-route-caps")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
      if (!pid || !bid || !caps.length) { toast("Chọn Dự án, Nghiệp vụ và nhập Capabilities."); return; }
      try {
        const res = await apiFetch("/api/v31/mcp/route", {
          method: "POST",
          body: JSON.stringify({
            project_id: pid,
            business_id: bid,
            required_capabilities: caps,
            allow_write: Boolean($("v31-route-write")?.checked),
            routing_mode: $("v31-route-mode")?.value || "automatic",
          }),
        });
        $("v31-route-result").replaceChildren(el("pre", JSON.stringify(res, null, 2)));
        loadGatewayMcp();
      } catch (e) {
        $("v31-route-result").textContent = "Lỗi routing: " + e.message;
      }
    });

    window.addEventListener("hub-tab", e => {
      if (e.detail === "business") loadBusinesses();
      if (e.detail === "mcp") { loadBusinesses(); loadGatewayMcp(); }
    });
  });
})();
