# V2.2.4 — Telemetry Coverage & Client Usage Survey

Cập nhật: 2026-11-25

## Kết quả khảo sát nguồn Usage thực tế trên Windows host

| Client | Vị trí dữ liệu cục bộ | Trường đo được hỗ trợ | Trường chưa hỗ trợ | Counter Mode | Trạng thái |
|---|---|---|---|---|---|
| **Antigravity IDE** | `~/.gemini/antigravity/brain/<conv-id>/.system_generated/logs/transcript.jsonl` (`PLANNER_RESPONSE`) | `input_tokens`, `output_tokens`, `cache_read_tokens`, `model`, `created_at` | `cache_write_tokens`, `reasoning_tokens`, `cost_usd`, `provider_reported_model` | `delta` | Hỗ trợ qua opt-in Usage Collector |
| **Claude Code** | `~/.claude/projects/<slug>/*.jsonl` (`assistant` / `message.usage`) | `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens_details.thinking_tokens`, `message.model` | `api_billing_cost` (thuê bao) | `delta` | Hỗ trợ qua opt-in Usage Collector |
| **Codex CLI** | `~/.codex/sessions/**/rollout-*.jsonl` (`token_usage_record` / `total_token_usage`) | `input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `reasoning_output_tokens`, `model` | `api_billing_cost` (thuê bao) | `cumulative` | Hỗ trợ qua opt-in Usage Collector |
| **Gemini CLI** | `~/.gemini/tmp/` | Không có | Toàn bộ (CLI chưa cài đặt trên host) | `unknown` | `unsupported` |

## Quy tắc minh bạch dữ liệu

- Không gán `0` khi chưa đo được; hiển thị chính xác một trong 7 trạng thái: `measured`, `estimated`, `pending`, `unsupported`, `collection_error`, `not_applicable`, `unknown`.
- Thao tác không dùng LLM (`ai_memory`, `code_search`, `local_rule`, `handoff`, `qa_test` với `used_llm=false`) hiển thị `Not applicable (0 LLM)`.
- Phân biệt rõ `requested_model`, `client_declared_model`, và `provider_reported_model`; không dùng model Agent tự khai báo làm model đã được provider xác minh.
- Usage Collector chỉ đọc metadata token/model/session/timestamp; bỏ qua hoàn toàn `thinking`, nội dung hội thoại, prompt, raw tool payloads và secrets.
