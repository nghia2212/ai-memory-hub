"""Self-contained Vietnamese Dashboard HTML/CSS/JS (zero external CDNs, zero secrets)."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Memory Hub — Bảng điều khiển quản trị</title>
  <style>
    :root {
      --bg: #0f172a;
      --surface: #1e293b;
      --surface-2: #273549;
      --border: #334155;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #22c55e;
      --warn: #f59e0b;
      --err: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }
    header h1 { margin: 0; font-size: 1.2rem; color: var(--accent); }
    .badge {
      display: inline-block;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      background: var(--surface-2);
      border: 1px solid var(--border);
    }
    .badge.ok { color: var(--ok); border-color: var(--ok); }
    .badge.warn { color: var(--warn); border-color: var(--warn); }
    .badge.err { color: var(--err); border-color: var(--err); }
    nav {
      display: flex;
      gap: 8px;
      padding: 12px 24px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
    }
    nav button {
      background: transparent;
      color: var(--muted);
      border: 1px solid transparent;
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.9rem;
    }
    nav button.active, nav button:hover {
      background: var(--surface-2);
      color: var(--text);
      border-color: var(--border);
    }
    main { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
    .panel { display: none; }
    .panel.active { display: block; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 20px; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
    }
    .card h2, .card h3 { margin-top: 0; font-size: 1rem; color: var(--accent); }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin: 8px 0 4px; }
    input, select, textarea {
      width: 100%;
      padding: 8px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      font-size: 0.9rem;
    }
    button.primary {
      background: var(--accent);
      color: #0f172a;
      border: none;
      padding: 8px 14px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 10px;
    }
    button.secondary {
      background: var(--surface-2);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
    }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: 10px; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    pre {
      background: var(--bg);
      border: 1px solid var(--border);
      padding: 10px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 0.82rem;
    }
    .row-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    #toast {
      position: fixed; bottom: 20px; right: 20px;
      background: var(--surface-2); border: 1px solid var(--accent);
      padding: 10px 16px; border-radius: 8px; display: none; z-index: 100;
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AI Memory Hub — Quản trị V1</h1>
      <small style="color:var(--muted)">Chế độ mặc định: Chỉ đọc (Read-only). Thao tác ghi cần đăng nhập quản trị.</small>
    </div>
    <div class="row-actions">
      <span id="auth-status" class="badge warn">Chưa đăng nhập</span>
      <input id="login-password" type="password" placeholder="Mật khẩu quản trị" style="width:180px" />
      <button class="secondary" id="btn-login" onclick="doLogin()">Đăng nhập</button>
      <button class="secondary" id="btn-logout" onclick="doLogout()" style="display:none">Đăng xuất</button>
    </div>
  </header>

  <nav>
    <button class="active" onclick="showTab('overview', this)">Tổng quan</button>
    <button onclick="showTab('projects', this)">Dự án & Worktree</button>
    <button onclick="showTab('code', this)">Tìm mã nguồn</button>
    <button onclick="showTab('memory', this)">Duyệt bộ nhớ</button>
    <button onclick="showTab('handoffs', this)">Lịch sử & Handoff</button>
    <button onclick="showTab('events', this)">Sự kiện & Kinh nghiệm</button>
    <button onclick="showTab('hermes', this)">Cấu hình Hermes</button>
    <button onclick="showTab('backup', this)">Sao lưu & Khôi phục</button>
    <button onclick="showTab('metrics', this)">Thống kê & Chi phí</button>
  </nav>

  <main>
    <!-- 1. Tổng quan -->
    <section id="tab-overview" class="panel active">
      <div class="grid" id="health-cards">
        <div class="card"><h3>Memory Bridge</h3><p>Đang kiểm tra...</p></div>
        <div class="card"><h3>Codebase Adapter</h3><p>Đang kiểm tra...</p></div>
        <div class="card"><h3>Hermes Worker</h3><p>Đang kiểm tra...</p></div>
      </div>
      <div class="card">
        <h2>Trạng thái tổng hợp</h2>
        <button class="secondary" onclick="loadOverview()">Làm mới trạng thái</button>
        <div id="overview-summary" style="margin-top:12px"></div>
      </div>
    </section>

    <!-- 2. Dự án & Worktree -->
    <section id="tab-projects" class="panel">
      <div class="grid">
        <div class="card">
          <h2>Đăng ký dự án mới</h2>
          <label>Tên dự án</label>
          <input id="proj-name" placeholder="Ví dụ: ai-memory-hub" />
          <label>Định danh chuẩn (Canonical Identity)</label>
          <input id="proj-identity" placeholder="git:github.com/org/repo" />
          <label><input id="proj-hermes" type="checkbox" style="width:auto" /> Cho phép Hermes xử lý sự kiện đã duyệt</label>
          <button class="primary" onclick="createProject()">Đăng ký dự án</button>
        </div>
        <div class="card">
          <h2>Đăng ký Worktree mới</h2>
          <label>Chọn dự án</label>
          <select id="wt-project-select"></select>
          <label>Đường dẫn gốc chuẩn (Canonical Root)</label>
          <input id="wt-root" placeholder="D:\\workspace\\my-repo" />
          <label>Nhánh (Branch)</label>
          <input id="wt-branch" placeholder="main" />
          <label>Commit HEAD</label>
          <input id="wt-head" placeholder="abc1234" />
          <button class="primary" onclick="createWorktree()">Đăng ký Worktree</button>
          <small style="color:var(--muted);display:block;margin-top:6px">Lưu ý: Worktree mới có trạng thái <code>pending_host_setup</code> cho tới khi host cấu hình mount read-only.</small>
        </div>
      </div>
      <div class="card">
        <h2>Danh sách dự án & Worktree</h2>
        <div id="projects-list"></div>
      </div>
    </section>

    <!-- 3. Tìm mã nguồn -->
    <section id="tab-code" class="panel">
      <div class="card">
        <h2>Tìm kiếm mã nguồn (Proxy tới Codebase Adapter)</h2>
        <div class="grid">
          <div><label>Dự án</label><select id="code-proj" onchange="syncWorktrees('code-proj','code-wt')"></select></div>
          <div><label>Worktree</label><select id="code-wt"></select></div>
        </div>
        <label>Truy vấn ký hiệu / mã nguồn</label>
        <input id="code-query" placeholder="Ví dụ: authenticate_token" />
        <div class="row-actions">
          <button class="primary" onclick="searchCode()">Tìm mã nguồn</button>
          <button class="secondary" onclick="triggerReindex()">Yêu cầu Reindex</button>
        </div>
        <pre id="code-results">Chưa có truy vấn.</pre>
      </div>
    </section>

    <!-- 4. Duyệt bộ nhớ -->
    <section id="tab-memory" class="panel">
      <div class="card">
        <h2>Tìm kiếm & Duyệt bộ nhớ (FTS5 Tiếng Việt)</h2>
        <div class="grid">
          <div><label>Dự án</label><select id="mem-proj" onchange="syncWorktrees('mem-proj','mem-wt'); loadMemories()"></select></div>
          <div><label>Worktree</label><select id="mem-wt"></select></div>
          <div>
            <label>Trạng thái lọc</label>
            <select id="mem-state" onchange="loadMemories()">
              <option value="">Tất cả</option>
              <option value="candidate">Chờ duyệt (candidate)</option>
              <option value="verified">Đã xác minh (verified)</option>
              <option value="superseded">Đã thay thế (superseded)</option>
            </select>
          </div>
        </div>
        <label>Từ khóa tìm kiếm tiếng Việt (FTS5)</label>
        <div class="row-actions">
          <input id="mem-query" placeholder="Ví dụ: lỗi kết nối cơ sở dữ liệu" style="flex:1" />
          <button class="primary" style="margin-top:0" onclick="searchMemory()">Tìm kiếm</button>
          <button class="secondary" onclick="loadMemories()">Tải danh sách</button>
        </div>
        <div id="memory-list"></div>
      </div>
    </section>

    <!-- 5. Lịch sử & Handoff -->
    <section id="tab-handoffs" class="panel">
      <div class="card">
        <h2>Tra cứu Handoff theo Task ID</h2>
        <div class="grid">
          <div><label>Dự án</label><select id="ho-proj" onchange="syncWorktrees('ho-proj','ho-wt')"></select></div>
          <div><label>Worktree</label><select id="ho-wt"></select></div>
          <div><label>Task ID</label><input id="ho-task" placeholder="Ví dụ: AIH-04" /></div>
        </div>
        <button class="primary" onclick="loadHandoff()">Tra cứu Handoff</button>
        <pre id="handoff-results">Nhập Task ID để xem lịch sử bàn giao.</pre>
      </div>
    </section>

    <!-- 6. Sự kiện & Kinh nghiệm -->
    <section id="tab-events" class="panel">
      <div class="card" style="margin-bottom:16px">
        <h2>AI Agents kết nối (Connected AI Agents)</h2>
        <div id="agents-status-list">Chọn dự án để xem trạng thái Claude / Gemini / Codex / Hermes.</div>
      </div>
      <div class="card">
        <h2>Sự kiện, Lỗi & Kinh nghiệm</h2>
        <div class="row-actions">
          <select id="ev-proj" style="max-width:320px" onchange="loadEvents()"></select>
          <button class="secondary" onclick="loadEvents()">Tải sự kiện</button>
        </div>
        <div id="events-list"></div>
      </div>
    </section>

    <!-- 7. Cấu hình Hermes -->
    <section id="tab-hermes" class="panel">
      <div class="card">
        <h2>Trạng thái cấu hình Hermes (Opt-in theo dự án)</h2>
        <p style="color:var(--muted)">Mặc định từ chối gửi dữ liệu ra ngoài. Chỉ các dự án bật <code>hermes_approved</code> và sự kiện đã duyệt/che bí mật mới vào hàng đợi worker.</p>
        <div id="hermes-status"></div>
      </div>
    </section>

    <!-- 8. Sao lưu & Khôi phục -->
    <section id="tab-backup" class="panel">
      <div class="card">
        <h2>Sao lưu & Khôi phục SQLite</h2>
        <button class="primary" onclick="createBackup()">Tạo bản sao lưu mới</button>
        <div id="backup-list"></div>
      </div>
    </section>

    <!-- 9. Thống kê & Chi phí -->
    <section id="tab-metrics" class="panel">
      <div class="card">
        <h2>Thống kê hiệu năng & Token thực tế</h2>
        <button class="secondary" onclick="loadMetrics()">Làm mới số liệu</button>
        <div id="metrics-view" style="margin-top:12px"></div>
      </div>
    </section>
  </main>

  <div id="toast"></div>

  <script>
    let csrfToken = null;
    let projectsCache = [];
    let worktreesByProject = {};

    function toast(msg) {
      const el = document.getElementById("toast");
      el.textContent = msg;
      el.style.display = "block";
      setTimeout(() => { el.style.display = "none"; }, 3500);
    }

    function showTab(name, btn) {
      document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
      document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
      document.getElementById("tab-" + name).classList.add("active");
      if (btn) btn.classList.add("active");
    }

    async function apiFetch(url, opts = {}) {
      const headers = Object.assign({}, opts.headers || {});
      if (csrfToken && (opts.method || "GET").toUpperCase() !== "GET") {
        headers["X-CSRF-Token"] = csrfToken;
      }
      if (opts.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
      const res = await fetch(url, Object.assign({}, opts, { headers, credentials: "same-origin" }));
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data?.error?.message || data?.detail?.error?.message || ("Lỗi HTTP " + res.status);
        throw new Error(msg);
      }
      return data;
    }

    function updateAuthUI(authenticated, token) {
      csrfToken = token || null;
      const badge = document.getElementById("auth-status");
      const pw = document.getElementById("login-password");
      const btnIn = document.getElementById("btn-login");
      const btnOut = document.getElementById("btn-logout");
      if (authenticated) {
        badge.textContent = "Đã đăng nhập (Quản trị)";
        badge.className = "badge ok";
        pw.style.display = "none";
        btnIn.style.display = "none";
        btnOut.style.display = "inline-block";
      } else {
        badge.textContent = "Chưa đăng nhập (Chỉ đọc)";
        badge.className = "badge warn";
        pw.style.display = "inline-block";
        btnIn.style.display = "inline-block";
        btnOut.style.display = "none";
      }
    }

    async function checkSession() {
      try {
        const data = await apiFetch("/api/auth/session");
        updateAuthUI(data.authenticated, data.csrf_token);
      } catch {
        updateAuthUI(false, null);
      }
    }

    async function doLogin() {
      const password = document.getElementById("login-password").value;
      try {
        const data = await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
        document.getElementById("login-password").value = "";
        updateAuthUI(true, data.csrf_token);
        toast("Đăng nhập thành công");
      } catch (e) {
        toast("Đăng nhập thất bại: " + e.message);
      }
    }

    async function doLogout() {
      try {
        await apiFetch("/api/auth/logout", { method: "POST" });
        updateAuthUI(false, null);
        toast("Đã đăng xuất");
      } catch (e) {
        toast(e.message);
      }
    }

    async function loadOverview() {
      try {
        const status = await apiFetch("/api/status");
        const cards = document.getElementById("health-cards");
        const renderCard = (title, s) => {
          const ok = s && s.ready;
          const cls = ok ? "ok" : "err";
          const txt = ok ? "Sẵn sàng" : (s?.status || "Không khả dụng");
          return `<div class="card"><h3>${title}</h3><span class="badge ${cls}">${txt}</span></div>`;
        };
        cards.innerHTML =
          renderCard("Memory Bridge", status.bridge) +
          renderCard("Codebase Adapter", status.codebase) +
          renderCard("Hermes Worker", status.hermes);
        document.getElementById("overview-summary").innerHTML = `<pre>${JSON.stringify(status, null, 2)}</pre>`;
      } catch (e) {
        toast("Lỗi tải trạng thái: " + e.message);
      }
    }

    async function loadProjects() {
      try {
        const data = await apiFetch("/api/projects");
        projectsCache = data.items || [];
        const selects = ["wt-project-select", "code-proj", "mem-proj", "ho-proj", "ev-proj"];
        for (const sid of selects) {
          const sel = document.getElementById(sid);
          const prev = sel.value;
          sel.innerHTML = projectsCache.map(p => `<option value="${p.project_id}">${p.name} (${p.project_id.slice(0,8)})</option>`).join("");
          if (prev && projectsCache.some(p => p.project_id === prev)) sel.value = prev;
        }
        let html = "<table><tr><th>Tên</th><th>Định danh</th><th>Hermes</th><th>Worktrees</th></tr>";
        let hermesHtml = "<table><tr><th>Dự án</th><th>Trạng thái Hermes</th></tr>";
        for (const p of projectsCache) {
          const wtData = await apiFetch(`/api/projects/${p.project_id}/worktrees`);
          worktreesByProject[p.project_id] = wtData.items || [];
          const wtText = (wtData.items || []).map(w => `${w.branch || "detached"} [${w.mount_status}]`).join("<br/>") || "Chưa có";
          html += `<tr><td>${p.name}</td><td><code>${p.canonical_identity}</code></td><td>${p.hermes_approved ? "Đã bật" : "Tắt"}</td><td>${wtText}</td></tr>`;
          hermesHtml += `<tr><td>${p.name}</td><td><span class="badge ${p.hermes_approved ? "ok" : "warn"}">${p.hermes_approved ? "Đã phê duyệt (Opt-in)" : "Từ chối mặc định"}</span></td></tr>`;
        }
        html += "</table>";
        hermesHtml += "</table>";
        document.getElementById("projects-list").innerHTML = html;
        document.getElementById("hermes-status").innerHTML = hermesHtml;
        syncWorktrees("code-proj", "code-wt");
        syncWorktrees("mem-proj", "mem-wt");
        syncWorktrees("ho-proj", "ho-wt");
      } catch (e) {
        toast("Lỗi tải dự án: " + e.message);
      }
    }

    function syncWorktrees(projSelId, wtSelId) {
      const pid = document.getElementById(projSelId).value;
      const wts = worktreesByProject[pid] || [];
      document.getElementById(wtSelId).innerHTML = wts.map(w => `<option value="${w.worktree_id}">${w.branch || "detached"} (${w.worktree_id.slice(0,8)})</option>`).join("");
    }

    async function createProject() {
      try {
        await apiFetch("/api/projects", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("proj-name").value,
            canonical_identity: document.getElementById("proj-identity").value,
            hermes_approved: document.getElementById("proj-hermes").checked
          })
        });
        toast("Đã đăng ký dự án");
        await loadProjects();
      } catch (e) { toast(e.message); }
    }

    async function createWorktree() {
      try {
        const pid = document.getElementById("wt-project-select").value;
        await apiFetch(`/api/projects/${pid}/worktrees`, {
          method: "POST",
          body: JSON.stringify({
            canonical_root: document.getElementById("wt-root").value,
            branch: document.getElementById("wt-branch").value || null,
            head_commit: document.getElementById("wt-head").value || null
          })
        });
        toast("Đã đăng ký worktree (pending_host_setup)");
        await loadProjects();
      } catch (e) { toast(e.message); }
    }

    async function searchCode() {
      try {
        const data = await apiFetch("/api/search/code", {
          method: "POST",
          body: JSON.stringify({
            project_id: document.getElementById("code-proj").value,
            worktree_id: document.getElementById("code-wt").value,
            query: document.getElementById("code-query").value
          })
        });
        document.getElementById("code-results").textContent = JSON.stringify(data, null, 2);
      } catch (e) {
        document.getElementById("code-results").textContent = "Lỗi: " + e.message;
      }
    }

    async function triggerReindex() {
      try {
        const data = await apiFetch("/api/index", {
          method: "POST",
          body: JSON.stringify({
            project_id: document.getElementById("code-proj").value,
            worktree_id: document.getElementById("code-wt").value
          })
        });
        toast("Đã gửi yêu cầu reindex: " + data.status);
      } catch (e) { toast(e.message); }
    }

    async function loadMemories() {
      const pid = document.getElementById("mem-proj").value;
      if (!pid) return;
      const state = document.getElementById("mem-state").value;
      try {
        const data = await apiFetch(`/api/memories?project_id=${encodeURIComponent(pid)}&state=${encodeURIComponent(state)}`);
        const rows = (data.items || []).map(m => `
          <tr>
            <td><strong>${m.title}</strong><br/><small>${m.body.slice(0,160)}</small></td>
            <td>${m.type}</td>
            <td><span class="badge ${m.state === "verified" ? "ok" : "warn"}">${m.state}</span></td>
            <td>${m.source_agent}</td>
            <td>${m.state === "candidate" ? `<button class="secondary" onclick="verifyMemory('${m.id}')">Phê duyệt</button>` : ""}</td>
          </tr>
        `).join("");
        document.getElementById("memory-list").innerHTML = `<table><tr><th>Nội dung</th><th>Loại</th><th>Trạng thái</th><th>Nguồn</th><th>Hành động</th></tr>${rows}</table>`;
      } catch (e) { toast(e.message); }
    }

    async function searchMemory() {
      try {
        const data = await apiFetch("/api/search/memory", {
          method: "POST",
          body: JSON.stringify({
            project_id: document.getElementById("mem-proj").value,
            worktree_id: document.getElementById("mem-wt").value,
            query: document.getElementById("mem-query").value
          })
        });
        document.getElementById("memory-list").innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
      } catch (e) { toast(e.message); }
    }

    async function verifyMemory(id) {
      try {
        await apiFetch(`/api/memories/${id}/verify`, { method: "POST", body: JSON.stringify({ action: "verify" }) });
        toast("Đã xác minh bộ nhớ");
        await loadMemories();
      } catch (e) { toast(e.message); }
    }

    async function loadHandoff() {
      try {
        const pid = document.getElementById("ho-proj").value;
        const wid = document.getElementById("ho-wt").value;
        const tid = document.getElementById("ho-task").value;
        const data = await apiFetch(`/api/handoffs/${encodeURIComponent(tid)}?project_id=${encodeURIComponent(pid)}&worktree_id=${encodeURIComponent(wid)}`);
        document.getElementById("handoff-results").textContent = JSON.stringify(data, null, 2);
      } catch (e) {
        document.getElementById("handoff-results").textContent = "Lỗi: " + e.message;
      }
    }

    async function loadEvents() {
      const pid = document.getElementById("ev-proj").value;
      if (!pid) return;
      try {
        const [data, agents] = await Promise.all([
          apiFetch(`/api/events?project_id=${encodeURIComponent(pid)}`),
          apiFetch(`/api/agents/status?project_id=${encodeURIComponent(pid)}`)
        ]);
        const agentRows = (agents.items || []).map(a => `
          <tr><td><strong>${a.source_agent}</strong></td><td>${a.last_kind}</td><td>${a.task_id}</td><td>${a.branch || "-"} (${(a.head_commit || "").slice(0,7)})</td><td>${a.last_seen_at}</td></tr>
        `).join("");
        document.getElementById("agents-status-list").innerHTML = agentRows
          ? `<table><tr><th>Agent</th><th>Sự kiện cuối</th><th>Task</th><th>Nhánh / Commit</th><th>Cập nhật</th></tr>${agentRows}</table>`
          : `<p style="color:var(--muted)">Chưa có sự kiện agent nào cho dự án này.</p>`;
        const rows = (data.items || []).map(ev => `
          <tr><td>${ev.task_id}</td><td>${ev.kind}</td><td>${ev.approved ? "Đã duyệt" : "Chờ"}</td><td><code>${JSON.stringify(ev.payload)}</code></td></tr>
        `).join("");
        document.getElementById("events-list").innerHTML = `<table><tr><th>Task</th><th>Loại</th><th>Trạng thái</th><th>Payload (đã che bí mật)</th></tr>${rows}</table>`;
      } catch (e) { toast(e.message); }
    }

    async function loadBackups() {
      try {
        const data = await apiFetch("/api/backups");
        const rows = (data.items || []).map(b => `
          <tr><td><code>${b.backup_id}</code></td><td>${b.schema_version}</td><td>${b.size_bytes} B</td><td>${b.created_at}</td>
          <td><button class="secondary" onclick="restoreBackup('${b.backup_id}')">Khôi phục</button></td></tr>
        `).join("");
        document.getElementById("backup-list").innerHTML = `<table><tr><th>Backup ID</th><th>Schema</th><th>Dung lượng</th><th>Thời gian</th><th>Hành động</th></tr>${rows}</table>`;
      } catch (e) { toast(e.message); }
    }

    async function createBackup() {
      try {
        const data = await apiFetch("/api/backup", { method: "POST" });
        toast("Đã tạo backup: " + data.backup_id);
        await loadBackups();
      } catch (e) { toast(e.message); }
    }

    async function restoreBackup(id) {
      try {
        await apiFetch("/api/restore", { method: "POST", body: JSON.stringify({ backup_id: id }) });
        toast("Đã khôi phục thành công từ " + id);
      } catch (e) { toast("Lỗi khôi phục: " + e.message); }
    }

    async function loadMetrics() {
      try {
        const m = await apiFetch("/api/metrics");
        const tokenBlock = m.measured_tokens_available
          ? `<pre>${JSON.stringify(m.token_usage, null, 2)}</pre>`
          : `<p class="badge warn">Chưa có dữ liệu token/chi phí đo thực tế (không ước lượng giả).</p>`;
        document.getElementById("metrics-view").innerHTML = `
          <p><strong>Tổng lượt truy vấn:</strong> ${m.query_count} | <strong>Độ trễ TB:</strong> ${m.avg_latency_ms} ms | <strong>Kích thước TB:</strong> ${m.avg_result_chars} ký tự</p>
          ${tokenBlock}
          <pre>${JSON.stringify(m.counts, null, 2)}</pre>
        `;
      } catch (e) { toast(e.message); }
    }

    window.addEventListener("DOMContentLoaded", async () => {
      await checkSession();
      await loadOverview();
      await loadProjects();
      await loadBackups();
      await loadMetrics();
    });
  </script>
</body>
</html>
"""
