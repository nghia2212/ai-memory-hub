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
    .badge.muted { color: var(--muted); border-color: var(--border); }
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
  <link rel="stylesheet" href="/workbench.css">
  <script defer src="/workbench.js"></script>
  <script defer src="/execution-map.js"></script>
  <script defer src="/v3-ui.js"></script>
</head>
<body>
  <header>
    <div>
      <h1>AI Memory Hub <small>Workbench 2.1-dev</small></h1>
      <button class="secondary" id="nav-toggle" aria-expanded="true" aria-controls="navigation">☰ Danh mục</button>
    </div>
    <div class="row-actions">
      <span id="auth-status" class="badge warn">Chưa đăng nhập</span>
      <input id="login-password" type="password" placeholder="Mật khẩu quản trị" style="width:180px" />
      <button class="secondary" id="btn-login" onclick="doLogin()">Đăng nhập</button>
      <button class="secondary" id="btn-logout" onclick="doLogout()" style="display:none">Đăng xuất</button>
    </div>
  </header>

  <nav id="navigation" aria-label="Điều hướng">
    <button onclick="showTab('overview', this)" class="active">Tổng quan</button>
    <button onclick="showTab('plans', this)" >Giao công việc</button>
    <button onclick="showTab('agents', this)" >Agent Control Center</button>
    <button id="trace-nav" onclick="showTab('traces', this)">Bản đồ thực thi</button>
    <button id="conv-nav" onclick="showTab('conversations', this)">Lịch sử hội thoại AI</button>
    <button id="mcp-nav" onclick="showTab('mcp', this)">Dynamic MCP</button>
    <button onclick="showTab('code', this)" >Codebase Explorer</button>
    <button onclick="showTab('projects', this)" >Dự án & Worktree</button>
    <button onclick="showTab('memory', this)" >Bộ nhớ</button>
    <button onclick="showTab('sessions', this)" >Phiên & Dự báo</button>
    <button onclick="showTab('brain', this)" >Bộ não điều phối</button>
    <button onclick="showTab('catalog', this)" >Danh mục Model</button>
    <button onclick="showTab('metrics', this)" >Token & Chi phí</button>
    <button onclick="showTab('events', this)" >Nhật ký & Audit</button>
    <button onclick="showTab('backup', this)" >Sao lưu & Khôi phục</button>
    <button onclick="showTab('hermes', this)" >Cài đặt</button>
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
      <div class="explorer-layout"><aside class="card"><h3>Thư mục</h3><button id="tree-root" class="secondary">Mở thư mục gốc</button>
      <p id="tree-path"></p><div id="tree-files"></div></aside><article class="card"><h3 id="source-title">Mã nguồn</h3>
      <div id="index-meta"></div><div id="source-actions" class="row-actions"></div><pre id="source-text"></pre>
      <div id="symbol-list"></div><label>Symbol cần trace</label><input id="graph-symbol" maxlength="512"><label>Độ sâu</label><select id="graph-depth"><option>1</option><option>2</option><option>3</option></select><button id="graph-run" class="secondary">Trace / Impact</button><div id="call-graph"></div></article></div>
      <div class="card"><h3>So sánh worktree cùng dự án</h3><label>Worktree đối chiếu</label><select id="compare-wt"></select><button id="compare-run" class="secondary">So sánh index / file đang mở</button><div id="compare-result"></div></div>
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
    <section id="tab-events" class="panel"><button class="secondary" onclick="showTab('handoffs')">Lịch sử &amp; Handoff</button>
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
      <div class="card" style="margin-bottom:16px">
        <h2>Điều khiển Hermes Worker (Quyền quản trị)</h2>
        <p style="color:var(--muted)">Mặc định Hermes ở trạng thái <strong>Đã tắt</strong> để không phát sinh chi phí ngoài ý muốn. Việc bật Hermes <strong>không tự động kích hoạt DeepSeek API trả phí</strong>; cần có <code>DEEPSEEK_API_KEY</code> và xác nhận cấp phép riêng.</p>
        <div id="hermes-runtime-card" style="margin-bottom:12px">Đang tải cấu hình Hermes...</div>
        <div class="grid">
          <div>
            <label>Provider</label>
            <select id="hermes-provider">
              <option value="deepseek">deepseek (cần API key & cấp phép riêng)</option>
              <option value="mock">mock (giả lập nội bộ, 0 chi phí)</option>
              <option value="hermes_gateway">hermes_gateway (gateway nội bộ)</option>
            </select>
          </div>
          <div style="display:flex;flex-direction:column;justify-content:flex-end">
            <label><input id="hermes-allow-paid" type="checkbox" style="width:auto" /> Cấp phép dùng DeepSeek API trả phí (Tắt mặc định)</label>
          </div>
        </div>
        <div class="row-actions" style="margin-top:10px">
          <button class="primary" style="margin-top:0" onclick="toggleHermes(true)">Bật Hermes (Quản trị)</button>
          <button class="secondary" onclick="toggleHermes(false)">Tắt Hermes (Quản trị)</button>
          <button class="secondary" onclick="loadHermesPanel()">Làm mới</button>
        </div>
      </div>
      <div class="card">
        <h2>Trạng thái Opt-in Hermes theo dự án</h2>
        <p style="color:var(--muted)">Mặc định từ chối gửi dữ liệu ra ngoài. Chỉ các dự án bật <code>hermes_approved</code> và sự kiện đã duyệt/che bí mật mới vào hàng đợi worker.</p>
        <div id="hermes-status"></div>
      </div>
    </section>

    <!-- 7b. Bộ não điều phối (V2 Adaptive AI Brain) -->
    <section id="tab-brain" class="panel">
      <div id="neon-banner" class="card" style="display:none;margin-bottom:16px;border-color:var(--ok)"></div>
      <div class="card" style="margin-bottom:16px">
        <h2>Bộ não điều phối (Adaptive AI Brain — Cấu hình trực tiếp theo Project)</h2>
        <p style="color:var(--muted)">Chọn trực tiếp <strong>Provider/CLI, Model, Effort và thứ tự Fallback</strong> cho từng tầng ngay trên Dashboard (lưu theo Project, không cần sửa source/redeploy). Mặc định yêu cầu phê duyệt (<code>REVIEW_REQUIRED</code>), không tự động dùng API trả phí.</p>
        <div class="row-actions">
          <select id="brain-proj" style="max-width:320px" onchange="loadBrainPanel()"></select>
          <button class="secondary" onclick="loadBrainPanel()">Làm mới</button>
          <button class="secondary" onclick="syncCatalog()">Quét lại CLI &amp; Model (agy/claude/codex)</button>
          <button class="primary" style="margin-top:0" onclick="saveBrainPolicy()">Lưu cấu hình Bộ não (theo Project)</button>
        </div>
        <datalist id="catalog-model-options"></datalist>
        <div id="brain-roles-view" style="margin-top:12px">Chọn dự án để cấu hình phân cấp bộ não điều phối.</div>
        <pre id="brain-probe-output" style="display:none;margin-top:10px"></pre>
      </div>
      <div class="grid">
        <div class="card">
          <h3>Chính sách điều phối &amp; Ngân sách (Quản trị)</h3>
          <label>Chế độ phê duyệt</label>
          <select id="brain-approval-mode">
            <option value="REVIEW_REQUIRED">Phê duyệt thủ công (REVIEW_REQUIRED — Mặc định)</option>
            <option value="AUTO_SCOPED">Tự động có giới hạn (AUTO_SCOPED)</option>
          </select>
          <label>Ngân sách điều phối hàng ngày (USD, để trống nếu chỉ dùng CLI gói)</label>
          <input id="brain-planner-budget" type="number" step="0.01" min="0" placeholder="Ví dụ: 2.00" />
          <label><input id="brain-paid-fallback" type="checkbox" style="width:auto" /> Cho phép fallback sang API trả phí (DeepSeek — Tắt mặc định)</label>
          <button class="primary" onclick="saveBrainPolicy()">Lưu chính sách &amp; Bộ não</button>
        </div>
        <div class="card">
          <h3>Thống kê riêng Token Điều phối (Planner) vs Thực thi (Executor)</h3>
          <div id="brain-usage-view">Chưa có dữ liệu đo thực tế.</div>
        </div>
      </div>
    </section>

    <!-- 7b. Kế hoạch & Duyệt DAG (V2) -->
    <section id="tab-agents" class="panel"><h2>Agent Control Center</h2>
      <div class="card"><div class="grid"><label>Dự án<select id="agents-proj"></select></label>
      <label>Lọc model, trạng thái hoặc agent<input id="agent-filter" type="search"></label></div>
      <button class="secondary" id="agent-refresh">Cập nhật</button><p id="agent-counts"></p><div id="agent-rows"></div><div id="agent-detail"></div></div>
    </section>
    <section id="tab-plans" class="panel">
      <h2>Giao công việc</h2><div class="work-layout"><aside class="card"><h3>Lịch sử</h3>
      <input id="history-filter" type="search" placeholder="Tìm nhiệm vụ"><div id="plans-list"></div></aside>
      <article class="card"><h3>Kế hoạch & Duyệt DAG</h3><div id="task-detail">Chọn nhiệm vụ trong lịch sử.</div>
      <pre id="plan-dispatch-output" style="display:none"></pre></article>
      <aside class="card"><h3>Phiên thực thi</h3><div id="task-meta">Chưa chọn nhiệm vụ.</div></aside></div>
      <div class="card" style="margin-bottom:16px">
        <h2>Giao yêu cầu mới</h2>
        <div class="grid">
          <div><label>Dự án</label><select id="plans-proj" onchange="syncWorktrees('plans-proj','plans-wt'); loadPlans()"></select></div>
          <div><label>Worktree</label><select id="plans-wt" onchange="loadPlans()"></select></div>
          <div>
            <label>Độ phức tạp (để Tự động hoặc chọn)</label>
            <select id="plan-complexity">
              <option value="">Tự động phân loại</option>
              <option value="XS">XS (tier1_light)</option>
              <option value="S">S (tier1_light)</option>
              <option value="M">M (tier2_primary)</option>
              <option value="L">L (tier3_advanced)</option>
              <option value="XL">XL (tier3_advanced)</option>
            </select>
          </div>
        </div>
        <label>Prompt / Mục tiêu nhiệm vụ</label>
        <textarea id="plan-objective" rows="5" placeholder="Ví dụ: Kiểm tra nhanh trạng thái kiến trúc module nhân vật và tóm tắt rủi ro"></textarea>
        <div class="row-actions prompt-tools"><span id="plan-char-count" aria-live="polite">0 / 32.000</span><button type="button" class="secondary" id="plan-expand">Mở rộng soạn thảo</button><button type="button" class="secondary" id="plan-import">Nhập TXT / MD</button><input type="file" id="plan-file" accept=".txt,.md,.markdown" hidden><button type="button" class="secondary" id="plan-clear">Xóa bản nháp</button><span id="plan-draft-state" aria-live="polite"></span></div>
        <div class="row-actions" style="margin-top:8px">
          <button id="plan-submit" class="primary" style="margin-top:0" onclick="analyzeAndCreatePlan()">Phân tích &amp; Tạo kế hoạch (Chờ duyệt)</button>
          <button class="secondary" onclick="loadPlans()">Tải danh sách kế hoạch</button>
        </div>
      </div>
    </section>

    <!-- 7c. Phiên & Dự báo (V2 Session Analytics) -->
    <section id="tab-sessions" class="panel">
      <div class="card" style="margin-bottom:16px">
        <h2>Lịch sử Phiên & Thống kê Cohort (V2)</h2>
        <div class="grid">
          <div><label>Dự án</label><select id="sess-proj" onchange="loadSessions()"></select></div>
          <div><label>Độ phức tạp</label><select id="sess-comp" onchange="loadSessions()"><option value="">Tất cả</option><option>XS</option><option>S</option><option>M</option><option>L</option><option>XL</option></select></div>
          <div><label>Kết quả</label><select id="sess-outcome" onchange="loadSessions()"><option value="">Tất cả</option><option value="completed">completed</option><option value="failed">failed</option><option value="checkpointed">checkpointed</option><option value="partial">partial</option></select></div>
        </div>
        <button class="secondary" onclick="loadSessions()">Làm mới phiên & hiệu chuẩn</button>
        <div id="sessions-list" style="margin-top:12px"></div>
      </div>
      <div class="grid">
        <div class="card">
          <h3>Phân bố Cohort (P50 / P90 / Retry)</h3>
          <div id="cohorts-view">Chưa có dữ liệu cohort.</div>
        </div>
        <div class="card">
          <h3>Hiệu chuẩn Dự báo (MAE / WAPE / P90 Coverage)</h3>
          <div id="calibration-view">Chưa có dữ liệu hiệu chuẩn.</div>
        </div>
      </div>
    </section>

    <!-- 7d. Danh mục Model & Đánh giá (V2) -->
    <section id="tab-catalog" class="panel">
      <div class="card">
        <h2>Danh mục Model &amp; Trạng thái Xác minh (Catalog / CLI / Thực thi)</h2>
        <div class="row-actions">
          <button class="primary" style="margin-top:0" onclick="syncCatalog()">Quét lại CLI &amp; Cập nhật Danh mục</button>
          <button class="secondary" onclick="loadCatalog()">Làm mới danh mục</button>
        </div>
        <div id="catalog-alerts" style="margin-top:10px"></div>
        <div id="catalog-view" style="margin-top:12px"></div>
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
        <h2>Thống kê hiệu năng & Token thực tế</h2><label>Dự án</label><select id="metrics-proj"></select>
        <button class="secondary" onclick="loadMetrics()">Làm mới số liệu</button>
        <div id="metrics-view" style="margin-top:12px"></div>
      </div>
    </section>
    <section id="tab-traces" class="panel">
      <div class="trace-page-head">
        <h2>Bản đồ thực thi</h2>
        <div class="row-actions">
          <button id="trace-toggle-filters" type="button" class="secondary">Bộ lọc</button>
          <button id="trace-toggle-inspector" type="button" class="secondary">Token Telemetry Inspector</button>
          <button id="trace-export-json" type="button" class="secondary">Xuất JSON</button>
          <button id="trace-export-csv" type="button" class="secondary">Xuất CSV</button>
          <button id="trace-refresh" type="button">Tải trace</button>
        </div>
      </div>

      <div id="trace-filter-card" class="card trace-filter-card">
        <div class="trace-filters">
          <label>Dự án<select id="trace-proj"></select></label>
          <label>Worktree<select id="trace-wt"></select></label>
          <label>Plan ID<input id="trace-plan"></label>
          <label>Task ID<input id="trace-task"></label>
          <label>Session ID<input id="trace-session"></label>
          <label>Agent ID<input id="trace-agent"></label>
          <label>Model ID<input id="trace-model"></label>
          <label>Trạng thái<select id="trace-status"><option value="">Tất cả</option><option>running</option><option>succeeded</option><option>failed</option><option>blocked</option><option>cancelled</option><option>unknown</option><option>unsupported</option></select></label>
          <label>Từ giờ VN<input type="datetime-local" id="trace-from"></label>
          <label>Đến giờ VN<input type="datetime-local" id="trace-to"></label>
          <label>Tìm step / tool / model<input id="trace-search" maxlength="200"></label>
          <label>Sắp xếp<select id="trace-sort"><option value="time_asc">Thời gian tăng</option><option value="time_desc">Mới nhất</option><option value="duration_desc">Lâu nhất</option><option value="status">Trạng thái</option></select></label>
          <label>Chế độ<select id="trace-mode"><option value="graph">Graph View</option><option value="swim">Swimlane View</option><option value="timeline">Timeline View</option><option value="table">Table View</option></select></label>
        </div>
      </div>

      <details id="trace-summary-wrap" class="card trace-collapsible" open>
        <summary><strong>Tổng quan Nhiệm vụ / Phiên (Task &amp; Session Summary)</strong> <span id="trace-live-badge" class="badge muted">Idle</span></summary>
        <div id="trace-summary" class="trace-summary-grid"></div>
      </details>

      <details id="trace-groups-wrap" class="card trace-collapsible" open>
        <summary><strong>Tổng hợp theo Nhóm / Lane (Group Summary)</strong></summary>
        <div id="trace-groups" class="trace-groups-grid"></div>
      </details>

      <div id="trace-metrics" class="trace-metrics" aria-live="polite"></div>

      <div id="trace-inspector-card" class="card" hidden>
        <div class="trace-page-head">
          <h3>Token Telemetry Inspector — Giải thích trạng thái đo lường</h3>
          <button id="trace-inspector-close" type="button" class="secondary">Đóng</button>
        </div>
        <div id="trace-inspector-body">Đang tải thông tin telemetry...</div>
      </div>

      <div id="trace-workspace" class="trace-workspace">
        <div class="trace-toolbar">
          <div class="trace-toolbar-group">
            <button id="trace-mode-graph" type="button" class="secondary trace-mode-btn" data-mode="graph">Graph View</button>
            <button id="trace-mode-swim" type="button" class="secondary trace-mode-btn" data-mode="swim">Swimlane View</button>
            <button id="trace-mode-timeline" type="button" class="secondary trace-mode-btn" data-mode="timeline">Timeline View</button>
            <button id="trace-mode-table" type="button" class="secondary trace-mode-btn" data-mode="table">Table View</button>
          </div>
          <div class="trace-toolbar-group">
            <button id="trace-zoom-out" type="button" class="secondary" title="Thu nhỏ">-</button>
            <span id="trace-zoom-label" class="trace-zoom-label">100%</span>
            <button id="trace-zoom-in" type="button" class="secondary" title="Phóng to">+</button>
            <button id="trace-zoom-reset" type="button" class="secondary">100%</button>
            <button id="trace-fit" type="button" class="secondary">Fit</button>
          </div>
          <div class="trace-toolbar-group">
            <button id="trace-density" type="button" class="secondary">Compact</button>
            <button id="trace-hide-empty" type="button" class="secondary">Ẩn lane trống</button>
            <button id="trace-collapse-all" type="button" class="secondary">Thu gọn nhóm</button>
            <button id="trace-fullscreen" type="button" class="secondary">Mở rộng</button>
            <button id="trace-detail-toggle" type="button" class="secondary">Chi tiết</button>
          </div>
          <div class="trace-toolbar-group trace-pager">
            <span id="trace-count" aria-live="polite"></span>
            <button id="trace-prev" type="button" class="secondary">Trước</button>
            <button id="trace-next" type="button" class="secondary">Sau</button>
          </div>
        </div>
        <div class="trace-layout" id="trace-layout">
          <div class="trace-canvas" id="trace-canvas-wrap">
            <div class="trace-stage" id="trace-view"></div>
          </div>
          <div class="trace-resizer" id="trace-resizer" title="Kéo để thay đổi chiều rộng Detail Panel"></div>
          <aside class="trace-detail-panel" id="trace-detail">Chọn step để xem bằng chứng.</aside>
        </div>
      </div>
    </section>
    <section id="tab-conversations" class="panel">
      <div class="trace-page-head">
        <h2>Lịch sử hội thoại AI (V3.2 / V3.3 FTS5)</h2>
        <button id="conv-refresh" type="button">Tải hội thoại</button>
      </div>
      <div class="card">
        <div class="trace-filters">
          <label>Dự án<select id="conv-proj"></select></label>
          <label>Worktree<select id="conv-wt"></select></label>
          <label>Client<select id="conv-client"><option value="">Tất cả</option><option>antigravity</option><option>claude</option><option>codex</option><option>gemini</option><option>manual_import</option></select></label>
          <label>Model<input id="conv-model" placeholder="vd: gemini-4-neon-medium"></label>
          <label>Tìm kiếm FTS5 tiếng Việt (0 LLM)<input id="conv-search" maxlength="200" placeholder="Nhập từ khóa + Enter"></label>
        </div>
      </div>
      <div class="conv-layout">
        <aside class="card conv-sidebar">
          <div id="conv-list" class="conv-list">Chọn dự án để xem hội thoại.</div>
          <div class="row-actions" style="margin-top:8px">
            <button id="conv-prev" type="button" class="secondary">Trước</button>
            <button id="conv-next" type="button" class="secondary">Sau</button>
          </div>
        </aside>
        <div class="card conv-main">
          <div id="conv-detail-head"></div>
          <div id="conv-messages" class="conv-messages">Chọn hội thoại bên trái để xem chi tiết.</div>
        </div>
      </div>
    </section>
    <section id="tab-mcp" class="panel">
      <div class="trace-page-head">
        <h2>Dynamic MCP Registry &amp; Capability Routing (V3.0)</h2>
        <button id="mcp-refresh" type="button">Làm mới Registry</button>
      </div>
      <div class="card">
        <h3>Kiểm thử Capability Router (0 LLM)</h3>
        <div class="trace-filters">
          <label>Dự án<select id="mcp-proj"></select></label>
          <label>Capabilities (phân tách bằng dấu phẩy)<input id="mcp-route-caps" value="code_search, memory_read"></label>
          <label><input id="mcp-route-write" type="checkbox"> Cho phép write_scoped</label>
        </div>
        <button id="mcp-route-btn" type="button" class="secondary">Định tuyến MCP</button>
        <div id="mcp-route-result" style="margin-top:8px"></div>
      </div>
      <div id="mcp-servers-list"></div>
      <div class="card">
        <h3>Nhật ký Audit Dynamic MCP</h3>
        <div id="mcp-audit-list"></div>
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
      window.dispatchEvent(new CustomEvent("hub-tab", {detail:name}));
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
        badge.textContent = "Chưa đăng nhập (Cần đăng nhập)";
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
        return Boolean(data.authenticated);
      } catch {
        updateAuthUI(false, null);
        return false;
      }
    }

    async function doLogin() {
      const password = document.getElementById("login-password").value;
      try {
        const data = await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
        document.getElementById("login-password").value = "";
        updateAuthUI(true, data.csrf_token);
        toast("Đăng nhập thành công");
        await Promise.all([loadOverview(), loadHermesPanel(), loadProjects()]);
        window.dispatchEvent(new Event("hub-ready"));
      } catch (e) {
        toast("Đăng nhập thất bại: " + e.message);
      }
    }

    async function doLogout() {
      try {
        await apiFetch("/api/auth/logout", { method: "POST" });
        updateAuthUI(false, null);
        projectsCache = [];
        worktreesByProject = {};
        document.getElementById("projects-list").innerHTML = `<p class="badge warn">Vui lòng đăng nhập để xem dữ liệu dự án.</p>`;
        toast("Đã đăng xuất");
      } catch (e) {
        toast(e.message);
      }
    }

    function serviceStateBadge(s) {
      const state = s?.state || (s?.ready ? "active" : (s?.status === "disabled" ? "disabled" : "unreachable"));
      if (state === "active") return { cls: "ok", label: s?.label || "Đang hoạt động" };
      if (state === "disabled") return { cls: "muted", label: s?.label || "Đã tắt" };
      if (state === "unconfigured") return { cls: "warn", label: s?.label || "Chưa cấu hình" };
      return { cls: "err", label: s?.label || "Lỗi kết nối" };
    }

    async function loadOverview() {
      try {
        const status = await apiFetch("/api/status");
        const cards = document.getElementById("health-cards");
        const renderCard = (title, s) => {
          const b = serviceStateBadge(s);
          const detail = s?.detail ? `<p style="margin:8px 0 0;font-size:0.82rem;color:var(--muted)">${s.detail}</p>` : "";
          return `<div class="card"><h3>${title}</h3><span class="badge ${b.cls}">${b.label}</span>${detail}</div>`;
        };
        cards.innerHTML =
          renderCard("Memory Bridge", status.bridge) +
          renderCard("Codebase Adapter", status.codebase) +
          renderCard("Hermes Worker", status.hermes) +
          renderCard("Host Brain Agent", status.host_brain);
        document.getElementById("overview-summary").innerHTML = `<pre>${JSON.stringify(status, null, 2)}</pre>`;
      } catch (e) {
        toast("Lỗi tải trạng thái: " + e.message);
      }
    }

    async function loadHermesPanel() {
      try {
        const data = await apiFetch("/api/hermes/config");
        const b = serviceStateBadge(data.runtime);
        document.getElementById("hermes-provider").value = data.provider || "deepseek";
        document.getElementById("hermes-allow-paid").checked = Boolean(data.allow_paid_deepseek);
        const keyBadge = data.deepseek_configured
          ? `<span class="badge ok">Đã cấu hình</span>`
          : `<span class="badge warn">Chưa cấu hình</span>`;
        const paidBadge = data.allow_paid_deepseek
          ? `<span class="badge warn">Đã cấp phép</span>`
          : `<span class="badge muted">Đã tắt (An toàn)</span>`;
        document.getElementById("hermes-runtime-card").innerHTML = `
          <div class="row-actions" style="gap:12px">
            <span>Trạng thái: <span class="badge ${b.cls}">${b.label}</span></span>
            <span>DEEPSEEK_API_KEY: ${keyBadge}</span>
            <span>DeepSeek API trả phí: ${paidBadge}</span>
          </div>
          <p style="margin:8px 0 0;font-size:0.85rem;color:var(--muted)">${data.runtime?.detail || ""}</p>
        `;
      } catch (e) {
        toast("Lỗi tải cấu hình Hermes: " + e.message);
      }
    }

    async function toggleHermes(enabled) {
      const provider = document.getElementById("hermes-provider").value;
      const allowPaid = enabled ? document.getElementById("hermes-allow-paid").checked : false;
      try {
        await apiFetch("/api/hermes/config", {
          method: "PUT",
          body: JSON.stringify({
            enabled: Boolean(enabled),
            provider: provider,
            allow_paid_deepseek: allowPaid
          })
        });
        toast(enabled ? "Đã bật Hermes (không tự kích hoạt DeepSeek API trả phí)" : "Đã tắt Hermes Worker");
        await Promise.all([loadHermesPanel(), loadOverview()]);
      } catch (e) {
        toast("Lỗi cập nhật Hermes (cần đăng nhập quản trị): " + e.message);
      }
    }

    async function toggleProjectHermes(projectId, approved) {
      try {
        await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/hermes`, {
          method: "PATCH",
          body: JSON.stringify({ hermes_approved: Boolean(approved) })
        });
        toast(approved ? "Đã bật Opt-in Hermes cho dự án" : "Đã tắt Opt-in Hermes cho dự án");
        await loadProjects();
      } catch (e) {
        toast("Lỗi cập nhật dự án (cần đăng nhập quản trị): " + e.message);
      }
    }

    async function loadProjects() {
      try {
        const data = await apiFetch("/api/projects");
        projectsCache = data.items || [];
        const selects = ["wt-project-select", "code-proj", "mem-proj", "ho-proj", "ev-proj", "brain-proj", "plans-proj", "sess-proj", "agents-proj", "metrics-proj", "trace-proj", "conv-proj", "mcp-proj"];
        for (const sid of selects) {
          const sel = document.getElementById(sid);
          const prev = sel.value;
          sel.innerHTML = projectsCache.map(p => `<option value="${p.project_id}">${p.name} (${p.project_id.slice(0,8)})</option>`).join("");
          if (prev && projectsCache.some(p => p.project_id === prev)) sel.value = prev;
        }
        let html = "<table><tr><th>Tên</th><th>Định danh</th><th>Hermes</th><th>Worktrees</th></tr>";
        let hermesHtml = "<table><tr><th>Dự án</th><th>Trạng thái Hermes</th><th>Hành động (Quản trị)</th></tr>";
        for (const p of projectsCache) {
          const wtData = await apiFetch(`/api/projects/${p.project_id}/worktrees`);
          worktreesByProject[p.project_id] = wtData.items || [];
          const wtText = (wtData.items || []).map(w => `${w.branch || "detached"} [${w.mount_status}]`).join("<br/>") || "Chưa có";
          html += `<tr><td>${p.name}</td><td><code>${p.canonical_identity}</code></td><td>${p.hermes_approved ? "Đã bật" : "Tắt"}</td><td>${wtText}</td></tr>`;
          const toggleBtn = p.hermes_approved
            ? `<button class="secondary" onclick="toggleProjectHermes('${p.project_id}', false)">Tắt Opt-in</button>`
            : `<button class="secondary" onclick="toggleProjectHermes('${p.project_id}', true)">Bật Opt-in</button>`;
          hermesHtml += `<tr><td>${p.name}</td><td><span class="badge ${p.hermes_approved ? "ok" : "muted"}">${p.hermes_approved ? "Đã phê duyệt (Opt-in)" : "Đã tắt (Từ chối mặc định)"}</span></td><td>${toggleBtn}</td></tr>`;
        }
        html += "</table>";
        hermesHtml += "</table>";
        document.getElementById("projects-list").innerHTML = html;
        document.getElementById("hermes-status").innerHTML = hermesHtml;
        syncWorktrees("code-proj", "code-wt");
        syncWorktrees("mem-proj", "mem-wt");
        syncWorktrees("ho-proj", "ho-wt");
        syncWorktrees("plans-proj", "plans-wt");
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

    let reindexPollTimer = null;
    async function triggerReindex() {
      const project_id = document.getElementById("code-proj").value;
      const worktree_id = document.getElementById("code-wt").value;
      if (!project_id || !worktree_id) {
        toast("Chọn dự án và worktree trước khi Reindex.");
        return;
      }
      try {
        const data = await apiFetch("/api/index", {
          method: "POST",
          body: JSON.stringify({ project_id, worktree_id, background: true })
        });
        const st = data.status || data.state || "unknown";
        if (data.no_op) {
          toast(`Index đã mới nhất (no-op): ${data.nodes ?? "?"} nodes / ${data.edges ?? "?"} edges.`);
          return;
        }
        toast(
          data.deduplicated
            ? `Đang có job reindex chạy (${data.job_id?.slice(0,8)}), theo dõi tiến độ...`
            : `Đã khởi chạy reindex bất đồng bộ (${data.job_id?.slice(0,8)}): ${st}`
        );
        if (st === "succeeded" || st === "failed" || !data.job_id) return;
        if (reindexPollTimer) clearInterval(reindexPollTimer);
        let polls = 0;
        reindexPollTimer = setInterval(async () => {
          polls += 1;
          if (polls > 90) {
            clearInterval(reindexPollTimer);
            reindexPollTimer = null;
            toast("Hết thời gian theo dõi tiến độ trên trình duyệt; job có thể vẫn đang chạy trên Codebase Adapter.");
            return;
          }
          try {
            const job = await apiFetch(
              `/api/index/${encodeURIComponent(data.job_id)}?project_id=${encodeURIComponent(project_id)}&worktree_id=${encodeURIComponent(worktree_id)}`
            );
            const jst = job.status || job.state;
            if (jst === "succeeded") {
              clearInterval(reindexPollTimer);
              reindexPollTimer = null;
              toast(`Reindex hoàn tất: ${job.nodes ?? "?"} nodes / ${job.edges ?? "?"} edges.`);
            } else if (jst === "failed") {
              clearInterval(reindexPollTimer);
              reindexPollTimer = null;
              toast(`Reindex thất bại: ${job.error || "unknown"}`);
            }
          } catch (pe) {
            clearInterval(reindexPollTimer);
            reindexPollTimer = null;
            toast("Lỗi kiểm tra tiến độ reindex: " + pe.message);
          }
        }, 2000);
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

    let brainRolesCache = [];
    let catalogCache = null;

    function verificationStageBadge(stage) {
      if (stage === "execution_verified") return `<span class="badge ok">Đã thực thi thành công</span>`;
      if (stage === "cli_available") return `<span class="badge ok">Khả dụng qua CLI</span>`;
      if (stage === "catalog_listed") return `<span class="badge warn">Xuất hiện trong Catalog</span>`;
      return `<span class="badge muted">Chưa khả dụng</span>`;
    }

    async function loadBrainPanel() {
      const pid = document.getElementById("brain-proj").value;
      if (!pid) {
        document.getElementById("brain-roles-view").innerHTML = `<p style="color:var(--muted)">Chưa có dự án đăng ký. Hãy đăng ký dự án trước.</p>`;
        return;
      }
      try {
        const [cfg, usage, cat] = await Promise.all([
          apiFetch(`/api/v2/brain/config?project_id=${encodeURIComponent(pid)}`),
          apiFetch(`/api/v2/usage?project_id=${encodeURIComponent(pid)}`),
          apiFetch("/api/v2/catalog")
        ]);
        catalogCache = cat;
        brainRolesCache = cfg.roles || [];

        const dl = document.getElementById("catalog-model-options");
        dl.innerHTML = (cat.models || []).map(m => `<option value="${m.model_id}">${m.provider} | ${m.verification_stage || m.status}</option>`).join("");

        const banner = document.getElementById("neon-banner");
        if (cat.gemini4_neon_available && (cat.gemini4_neon_models || []).length) {
          const neonId = cat.gemini4_neon_models[0];
          banner.style.display = "block";
          banner.innerHTML = `<strong>${cat.gemini4_neon_recommendation}</strong> <button class="primary" style="margin:0 0 0 10px" onclick="switchToGemini4Neon('${neonId}')">Chuyển bộ não chính sang ${neonId}</button>`;
        } else {
          banner.style.display = "none";
        }

        document.getElementById("brain-approval-mode").value = cfg.approval_mode || "REVIEW_REQUIRED";
        document.getElementById("brain-planner-budget").value = cfg.daily_planner_budget_usd ?? "";
        document.getElementById("brain-paid-fallback").checked = Boolean(cfg.allow_paid_api_fallback);

        const stageByModel = {};
        for (const m of (cat.models || [])) stageByModel[m.model_id] = m.verification_stage || m.status;

        const rows = brainRolesCache.map((r, idx) => {
          const prov = r.effective_provider || "agy";
          const mid = r.effective_model_id || "";
          const eff = r.effective_effort || "";
          const fb = (r.fallback_order || []).join(", ");
          const stage = prov === "local" ? "execution_verified" : (stageByModel[mid] || (r.availability_status === "verified" ? "cli_available" : "unavailable"));
          return `<tr>
            <td><code>${r.role_name}</code><br/><small style="color:var(--muted)">${r.preferred_label || ""}</small></td>
            <td>
              <select id="br-prov-${idx}" style="min-width:95px">
                <option value="agy" ${prov==="agy"?"selected":""}>agy (CLI)</option>
                <option value="claude" ${prov==="claude"?"selected":""}>claude (CLI)</option>
                <option value="codex" ${prov==="codex"?"selected":""}>codex (CLI)</option>
                <option value="local" ${prov==="local"?"selected":""}>local (0 LLM)</option>
                <option value="deepseek" ${prov==="deepseek"?"selected":""}>deepseek (API)</option>
              </select>
            </td>
            <td><input id="br-model-${idx}" list="catalog-model-options" value="${mid}" placeholder="Ví dụ: claude-sonnet-5-5-medium" /></td>
            <td>
              <select id="br-eff-${idx}" style="min-width:85px">
                <option value="" ${!eff?"selected":""}>Mặc định</option>
                <option value="low" ${eff==="low"?"selected":""}>low</option>
                <option value="medium" ${eff==="medium"?"selected":""}>medium</option>
                <option value="high" ${eff==="high"?"selected":""}>high</option>
              </select>
            </td>
            <td><input id="br-fb-${idx}" value="${fb}" placeholder="model1, model2" /></td>
            <td>${verificationStageBadge(stage)}<br/><small style="color:var(--muted)">${r.availability_reason || ""}</small></td>
            <td class="row-actions">
              <button class="secondary" onclick="probeRoleModel(${idx}, 'connection_check')">Kiểm tra kết nối</button>
              <button class="secondary" onclick="probeRoleModel(${idx}, 'trial_run')">Chạy thử (Cần duyệt)</button>
            </td>
          </tr>`;
        }).join("");
        document.getElementById("brain-roles-view").innerHTML =
          `<table><tr><th>Vai trò</th><th>Provider / CLI</th><th>Model ID</th><th>Effort</th><th>Thứ tự Fallback</th><th>Xác minh</th><th>Kiểm thử</th></tr>${rows}</table>`;

        const fmt = (v) => (v === null || v === undefined ? "Không có dữ liệu" : String(v));
        const p = usage.planner || {};
        const ex = usage.executor || {};
        document.getElementById("brain-usage-view").innerHTML = `
          <table>
            <tr><th>Nhóm</th><th>Input</th><th>Output</th><th>Cache Read</th><th>Chi phí (USD)</th><th>Nguồn đo</th></tr>
            <tr><td><strong>Điều phối (Planner)</strong></td><td>${fmt(p.input_tokens)}</td><td>${fmt(p.output_tokens)}</td><td>${fmt(p.cache_read_tokens)}</td><td>${fmt(p.cost_usd)}</td><td>${fmt(p.measurement_kind)}</td></tr>
            <tr><td><strong>Thực thi (Executor)</strong></td><td>${fmt(ex.input_tokens)}</td><td>${fmt(ex.output_tokens)}</td><td>${fmt(ex.cache_read_tokens)}</td><td>${fmt(ex.cost_usd)}</td><td>${fmt(ex.measurement_kind)}</td></tr>
          </table>
          <p style="margin-top:8px;color:var(--muted)">Hạn mức gói (Subscription Quota): <strong>Không có dữ liệu</strong> (không suy diễn từ số token hay cửa sổ ngữ cảnh).</p>
        `;
      } catch (e) { toast("Lỗi tải Bộ não điều phối: " + e.message); }
    }

    function collectBrainRolesFromUI() {
      return brainRolesCache.map((r, idx) => {
        const prov = document.getElementById(`br-prov-${idx}`)?.value || r.effective_provider || "agy";
        const mid = (document.getElementById(`br-model-${idx}`)?.value || "").trim() || null;
        const eff = document.getElementById(`br-eff-${idx}`)?.value || null;
        const fbRaw = (document.getElementById(`br-fb-${idx}`)?.value || "").trim();
        const fb = fbRaw ? fbRaw.split(",").map(s => s.trim()).filter(Boolean) : [];
        const authMode = prov === "deepseek" ? "api_key" : (prov === "local" ? "unknown" : "subscription_cli");
        return {
          role_name: r.role_name,
          preferred_label: r.preferred_label || r.role_name,
          effective_provider: prov,
          effective_model_id: mid,
          effective_effort: eff,
          auth_mode: authMode,
          availability_status: mid ? "verified" : "unverified",
          availability_reason: r.availability_reason || "Cấu hình trực tiếp bởi quản trị trên Dashboard",
          fallback_order: fb,
          max_escalations: r.max_escalations ?? 1,
          last_verified_at: r.last_verified_at || null
        };
      });
    }

    async function saveBrainPolicy() {
      const pid = document.getElementById("brain-proj").value;
      if (!pid) { toast("Vui lòng chọn dự án"); return; }
      const rawBudget = document.getElementById("brain-planner-budget").value;
      const allowPaid = document.getElementById("brain-paid-fallback").checked;
      try {
        await apiFetch("/api/v2/brain/config", {
          method: "PUT",
          body: JSON.stringify({
            project_id: pid,
            approval_mode: document.getElementById("brain-approval-mode").value,
            daily_planner_budget_usd: rawBudget === "" ? null : Number(rawBudget),
            allow_paid_api_fallback: allowPaid,
            allowed_auth_modes: allowPaid ? ["subscription_cli", "api_key"] : ["subscription_cli"],
            roles: collectBrainRolesFromUI()
          })
        });
        toast("Đã lưu cấu hình Bộ não điều phối cho dự án");
        await loadBrainPanel();
      } catch (e) { toast("Lỗi lưu cấu hình (cần đăng nhập quản trị): " + e.message); }
    }

    async function switchToGemini4Neon(neonModelId) {
      const idx = brainRolesCache.findIndex(r => r.role_name === "tier2_primary");
      if (idx >= 0) {
        document.getElementById(`br-prov-${idx}`).value = "agy";
        document.getElementById(`br-model-${idx}`).value = neonModelId;
        document.getElementById(`br-eff-${idx}`).value = "medium";
        await saveBrainPolicy();
      }
    }

    async function probeRoleModel(idx, mode) {
      const pid = document.getElementById("brain-proj").value;
      const prov = document.getElementById(`br-prov-${idx}`).value;
      const mid = document.getElementById(`br-model-${idx}`).value.trim();
      const allowPaid = document.getElementById("brain-paid-fallback").checked;
      if (!mid) { toast("Vui lòng nhập Model ID trước khi kiểm tra"); return; }
      if (mode === "trial_run") {
        const ok = window.confirm(`Xác nhận phê duyệt chạy thử model '${mid}' qua CLI '${prov}'? (Sẽ gửi 1 prompt ngắn kiểm tra phản hồi thực tế.)`);
        if (!ok) return;
      }
      const outEl = document.getElementById("brain-probe-output");
      outEl.style.display = "block";
      outEl.textContent = mode === "trial_run" ? `Đang chạy thử ${prov}/${mid}...` : `Đang kiểm tra kết nối ${prov}/${mid}...`;
      try {
        const res = await apiFetch("/api/v2/brain/probe", {
          method: "POST",
          body: JSON.stringify({
            project_id: pid,
            provider: prov,
            cli_family: prov,
            model_id: mid,
            mode: mode,
            user_approved: mode === "trial_run",
            allow_paid_api: allowPaid
          })
        });
        outEl.textContent = JSON.stringify(res, null, 2);
        toast(res.message || "Hoàn tất kiểm tra");
        if (mode === "trial_run" && res.ok) {
          await Promise.all([loadBrainPanel(), loadCatalog()]);
        }
      } catch (e) {
        outEl.textContent = "Lỗi: " + e.message;
        toast("Lỗi kiểm tra model: " + e.message);
      }
    }

    async function syncCatalog() {
      try {
        toast("Đang quét CLI trên host (agy/claude/codex)...");
        const res = await apiFetch("/api/v2/catalog/sync", { method: "POST", body: JSON.stringify({}) });
        toast("Đã cập nhật danh mục model từ host CLI");
        await Promise.all([loadCatalog(), loadBrainPanel(), loadOverview()]);
      } catch (e) { toast("Lỗi đồng bộ danh mục (cần đăng nhập quản trị & Host Brain Agent): " + e.message); }
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

    async function analyzeAndCreatePlan() {
      const pid = document.getElementById("plans-proj").value;
      const wid = document.getElementById("plans-wt").value;
      const obj = document.getElementById("plan-objective").value.trim();
      const comp = document.getElementById("plan-complexity").value || null;
      if (Array.from(obj).length > 32000) { toast("Prompt vượt 32.000 ký tự. Hãy rút gọn hoặc chia nhiệm vụ."); return; }
      if (!pid || !wid || !obj) { toast("Vui lòng chọn dự án, worktree và nhập Prompt"); return; }
      try {
        const res = await apiFetch("/api/v2/plans/analyze", {
          method: "POST",
          body: JSON.stringify({ project_id: pid, worktree_id: wid, objective: obj, complexity: comp })
        });
        const editor = document.getElementById("plan-objective");
        if (editor.value.trim() === obj && document.getElementById("plans-proj").value === pid && document.getElementById("plans-wt").value === wid) { editor.value = ""; editor.dispatchEvent(new Event("input")); }
        toast("Đã phân tích & tạo kế hoạch chờ phê duyệt (REVIEW_REQUIRED)");
        await loadPlans();
      } catch (e) { toast("Lỗi tạo kế hoạch: " + e.message); }
    }

    async function dispatchPlan(planId, worktreeId) {
      const pid = document.getElementById("plans-proj").value;
      const outEl = document.getElementById("plan-dispatch-output");
      outEl.style.display = "block";
      outEl.textContent = "Đang khởi chạy Agent trên Host Brain Agent...";
      try {
        const res = await apiFetch(`/api/v2/plans/${planId}/dispatch?project_id=${encodeURIComponent(pid)}`, {
          method: "POST",
          body: JSON.stringify({ worktree_id: worktreeId })
        });
        outEl.textContent = res.message || "Đã nhận phản hồi từ runner.";
        toast(res.ok ? "Agent thực thi hoàn tất!" : "Agent thực thi thất bại");
        await Promise.all([loadPlans(), loadSessions(), loadCatalog()]);
      } catch (e) {
        outEl.textContent = "Lỗi: " + e.message;
        toast("Lỗi khởi chạy Agent: " + e.message);
      }
    }

    async function planAction(planId, action, revId, revHash) {
      const pid = document.getElementById("plans-proj").value;
      try {
        const body = action === "approve"
          ? { revision_id: revId, revision_hash: revHash, actor: "dashboard-admin", mode: "manual" }
          : { project_id: pid, actor: "dashboard-admin" };
        await apiFetch(`/api/v2/plans/${planId}/${action}?project_id=${encodeURIComponent(pid)}`, {
          method: "POST",
          body: JSON.stringify(body)
        });
        toast("Đã thực hiện: " + action);
        await loadPlans();
      } catch (e) { toast("Lỗi kế hoạch: " + e.message); }
    }

    async function loadSessions() {
      const pid = document.getElementById("sess-proj").value;
      if (!pid) return;
      const comp = document.getElementById("sess-comp").value;
      const out = document.getElementById("sess-outcome").value;
      try {
        const [sData, cData, calData] = await Promise.all([
          apiFetch(`/api/v2/sessions?project_id=${encodeURIComponent(pid)}&complexity=${encodeURIComponent(comp)}&outcome=${encodeURIComponent(out)}`),
          apiFetch(`/api/v2/analytics/cohorts?project_id=${encodeURIComponent(pid)}`),
          apiFetch(`/api/v2/analytics/calibration?project_id=${encodeURIComponent(pid)}`)
        ]);
        const fmt = v => (v === null || v === undefined) ? "Không có dữ liệu" : v;
        const sRows = (sData.items || []).map(s => `
          <tr><td>${s.role}</td><td><code>${s.model_id}</code></td><td>${fmt(s.complexity)}</td><td><span class="badge">${s.status}</span></td>
          <td>In: ${fmt(s.usage?.input_tokens)} / Out: ${fmt(s.usage?.output_tokens)}</td>
          <td><button class="secondary" onclick="deleteSession('${s.id}')">Xóa (Retention)</button></td></tr>
        `).join("");
        document.getElementById("sessions-list").innerHTML = sRows
          ? `<table><tr><th>Vai trò</th><th>Model</th><th>Độ phức tạp</th><th>Trạng thái</th><th>Token đo được</th><th>Hành động</th></tr>${sRows}</table>`
          : `<p style="color:var(--muted)">Chưa có phiên nào.</p>`;
        document.getElementById("cohorts-view").innerHTML = `<pre>${JSON.stringify(cData.items || [], null, 2)}</pre>`;
        document.getElementById("calibration-view").innerHTML = `<pre>${JSON.stringify(calData, null, 2)}</pre>`;
      } catch (e) { toast(e.message); }
    }

    async function deleteSession(sid) {
      const pid = document.getElementById("sess-proj").value;
      try {
        await apiFetch(`/api/v2/sessions/${sid}?project_id=${encodeURIComponent(pid)}`, { method: "DELETE" });
        toast("Đã xóa phiên theo chính sách lưu trữ");
        await loadSessions();
      } catch (e) { toast(e.message); }
    }

    async function loadCatalog() {
      try {
        const cat = await apiFetch("/api/v2/catalog");
        const alerts = [];
        if (cat.gemini4_neon_available) {
          alerts.push(`<span class="badge ok">${cat.gemini4_neon_recommendation}</span>`);
        }
        if ((cat.new_models || []).length) {
          alerts.push(`<span class="badge ok">Model mới phát hiện: ${cat.new_models.join(", ")}</span>`);
        }
        if ((cat.retiring_models || []).length) {
          alerts.push(`<span class="badge warn">Model sắp ngừng hỗ trợ / gỡ bỏ: ${cat.retiring_models.join(", ")}</span>`);
        }
        document.getElementById("catalog-alerts").innerHTML = alerts.join(" ");
        const rows = (cat.models || []).map(m => `
          <tr>
            <td>${m.provider}</td>
            <td><code>${m.model_id}</code></td>
            <td>${m.cli_family || "-"} ${m.cli_version || ""}</td>
            <td>${m.auth_mode}</td>
            <td>${verificationStageBadge(m.verification_stage || m.status)}</td>
          </tr>
        `).join("");
        document.getElementById("catalog-view").innerHTML = rows
          ? `<p>Snapshot: <code>${cat.snapshot_id}</code> | Cập nhật: ${cat.fetched_at || "-"} | Stale: ${cat.is_stale}</p><table><tr><th>Provider</th><th>Model ID</th><th>CLI</th><th>Auth</th><th>Mức xác minh</th></tr>${rows}</table>`
          : `<p class="badge warn">Chưa có snapshot danh mục model. Bấm "Quét lại CLI &amp; Cập nhật Danh mục".</p>`;
      } catch (e) { toast(e.message); }
    }

    window.addEventListener("DOMContentLoaded", async () => {
      const authed = await checkSession();
      await loadOverview();
      if (authed) {
        await loadHermesPanel();
        await loadProjects();
        window.dispatchEvent(new Event("hub-ready"));
      } else {
        document.getElementById("projects-list").innerHTML = `<p class="badge warn">Vui lòng đăng nhập để xem và quản trị dữ liệu dự án.</p>`;
      }
    });
  </script>
</body>
</html>
"""
