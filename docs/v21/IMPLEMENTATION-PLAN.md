# Kế hoạch triển khai Workbench V2.1

Nhánh triển khai bắt đầu tại `6a8f585` trên V2. PR thiết kế #8 là tài liệu tham chiếu cho hợp đồng revision, scope, lease và audit. Memory Bridge tiếp tục sở hữu dữ liệu nghiệp vụ. Codebase Adapter sở hữu cache index riêng.

| Mốc | Đã có ở V2 | Công việc và điều kiện nghiệm thu | Tiến độ |
|---|---|---|---|
| 0. Tiếp quản | Ba container healthy, host CLI, hai index | Đối chiếu worktree, sao lưu và xác minh DB, phân loại kế hoạch treo | Đã kiểm tra |
| 1. Orchestration | DAG, revision/hash, claim và ledger | Chặn lease cũ, bảo toàn lease hợp lệ khi restart, tách kết quả chưa biết khỏi retry, hiển thị kết quả sau reload | Đang làm |
| 2. Agent | Hook events, sessions và attempts | Hiển thị heartbeat, session thật, phân biệt managed/external, kiểm soát theo quyền | Chờ |
| 3. Explorer | Adapter 0.12.1, search/index | Cây, source giới hạn, graph có bằng chứng, so sánh hai worktree độc lập, export và điều hướng | Chờ |
| 4. Công việc | Phân loại, phê duyệt thủ công, dispatch | Chứng minh context, search, forecast, chỉnh revision, attachment an toàn và handoff | Chờ |
| 5. Brain | Catalog CLI, role editor, probe | Kiểm chính xác model thực thi, chính sách fallback và quota; cập nhật cấu hình tại chỗ | Chờ |
| 6. Analytics | Usage, cohort và quota ledger | Đo tài nguyên và đối chiếu token, hiển thị nguồn và giá trị chưa biết | Chờ |
| 7. Dashboard | Giao diện tiếng Việt, dark mode | Sidebar, lịch sử, bộ lọc, responsive và screenshot đã kiểm tra | Chờ |
| 8. Phân quyền | Bearer theo vai, CSRF, loopback | Ràng buộc worktree/lease/revision, không shell tự do, cô lập secrets và giới hạn kết quả | Xuyên suốt |
| 9. QA | Bộ test V1/V2 | Runtime, game chỉ đọc, managed/external, handoff, mất kết nối, persistence, hồi quy | Chờ |
| 10. Bàn giao | Nhánh tích hợp V2 | Push từng mốc, Draft PR, hướng dẫn, evidence, SHA và phần chưa xác minh | Đang làm |

### Điểm phục hồi 25/11/2026

Worktree tích hợp sạch khi fetch. Worktree Agent C V1 có ba tệp chưa track liên quan runner. Giữ nguyên tại chỗ. Bản tích hợp đã có runner và commit căn chỉnh schema mới hơn. Dashboard, Codebase và Bridge đang chạy. Host service sống, không thấy tiến trình con tại thời điểm kiểm tra. Hermes tùy chọn.

Backup ngoài Git bao gồm dữ liệu hai volume, host data và cấu hình local. Bảy bản SQLite dùng online backup và trả về `ok` từ integrity_check. Baseline có 1 project, 2 worktree, 21 memory, 2 plan và 2 session. Worktree Hub chưa đăng ký trong Bridge.

Một attempt dừng ở claimed, heartbeat cuối 11:46:13 và lease hết 11:51:13 (UTC+7). Plan vẫn RUNNING. Attempt thứ hai đã lưu succeeded và release lúc 11:48:13. V2 startup hiện đưa mọi tác vụ leased/running về queued, có thể thực thi lại công việc chưa rõ kết quả. Mốc 1 thay hành vi này trước khi restart.

Catalog thực tế: agy 1.1.27 liệt kê 10 selector, có Sonnet 5.5, chưa có Neon. Claude Code 2.1.269, Codex CLI 0.166.0; không tìm thấy Gemini CLI trên PATH. Lệnh discovery không dùng prompt.

Docker stats (một mẫu, không phải peak): Bridge 76.79 MiB / 0.12% CPU, Dashboard 48.89 MiB / 0.13%, Codebase 72.87 MiB / 6.78%.

### Tiếp quản từ Codex — checkpoint M1
Đã sửa source: giữ lease còn hạn khi Bridge restart, quarantine attempt hết hạn thành orphaned/blocked và pause plan (không tự chạy lại), từ chối heartbeat sai runner hoặc lease hết hạn, chặn revision/resume khi outcome chưa rõ. Budget reservation giữ nguyên để đối soát. Reconcile khi startup, đọc plan hoặc claim. Thêm regression test synthetic. Không có quyền truy cập runtime laptop trong phiên GitHub này; test chưa chạy, chưa deploy. Còn cần UI hiển thị nguyên nhân blocked, quy trình owner reconciliation/settlement, kiểm tra pause/cancel propagation, actual result/telemetry và các milestone còn lại. Không merge main.

### Checkpoint Workbench UI
Restored missing workbench.js/css referenced by Codex; added real scoped tree/source/outline/trace evidence, history/detail, agent session monitor, V2 usage table, responsive sidebar, dependency list, unified lease expiration. Trace is evidence table, not full interactive graph. Read-only host dispatch remains limited; attachments, safe write execution, external telemetry, self-onboarding and real Windows QA remain open. No tests executed in GitHub connector session.

### P1.2 checkpoint
Added explicit upstream-edge visualization, source navigation for fresh index evidence, separate same-project worktree index/source comparisons and stale-response guards. Unknown graph formats remain evidence-only. Real CBM v0.12.1 trace payload and browser QA NOT RUN; no merge or deployment.

### P1.3 checkpoint
Workbench task creation uses Bridge routing. Policy auth modes intersect request constraints; expired/retiring catalog entries excluded; unknown cost remains unknown; explicit cost limit requires evaluation evidence. Host dispatch rejects non-subscription CLI. Quota semantics and reasoning-effort execution still require provider-specific verification. Tests NOT RUN; no deploy.

### P1.4 checkpoint
Added nearest-rank P50/P95/P99 latency, sample counts, per-role telemetry coverage, sequential cumulative/delta aggregation, quota provenance and unknown states. Regression/browser/Windows tests NOT RUN. Full benchmark and integrated acceptance remain open.
