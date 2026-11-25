/* V3.2 (SK32) Dashboard UI: Kho Skill & Quy tắc (Centralized Skill Management) + Skill Telemetry.
 * All untrusted markdown/values rendered strictly via textContent / DOM APIs (no raw HTML execution). */
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

  let selectedSkillId = null;

  async function loadSkills() {
    const box = $("sk32-list");
    if (!box) return;
    box.textContent = "Đang tải Kho Skill...";
    const params = new URLSearchParams();
    if ($("sk32-filter-ns")?.value) params.set("namespace", $("sk32-filter-ns").value);
    if ($("sk32-filter-life")?.value) params.set("lifecycle", $("sk32-filter-life").value);
    if ($("sk32-filter-tag")?.value.trim()) params.set("tag", $("sk32-filter-tag").value.trim());
    if ($("sk32-filter-q")?.value.trim()) params.set("search", $("sk32-filter-q").value.trim());
    try {
      const res = await apiFetch("/api/v32/skills?" + params);
      const items = res.items || [];
      box.replaceChildren();
      if (!items.length) {
        box.append(el("p", "Không có skill nào khớp bộ lọc.", "trace-muted"));
        return;
      }
      const tbl = el("table", null, "trace-table");
      const hr = el("tr");
      for (const h of ["Namespace / Slug", "Tiêu đề", "Phiên bản Active", "Tags", "Bindings", "Trạng thái", "Thao tác"]) hr.append(el("th", h));
      tbl.append(hr);
      for (const s of items) {
        const tr = el("tr");
        tr.append(
          el("td", `${s.namespace}/${s.slug}${s.is_starter ? " ★" : ""}`),
          el("td", s.title),
          el("td", s.active_version ? `v${s.active_version} (${(s.active_hash || "").slice(0, 8)})` : "Chưa publish"),
          el("td", (s.tags || []).join(", ") || "—"),
          el("td", String(s.binding_count)),
          el("td", s.lifecycle)
        );
        const tdA = el("td");
        const acts = el("div", null, "row-actions");
        const bOpen = el("button", "Chi tiết / Sửa", "secondary");
        bOpen.type = "button";
        bOpen.addEventListener("click", () => openSkillDetail(s.id));
        const bExp = el("button", "Export", "secondary");
        bExp.type = "button";
        bExp.addEventListener("click", async () => {
          const exp = await apiFetch(`/api/v32/skills/${encodeURIComponent(s.id)}/export`);
          const blob = new Blob([JSON.stringify(exp, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `skill-${s.slug}.json`;
          a.click();
        });
        const bDel = el("button", s.active_version ? "Lưu trữ" : "Xóa Draft", "secondary");
        bDel.type = "button";
        bDel.addEventListener("click", async () => {
          if (!confirm(`Xử lý skill "${s.title}"? (Skill đã publish sẽ được lưu trữ để giữ bằng chứng lịch sử)`)) return;
          const r = await apiFetch(`/api/v32/skills/${encodeURIComponent(s.id)}`, { method: "DELETE" });
          toast(`Đã thực hiện: ${r.action}`);
          loadSkills();
        });
        acts.append(bOpen, bExp, bDel);
        tdA.append(acts);
        tr.append(tdA);
        tbl.append(tr);
      }
      box.append(tbl);
    } catch (e) {
      box.textContent = "Lỗi tải Kho Skill: " + e.message;
    }
  }

  async function openSkillDetail(skillId) {
    selectedSkillId = skillId;
    const card = $("sk32-detail-card");
    const body = $("sk32-detail-body");
    if (!card || !body) return;
    card.style.display = "block";
    body.textContent = "Đang tải chi tiết Skill...";
    try {
      const d = await apiFetch(`/api/v32/skills/${encodeURIComponent(skillId)}`);
      $("sk32-detail-title").textContent = `${d.namespace}/${d.slug} — ${d.title} (${d.lifecycle})`;
      body.replaceChildren();

      // MCP Dependencies & Manifest summary
      const latest = (d.versions || [])[0];
      const man = latest?.manifest || {};
      const depBox = el("div", null, "row-actions");
      depBox.style.marginBottom = "10px";
      depBox.append(
        el("span", `Required MCP: ${(man.required_mcp_ids || []).join(", ") || "Không"}`, "badge muted"),
        el("span", `Optional MCP: ${(man.optional_mcp_ids || []).join(", ") || "Không"}`, "badge muted"),
        el("span", `Required Tools: ${(man.required_tool_names || []).join(", ") || "Không"}`, "badge muted"),
        el("span", `Capabilities: ${(man.applicable_capabilities || []).join(", ") || "Chung"}`, "badge ok")
      );
      body.append(depBox);

      // Versions table with Publish / Rollback
      const vTbl = el("table", null, "trace-table");
      const vHr = el("tr");
      for (const h of ["Version", "Trạng thái", "Hash", "Tác giả / Duyệt", "Thời gian", "Thao tác"]) vHr.append(el("th", h));
      vTbl.append(vHr);
      for (const v of d.versions || []) {
        const tr = el("tr");
        const isActive = v.id === d.active_version_id;
        tr.append(
          el("td", `v${v.version}${isActive ? " (ACTIVE)" : ""}`),
          el("td", v.state),
          el("td", v.content_hash.slice(0, 12)),
          el("td", `${v.author} / ${v.approved_by || "—"}`),
          el("td", vnTime(v.created_at))
        );
        const tdA = el("td");
        if (v.state === "draft") {
          const bPub = el("button", "Phê duyệt & Publish", "primary");
          bPub.type = "button";
          bPub.addEventListener("click", async () => {
            await apiFetch(`/api/v32/skills/${encodeURIComponent(d.id)}/publish`, {
              method: "POST",
              body: JSON.stringify({ version_id: v.id, set_active: true }),
            });
            toast(`Đã publish v${v.version}.`);
            openSkillDetail(d.id);
            loadSkills();
          });
          tdA.append(bPub);
        } else if (v.state === "published" && !isActive) {
          const bRb = el("button", "Pin / Rollback về bản này", "secondary");
          bRb.type = "button";
          bRb.addEventListener("click", async () => {
            await apiFetch(`/api/v32/skills/${encodeURIComponent(d.id)}/rollback`, {
              method: "POST",
              body: JSON.stringify({ version_id: v.id }),
            });
            toast(`Đã rollback active version về v${v.version}.`);
            openSkillDetail(d.id);
            loadSkills();
          });
          tdA.append(bRb);
        }
        tr.append(tdA);
        vTbl.append(tr);
      }
      body.append(el("h4", "Lịch sử phiên bản (Immutable)"), vTbl);

      // Safe markdown preview + Create New Draft Version form
      if (latest) {
        body.append(el("h4", `Nội dung SKILL.md (v${latest.version} — Safe Preview)`), el("pre", latest.markdown_body));
      }

      const editWrap = el("div", null, "card");
      editWrap.style.marginTop = "10px";
      editWrap.append(el("h4", "Tạo phiên bản Draft mới (không sửa lịch sử bản đã Publish)"));
      const inVer = el("input"); inVer.value = latest ? `${latest.version}-next` : "1.0.1";
      const inBody = el("textarea"); inBody.rows = 4; inBody.value = latest?.markdown_body || "";
      const bNewDraft = el("button", "Lưu thành Draft mới", "secondary");
      bNewDraft.type = "button";
      bNewDraft.addEventListener("click", async () => {
        try {
          await apiFetch(`/api/v32/skills/${encodeURIComponent(d.id)}/drafts`, {
            method: "POST",
            body: JSON.stringify({
              version: inVer.value.trim(),
              manifest: { ...man, name: man.name || d.title, version: inVer.value.trim() },
              markdown_body: inBody.value,
              expected_updated_at: d.updated_at,
            }),
          });
          toast(`Đã tạo Draft v${inVer.value.trim()}.`);
          openSkillDetail(d.id);
        } catch (e) { toast("Lỗi tạo draft: " + e.message); }
      });
      editWrap.append(el("label", "Số phiên bản mới"), inVer, el("label", "Nội dung SKILL.md mới"), inBody, bNewDraft);
      body.append(editWrap);

      // Add Binding form
      const bWrap = el("div", null, "card");
      bWrap.style.marginTop = "10px";
      bWrap.append(el("h4", "Gắn phạm vi (Scoped Binding: Global / Business / Project / Agent Role)"));
      const selType = el("select");
      for (const [val, lbl] of [["global", "Global"], ["business", "Business Workspace"], ["project", "Project"], ["agent_role", "Agent Role"]]) {
        selType.append(new Option(lbl, val));
      }
      const inScope = el("input"); inScope.value = "global"; inScope.placeholder = "global / <business_id> / <project_id> / planner";
      const selMode = el("select");
      for (const m of ["enabled", "disabled", "inherit"]) selMode.append(new Option(m, m));
      const chkReq = el("input"); chkReq.type = "checkbox"; chkReq.style.width = "auto";
      const chkMand = el("input"); chkMand.type = "checkbox"; chkMand.style.width = "auto";
      const bSaveBind = el("button", "Lưu Binding", "primary");
      bSaveBind.type = "button";
      bSaveBind.addEventListener("click", async () => {
        try {
          await apiFetch("/api/v32/bindings", {
            method: "POST",
            body: JSON.stringify({
              scope_type: selType.value,
              scope_id: inScope.value.trim(),
              skill_id: d.id,
              mode: selMode.value,
              required: chkReq.checked,
              mandatory: chkMand.checked,
            }),
          });
          toast("Đã lưu Scoped Binding.");
          openSkillDetail(d.id);
          loadSkills();
        } catch (e) { toast("Lỗi binding: " + e.message); }
      });
      const lblRow = el("div", null, "row-actions");
      const l1 = el("label"); l1.append(chkReq, " Required");
      const l2 = el("label"); l2.append(chkMand, " Mandatory (không thể bị tắt ở cấp dưới)");
      lblRow.append(l1, l2);
      bWrap.append(el("label", "Cấp phạm vi"), selType, el("label", "Scope ID"), inScope, el("label", "Chế độ"), selMode, lblRow, bSaveBind);

      if ((d.bindings || []).length) {
        const ul = el("ul");
        for (const b of d.bindings) {
          const li = el("li");
          li.append(el("span", `${b.scope_type}:${b.scope_id} → ${b.mode} (prio=${b.priority}${b.required ? ", required" : ""}${b.mandatory ? ", MANDATORY" : ""}) `));
          const bRm = el("button", "Gỡ", "secondary");
          bRm.type = "button";
          bRm.addEventListener("click", async () => {
            await apiFetch(`/api/v32/bindings/${encodeURIComponent(b.id)}`, { method: "DELETE" });
            toast("Đã gỡ binding.");
            openSkillDetail(d.id);
            loadSkills();
          });
          li.append(bRm);
          ul.append(li);
        }
        bWrap.append(ul);
      }
      body.append(bWrap);
    } catch (e) {
      body.textContent = "Lỗi tải chi tiết Skill: " + e.message;
    }
  }

  async function loadCandidates() {
    const pid = $("sk32-cand-proj")?.value;
    const box = $("sk32-cand-list");
    if (!pid || !box) return;
    box.textContent = "Đang tải hàng đợi Candidate Skills...";
    try {
      const res = await apiFetch(`/api/v32/candidates?project_id=${encodeURIComponent(pid)}`);
      const items = res.items || [];
      box.replaceChildren();
      if (!items.length) {
        box.append(el("p", "Chưa có đề xuất Skill nào từ Verified Memory.", "trace-muted"));
        return;
      }
      for (const c of items) {
        const card = el("div", null, "card");
        card.style.marginBottom = "8px";
        card.append(
          el("strong", `${c.proposed_title} (${c.proposed_slug}) — [${c.status}]`),
          el("p", c.diff_summary, "trace-muted"),
          el("pre", (c.proposed_markdown || "").slice(0, 600))
        );
        if (c.status === "pending") {
          const acts = el("div", null, "row-actions");
          const bOk = el("button", "Phê duyệt & Publish", "primary");
          bOk.type = "button";
          bOk.addEventListener("click", async () => {
            await apiFetch(`/api/v32/candidates/${encodeURIComponent(c.id)}/review`, { method: "POST", body: JSON.stringify({ decision: "approve", note: "Approved via Dashboard" }) });
            toast("Đã duyệt và publish Skill mới!");
            loadCandidates();
            loadSkills();
          });
          const bRej = el("button", "Từ chối", "secondary");
          bRej.type = "button";
          bRej.addEventListener("click", async () => {
            await apiFetch(`/api/v32/candidates/${encodeURIComponent(c.id)}/review`, { method: "POST", body: JSON.stringify({ decision: "reject", note: "Rejected via Dashboard" }) });
            toast("Đã từ chối đề xuất.");
            loadCandidates();
          });
          acts.append(bOk, bRej);
          card.append(acts);
        }
        box.append(card);
      }
    } catch (e) {
      box.textContent = "Lỗi tải Candidates: " + e.message;
    }
  }

  async function loadSkillRuns() {
    const pid = $("sk32-res-proj")?.value || $("sk32-cand-proj")?.value;
    const box = $("sk32-runs-list");
    if (!pid || !box) return;
    box.textContent = "Đang tải Skill Telemetry...";
    try {
      const res = await apiFetch(`/api/v32/runs?project_id=${encodeURIComponent(pid)}&limit=30`);
      const items = res.items || [];
      box.replaceChildren();
      if (!items.length) {
        box.append(el("p", "Chưa có lượt phân giải/nạp Skill nào cho dự án này.", "trace-muted"));
        return;
      }
      const tbl = el("table", null, "trace-table");
      const hr = el("tr");
      for (const h of ["Thời gian (VN)", "Version / Hash", "Trạng thái (Telemetry)", "Adapter", "Lý do chọn", "Bytes / Token"]) hr.append(el("th", h));
      tbl.append(hr);
      for (const r of items) {
        const tr = el("tr");
        const tokLabel = r.input_tokens != null ? `${r.input_tokens} (${r.measurement_kind})` : `Unknown (${r.measurement_kind})`;
        for (const v of [
          vnTime(r.created_at),
          `v${r.version_str} (${(r.content_hash || "").slice(0, 8)})`,
          r.status,
          r.adapter,
          r.selection_reason,
          `${r.bytes_count} B · ${tokLabel}`,
        ]) tr.append(el("td", v));
        tbl.append(tr);
      }
      box.append(tbl);
    } catch (e) {
      box.textContent = "Lỗi tải Skill Telemetry: " + e.message;
    }
  }

  function renderResolveResult(res) {
    const box = $("sk32-resolve-out");
    if (!box) return;
    box.replaceChildren();
    const stCls = res.status === "ready" ? "badge ok" : res.status === "blocked" ? "badge err" : "badge warn";
    box.append(
      el("span", `Trạng thái: ${res.status} · Budget: ${res.used_chars}/${res.max_chars} chars · Đã chọn: ${(res.selected_skills || []).length}`, stCls)
    );
    if ((res.blockers || []).length) {
      const ul = el("ul");
      for (const b of res.blockers) ul.append(el("li", `BLOCKER: ${b}`));
      box.append(ul);
    }
    if ((res.degraded_notes || []).length) {
      const ul = el("ul");
      for (const d of res.degraded_notes) ul.append(el("li", `Cảnh báo MCP tùy chọn: ${d}`));
      box.append(ul);
    }
    if ((res.selected_skills || []).length) {
      const tbl = el("table", null, "trace-table");
      const hr = el("tr");
      for (const h of ["Skill", "Version / Hash", "Nguồn kế thừa", "Lý do chọn", "Bắt buộc"]) hr.append(el("th", h));
      tbl.append(hr);
      for (const s of res.selected_skills) {
        const tr = el("tr");
        for (const v of [
          `${s.namespace}/${s.slug}`,
          `v${s.version} (${(s.content_hash || "").slice(0, 8)})`,
          s.source_scope,
          s.selection_reason,
          s.mandatory ? "MANDATORY" : s.required ? "Required" : "Optional",
        ]) tr.append(el("td", v));
        tbl.append(tr);
      }
      box.append(tbl);
    } else {
      box.append(el("p", "Không có Skill nào được kích hoạt cho phạm vi và capability này.", "trace-muted"));
    }
  }

  window.addEventListener("hub-ready", () => {
    $("sk32-refresh")?.addEventListener("click", () => { loadSkills(); loadCandidates(); loadSkillRuns(); });
    $("sk32-filter-q")?.addEventListener("keydown", e => { if (e.key === "Enter") loadSkills(); });
    $("sk32-filter-life")?.addEventListener("change", loadSkills);
    $("sk32-cand-proj")?.addEventListener("change", loadCandidates);
    $("sk32-res-proj")?.addEventListener("change", loadSkillRuns);

    $("sk32-import-btn")?.addEventListener("click", async () => {
      const raw = prompt("Dán JSON SKILL import (gồm slug, title, source, license, manifest, markdown_body):");
      if (!raw) return;
      try {
        const parsed = JSON.parse(raw);
        const prev = await apiFetch("/api/v32/skills/import", { method: "POST", body: JSON.stringify({ ...parsed, preview_only: true, approved: false }) });
        if (!confirm(`Import Preview OK (hash=${prev.content_hash}, bytes=${prev.bytes}, license=${prev.license}). Xác nhận phê duyệt Import?`)) return;
        await apiFetch("/api/v32/skills/import", { method: "POST", body: JSON.stringify({ ...parsed, preview_only: false, approved: true }) });
        toast("Đã import Skill Draft thành công.");
        loadSkills();
      } catch (e) { toast("Lỗi Import Skill: " + e.message); }
    });

    $("sk32-create-btn")?.addEventListener("click", async () => {
      const slug = ($("sk32-new-slug")?.value || "").trim();
      const title = ($("sk32-new-title")?.value || "").trim();
      const body = ($("sk32-new-body")?.value || "").trim();
      if (!slug || !title || body.length < 10) { toast("Nhập đủ Slug, Tiêu đề và Nội dung SKILL.md (>=10 ký tự)."); return; }
      const tags = ($("sk32-new-tags")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
      const caps = ($("sk32-new-caps")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
      try {
        await apiFetch("/api/v32/skills", {
          method: "POST",
          body: JSON.stringify({
            namespace: ($("sk32-new-ns")?.value || "global").trim(),
            slug,
            title,
            description: title,
            manifest: { name: title, description: title, version: "1.0.0", tags, applicable_capabilities: caps },
            markdown_body: body,
          }),
        });
        $("sk32-new-slug").value = "";
        $("sk32-new-title").value = "";
        $("sk32-new-body").value = "";
        toast("Đã tạo Skill Draft mới (mở Chi tiết để Publish).");
        loadSkills();
      } catch (e) { toast("Lỗi tạo Skill: " + e.message); }
    });

    $("sk32-resolve-btn")?.addEventListener("click", async () => {
      const pid = $("sk32-res-proj")?.value;
      if (!pid) { toast("Chọn dự án trước."); return; }
      const caps = ($("sk32-res-caps")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
      const role = ($("sk32-res-role")?.value || "").trim() || null;
      try {
        const res = await apiFetch("/api/v32/resolve", {
          method: "POST",
          body: JSON.stringify({ project_id: pid, agent_role: role, required_capabilities: caps, record_run: false }),
        });
        renderResolveResult(res);
      } catch (e) {
        $("sk32-resolve-out").textContent = "Lỗi phân giải Skill: " + e.message;
      }
    });

    const syncSkillProjects = async () => {
      let projs = [];
      try {
        const r = await apiFetch("/api/projects");
        projs = r.items || [];
      } catch (_) {
        projs = [];
      }
      for (const id of ["sk32-res-proj", "sk32-cand-proj"]) {
        const sel = $(id);
        if (sel && projs.length) {
          const cur = sel.value;
          sel.replaceChildren(...projs.map(p => new Option(p.name, p.project_id)));
          if (cur && projs.some(p => p.project_id === cur)) sel.value = cur;
        }
      }
    };

    window.addEventListener("hub-tab", async e => {
      if (e.detail === "skills") {
        await syncSkillProjects();
        loadSkills();
        loadCandidates();
        loadSkillRuns();
      }
    });
  });
})();
