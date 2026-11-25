/* V3.2.1 P16B — Persistent Task & Auto Recovery UI (Manual / Automatic / Hybrid).
 * Strictly XSS-safe: textContent & DOM nodes only, zero raw innerHTML. */
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

  const STATUS_VI = {
    pending_review: "Cần xác nhận đối soát",
    auto_resumable: "Sẵn sàng phục hồi an toàn",
    reconnected: "Đã kết nối lại Agent cũ",
    resumed: "Đã phục hồi (Attempt mới)",
    paused: "Đã tạm dừng",
    canceled: "Đã hủy",
    resolved: "Đã hoàn tất",
  };
  const RUNNER_VI = {
    alive: "Đang chạy (Alive)",
    stopped: "Đã dừng (Stopped)",
    unknown: "Chưa xác định",
    unreachable: "Host Runner ngoại tuyến",
  };

  function getSelectedProjectId() {
    return $("plans-proj")?.value || $("agents-proj")?.value || $("chat-proj-sel")?.value || "";
  }

  function renderBudget(b) {
    if (!b || !Object.keys(b).length) return "Thuê bao / 0 phí API";
    const parts = [];
    if (b.spent_usd != null) parts.push(`Đã dùng $${Number(b.spent_usd).toFixed(4)}`);
    if (b.reserved_usd) parts.push(`Đang giữ $${Number(b.reserved_usd).toFixed(4)}`);
    if (b.uncertain_usd) parts.push(`Chưa rõ $${Number(b.uncertain_usd).toFixed(4)}`);
    if (b.remaining_usd != null) parts.push(`Còn $${Number(b.remaining_usd).toFixed(4)}`);
    return parts.join(" · ") || "—";
  }

  function renderGit(g) {
    if (!g || !Object.keys(g).length) return "Chưa có snapshot Git";
    const br = g.branch || "—";
    const hd = g.head_commit ? String(g.head_commit).slice(0, 8) : "—";
    const dirty = g.dirty ? `Có thay đổi chưa commit (${(g.dirty_files || []).length} tệp)` : "Sạch";
    return `${br} @ ${hd} · ${dirty}`;
  }

  async function runAction(item, action, bodyExtra = {}, query = "") {
    const pid = item.project_id || getSelectedProjectId();
    try {
      const res = await apiFetch(`/api/v321/recovery/${encodeURIComponent(item.id)}/${action}${query}`, {
        method: "POST",
        body: JSON.stringify({ project_id: pid, ...bodyExtra }),
      });
      if (action === "review") {
        renderReviewDetail(res);
      } else {
        toast(`Thao tác '${action}' thành công.`);
        await loadRecoveryQueue();
      }
    } catch (e) {
      toast(`Không thể thực hiện '${action}': ${e.message}`);
    }
  }

  function renderReviewDetail(res) {
    const box = $("recovery-review-detail");
    if (!box) return;
    box.replaceChildren();
    const card = el("div", null, "card");
    const it = res.item || {};
    card.append(el("strong", `Đối soát phục hồi: ${it.entity_kind} / ${it.entity_id} (Fencing #${it.fencing_token})`));
    card.append(el("p", it.interruption_reason || "", "trace-muted"));
    if (it.safety_classification === "result_undelivered" && it.recovery_status !== "resolved") {
      const bDel = el("button", "Giao lại kết quả AI đã lưu (0 LLM, 0 phí)", "primary");
      bDel.type = "button";
      bDel.addEventListener("click", () => runAction(it, "review", { note: "Giao lại kết quả từ UI" }, "?deliver_cached_result=true"));
      card.append(bDel);
    }
    if ((res.checkpoints || []).length) {
      card.append(el("p", `Checkpoints (${res.checkpoints.length}): v${res.checkpoints[0].checkpoint_version} lúc ${vnTime(res.checkpoints[0].created_at)}`));
    }
    if ((res.attempts || []).length) {
      card.append(el("p", `Lịch sử Attempt: ${res.attempts.map(a => `#${a.attempt_number}:${a.status}`).join(" → ")}`));
    }
    if ((res.provider_requests || []).length) {
      card.append(el("p", `Provider Requests (${res.provider_requests.length}): ${res.provider_requests.map(r => `${r.provider_key}/${r.status}($${r.reserved_cost_usd})`).join(", ")}`));
    }
    box.append(card);
  }

  async function loadRecoveryQueue() {
    const tblBox = $("recovery-queue-table");
    const sumBox = $("recovery-queue-summary");
    if (!tblBox || !sumBox) return;
    const pid = getSelectedProjectId();
    try {
      const params = new URLSearchParams();
      if (pid) params.set("project_id", pid);
      const data = await apiFetch("/api/v321/recovery/queue?" + params);
      if (data.policy && $("rec-mode-sel")) {
        $("rec-mode-sel").value = data.policy.recovery_mode || "hybrid";
      }
      const items = data.items || [];
      sumBox.textContent = items.length
        ? `Phát hiện ${items.length} nhiệm vụ chưa hoàn thành / cần phục hồi (Chế độ hiện tại: ${data.policy?.recovery_mode || "hybrid"}).`
        : "Không có nhiệm vụ bị gián đoạn cần phục hồi trong phạm vi dự án hiện tại.";
      tblBox.replaceChildren();
      if (!items.length) return;

      const tbl = document.createElement("table");
      const head = document.createElement("tr");
      [
        "Dự án / Plan & Task",
        "Agent & Model",
        "Checkpoint & Bước",
        "Nguyên nhân & Trạng thái Agent cũ",
        "Mã nguồn Git & Ngân sách",
        "Trạng thái phục hồi",
        "Thao tác",
      ].forEach(h => head.append(el("th", h)));
      tbl.append(head);

      for (const it of items) {
        const tr = document.createElement("tr");
        const doneNames = (it.completed_steps || []).map(s => s.step || s.task_key || JSON.stringify(s)).join(", ") || "Chưa có";
        const pendNames = (it.pending_steps || []).map(s => s.step || s.task_key || JSON.stringify(s)).join(", ") || "—";

        tr.append(
          el("td", `${it.project_name || it.project_id.slice(0, 8)}\nPlan: ${(it.plan_id || "—").slice(0, 8)} · Task: ${it.entity_id.slice(0, 8)}`),
          el("td", `${it.agent_source || "—"} / ${it.model_id || "—"}`),
          el("td", `Ckpt: ${(it.latest_checkpoint_id || "—").slice(0, 8)} (fence #${it.fencing_token})\nXong: ${doneNames}\nCòn lại: ${pendNames}`),
          el("td", `${it.interruption_reason}\nAgent cũ: ${RUNNER_VI[it.old_agent_status] || it.old_agent_status}`),
          el("td", `Git: ${renderGit(it.git_state)}\nNgân sách: ${renderBudget(it.budget)}`),
          el("td", `${STATUS_VI[it.recovery_status] || it.recovery_status}\n(${it.safety_classification})`)
        );

        const tdAct = el("td", null, "row-actions");
        tdAct.style.flexWrap = "wrap";
        const a = it.actions_allowed || {};

        const mkBtn = (label, enabled, title, fn, cls = "secondary") => {
          const b = el("button", label, cls);
          b.type = "button";
          b.style.marginTop = "0";
          b.disabled = !enabled;
          if (title) b.title = title;
          b.addEventListener("click", fn);
          tdAct.append(b);
        };

        mkBtn("Resume", a.resume, a.resume ? "Phục hồi từ checkpoint bằng Attempt mới" : "Không thể Resume khi Agent cũ đang chạy hoặc hết quota", () => {
          const extra = {};
          if (it.safety_classification === "uncertain_api_inflight") {
            if (!window.confirm("Request API trước đó chưa rõ kết quả. Xác nhận bạn đã đối soát và cho phép thử lại?")) return;
            extra.confirm_uncertain_retry = true;
          }
          if (it.safety_classification === "dirty_worktree_edit") {
            if (!window.confirm("Worktree có thay đổi chưa commit. Xác nhận bạn đã kiểm tra diff và muốn tiếp tục?")) return;
            extra.confirm_dirty_worktree = true;
          }
          runAction(it, "resume", extra);
        }, "primary");

        mkBtn("Reconnect Agent", a.reconnect, a.reconnect ? "Kết nối lại Agent cũ đang chạy" : "Chỉ dùng khi Agent cũ vẫn còn sống", () => runAction(it, "reconnect"));
        mkBtn("Pause", a.pause, "Tạm dừng an toàn", () => runAction(it, "pause"));
        mkBtn("Cancel", a.cancel, "Hủy vĩnh viễn (không tự phục hồi)", () => {
          if (window.confirm("Hủy vĩnh viễn nhiệm vụ này? Sẽ không tự động phục hồi lại.")) runAction(it, "cancel");
        });
        mkBtn("Change Agent", a.change_agent, "Chuyển sang Agent/Model hợp lệ khác (không tự chuyển API trả phí)", () => {
          const src = window.prompt("Nhập Execution Source mới (ví dụ: antigravity, claude_code, codex_cli):", "antigravity");
          if (!src) return;
          const mid = window.prompt("Nhập Model ID mới (ví dụ: gemini-4-neon-medium):", it.model_id || "gemini-4-neon-medium");
          if (!mid) return;
          runAction(it, "change-agent", { execution_source: src.trim(), model_id: mid.trim(), allow_paid_api_consent: false });
        });
        mkBtn("Review Recovery", a.review, "Xem chi tiết đối soát checkpoint, attempt & ngân sách", () => runAction(it, "review"));

        tr.append(tdAct);
        tbl.append(tr);
      }
      tblBox.append(tbl);
    } catch (e) {
      sumBox.textContent = "Lỗi tải hàng đợi phục hồi: " + e.message;
    }
  }

  window.addEventListener("hub-ready", () => {
    $("rec-scan-btn")?.addEventListener("click", async () => {
      try {
        const pid = getSelectedProjectId();
        const res = await apiFetch("/api/v321/recovery/scan", {
          method: "POST",
          body: JSON.stringify({ project_id: pid || null, check_host_runner: true, apply_auto_policy: true }),
        });
        toast(`Đã quét phục hồi: phát hiện ${res.upserted} mục (${res.pending_review} cần đối soát).`);
        await loadRecoveryQueue();
      } catch (e) { toast("Lỗi quét phục hồi: " + e.message); }
    });
    $("rec-save-policy-btn")?.addEventListener("click", async () => {
      const pid = getSelectedProjectId();
      if (!pid) { toast("Chọn dự án trước khi lưu chế độ Recovery."); return; }
      try {
        await apiFetch(`/api/v321/projects/${encodeURIComponent(pid)}/recovery-policy`, {
          method: "PUT",
          body: JSON.stringify({ recovery_mode: $("rec-mode-sel")?.value || "hybrid" }),
        });
        toast("Đã lưu chế độ phục hồi cho dự án.");
        await loadRecoveryQueue();
      } catch (e) { toast("Lỗi lưu chế độ: " + e.message); }
    });
    $("plans-proj")?.addEventListener("change", loadRecoveryQueue);
    loadRecoveryQueue();
  });

  window.addEventListener("hub-tab", ev => {
    if (ev.detail === "plans" || ev.detail === "agents") loadRecoveryQueue();
  });
})();
