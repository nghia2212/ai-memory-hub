/* V3.2 (SK32) Dashboard UI: Kho Skill & Quy tắc (Centralized Skill Management) + P6A Scope Cascading Helpers.
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

      // Safe markdown preview (strictly textContent, zero HTML execution)
      const latest = (d.versions || [])[0];
      if (latest) {
        body.append(el("h4", `Nội dung SKILL.md (v${latest.version} — Safe Preview)`), el("pre", latest.markdown_body));
      }

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

  window.addEventListener("hub-ready", () => {
    $("sk32-refresh")?.addEventListener("click", () => { loadSkills(); loadCandidates(); });
    $("sk32-filter-q")?.addEventListener("keydown", e => { if (e.key === "Enter") loadSkills(); });
    $("sk32-filter-life")?.addEventListener("change", loadSkills);
    $("sk32-cand-proj")?.addEventListener("change", loadCandidates);

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
        $("sk32-resolve-out").replaceChildren(el("pre", JSON.stringify(res, null, 2)));
      } catch (e) {
        $("sk32-resolve-out").textContent = "Lỗi phân giải Skill: " + e.message;
      }
    });

    // Populate project selectors inside Skills tab when projects load
    const syncSkillProjects = () => {
      const projs = window.projectsCache || [];
      for (const id of ["sk32-res-proj", "sk32-cand-proj"]) {
        const sel = $(id);
        if (sel && projs.length) {
          const cur = sel.value;
          sel.replaceChildren(...projs.map(p => new Option(p.name, p.project_id)));
          if (cur) sel.value = cur;
        }
      }
    };

    window.addEventListener("hub-tab", e => {
      if (e.detail === "skills") {
        syncSkillProjects();
        loadSkills();
        loadCandidates();
      }
    });
  });
})();
