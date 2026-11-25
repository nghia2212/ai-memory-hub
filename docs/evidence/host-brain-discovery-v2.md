# Xác minh thực tế Interface CLI & Model khả dụng trên Host Windows (V2 Adaptive Brain)

- **Thời điểm xác minh:** `2026-11-25T10:16:57+07:00`
- **Phương thức:** Chỉ dùng lệnh CLI chính thức ở chế độ chỉ đọc (`--version`, `--help`, `agy models`); **KHÔNG** scrape UI IDE, **KHÔNG** đọc credential phiên, **KHÔNG** gọi endpoint riêng tư, **KHÔNG** gọi API trả phí.

## 1. Kết quả kiểm tra CLI trên máy host

| CLI | Trạng thái | Phiên bản | Hỗ trợ Non-interactive & Structured JSON | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| **`agy`** (`C:\Users\10wos\AppData\Local\agy\bin\agy.exe`) | **INSTALLED** | `1.1.27` | Có (`--print`, `--output-format json\|stream-json`, `--json-schema`, `--mode plan`, `--model`, `--effort low\|medium\|high`) | Không dùng `--dangerously-skip-permissions`. Lệnh chính thức `agy models` trả danh sách model thực sự khả dụng cho CLI. |
| **`claude`** (`C:\Users\10wos\.local\bin\claude.exe`) | **INSTALLED** | `2.1.269 (Claude Code)` | Có (`-p`, `--output-format json\|stream-json`, `--model`, `--effort`, `--max-budget-usd`, `--permission-mode`) | Không có cờ đọc hạn mức gói thuê bao còn lại (`subscription_quota = null`). |
| **`codex`** (`C:\Users\10wos\AppData\Roaming\npm\codex.ps1`) | **INSTALLED** | `codex-cli 0.166.0` | Có (`codex exec --json -m <model> -s read-only\|workspace-write --output-schema`) | Không có cờ đọc hạn mức gói thuê bao còn lại (`subscription_quota = null`). |
| **`gemini`** | **NOT_INSTALLED** | — | — | Chưa cài trên PATH Windows. |
| Python SDK (`antigravity`, `google.genai`) | **NOT_INSTALLED** | — | — | Chưa cài trong Python hệ thống. |

## 2. Danh sách Model thực tế từ `agy models` (`agy 1.1.27`)

```text
gemini-3.8-flash-high    Gemini 3.8 Flash (High)
gemini-3.8-flash-medium  Gemini 3.8 Flash (Medium)
gemini-3.8-flash-low     Gemini 3.8 Flash (Low)
claude-opus-5-5-low      Claude Opus 5.5 (Low)
claude-opus-5-5-medium   Claude Opus 5.5 (Medium)
claude-opus-5-5-high     Claude Opus 5.5 (High)
claude-sonnet-5-5-low    Claude Sonnet 5.5 (Low)
claude-sonnet-5-5-medium Claude Sonnet 5.5 (Medium)
claude-sonnet-5-5-high   Claude Sonnet 5.5 (High)
gpt-oss-120b-medium      GPT-OSS 120B (Medium)
```

## 3. Kết luận đối chiếu với ưu tiên Adaptive AI Brain (`docs/ADAPTIVE-BRAIN-V2.md`)

- **Gemini 4 Neon Medium / Gemini 4 Hydrogen / Gemini 4 Carbon:**
  - **Trạng thái CLI hiện tại:** `unavailable` (KHÔNG xuất hiện trong `agy models` v1.1.27 dù có thể hiển thị trên menu IDE Antigravity).
  - **Chính sách tuân thủ:** Giữ `preferred_label` là ưu tiên cấu hình, gắn `availability_status = "unavailable"` với lý do rõ ràng; **KHÔNG** hardcode selector chưa được xác minh và **KHÔNG** tự ý chuyển sang DeepSeek API trả phí.
- **Đề xuất phương án thay thế đã xác minh qua `agy --print` (chờ chủ dự án phê duyệt trên Dashboard):**
  - `tier0_rules`: Quy tắc cục bộ + SQLite FTS5/forecast (0 LLM).
  - `tier1_light`: `gemini-3.8-flash-low` hoặc `gemini-3.8-flash-medium` (qua `agy`).
  - `tier2_primary` (thay thế tạm khi Neon chưa có trên CLI): `claude-sonnet-5-5-medium` hoặc `gemini-3.8-flash-high` (qua `agy`).
  - `tier3_advanced`: `claude-opus-5-5-medium` / `claude-opus-5-5-high` (qua `agy`).
  - `secondary_fallback`: `claude-sonnet-5-5-medium` (qua `claude` CLI hoặc `agy`); DeepSeek API chỉ bật khi có phê duyệt dữ liệu ngoài + ngân sách.
