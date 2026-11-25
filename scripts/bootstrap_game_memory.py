"""Incremental, idempotent Project Memory bootstrapper for Doi Cun Sieu Tri Tue.

Loads verified metadata & curated facts from local repository docs/commits into
Memory Bridge without sending raw source/git history to any external LLM.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HUB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "3520240e-b631-4046-942f-d1543e4d7d67"
WORKTREE_ID = "a0ed1576-ffde-4810-922d-0b90df8fcfba"
HEAD_COMMIT = "b9eb1e6e8c798738802ea867add8959d3b14c802"
CHECKPOINT_FILE = HUB_ROOT / "data" / "bootstrap-doi-cun-checkpoint.json"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api_call(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


MEMORIES: list[dict[str, Any]] = [
    # 1. Project Overview
    {
        "key": "dc-overview-01",
        "type": "note",
        "verify": True,
        "title": "Tổng quan dự án Đội Cún Siêu Trí Tuệ (PWA gia đình cho trẻ 5-10 tuổi)",
        "body": (
            "Game web PWA cài được cho Android và laptop, dành cho trẻ 5-10 tuổi học đếm, cộng, trừ dưới 20 "
            "cùng các nhân vật PAW Patrol. Phát hành công khai trên Cloudflare Pages (https://doi-cun-sieu-tri-tue.pages.dev) "
            "theo cách B: phi thương mại, không quảng bá, chặn máy tìm kiếm (noindex, robots.txt), ghi rõ game tự làm. "
            "Thiết bị ưu tiên kiểm thử thực tế: Samsung S20 (chuẩn hiệu năng 3D ban đầu: Samsung Galaxy A17 4GB)."
        ),
        "tags": ["cat:project-overview", "pwa", "samsung-s20", "kids-game"],
        "evidence_ref": "CLAUDE.md; AI-MEMORY.md",
    },
    # 2. Architecture Decisions
    {
        "key": "dc-arch-01",
        "type": "decision",
        "verify": True,
        "title": "Kiến trúc song song Phaser 3 (2D) + Three.js & HTML/CSS HUD (Trang trại 3D)",
        "body": (
            "Phaser 3.90.0 + TypeScript 7.0.2 + Vite 8.3.4 giữ toàn bộ màn 2D (7 chương Vịnh Phiêu Lưu, Đảo Khủng Long, Sân chơi, Vườn Bạn Thú). "
            "Khu Trang trại 3D dùng Three.js 0.186.1 + HUD HTML/CSS, mô hình GLB dựng bằng Blender. "
            "Dùng chung bộ sinh đề (src/math/generator.ts), độ khó (difficulty.ts), giọng đọc (audio.ts), IndexedDB (idb), save/merge và xương vàng."
        ),
        "tags": ["cat:architecture-decisions", "phaser3", "threejs", "typescript"],
        "evidence_ref": "package.json; ai-memory/quyet-dinh.md#93f-93h; CLAUDE.md",
    },
    {
        "key": "dc-arch-02",
        "type": "decision",
        "verify": True,
        "title": "Lưu trữ Offline-first IndexedDB + Đồng bộ Google Drive theo rev & merge không mất dữ liệu",
        "body": (
            "Game luôn đọc/ghi trên máy trước (IndexedDB), không bắt buộc đăng nhập hay có mạng. "
            "Đồng bộ Google Drive (scope drive.appdata) chạy nền theo từng file có phiên bản rev; khi hai máy cùng đổi phải gộp qua src/save/merge.ts "
            "(sao lấy max, đồ chỉ tăng, không ghi đè mất tiến độ). Mọi đổi schema save phải có migration trong src/save/migrate.ts + fixture."
        ),
        "tags": ["cat:architecture-decisions", "indexeddb", "google-drive", "save-merge"],
        "evidence_ref": "CLAUDE.md; ai-memory/quyet-dinh.md",
    },
    {
        "key": "dc-arch-03",
        "type": "decision",
        "verify": True,
        "title": "Ngoại lệ Trang trại 3D không dùng Tủ đồ & dừng hẳn nhánh 2.5D",
        "body": (
            "Nhánh nâng cấp 2.5D (?anim25d) đã dừng hẳn (bước 92b). Đảo Khủng Long Tập 3 giữ 2D. "
            "Khu Trang trại 3D dùng 7 cún 3D có bộ đồ trang trại cố định riêng (Marshall: mũ rơm dải đỏ + khăn quàng đỏ chấm bi), "
            "không áp dụng ghép lớp trang bị Tủ đồ; quà vẫn vào Tủ đồ cho các màn 2D."
        ),
        "tags": ["cat:architecture-decisions", "farm-3d", "outfit-exception"],
        "evidence_ref": "ai-memory/quyet-dinh.md#92b-93i; CLAUDE.md",
    },
    # 3. Game Requirements
    {
        "key": "dc-req-01",
        "type": "requirement",
        "verify": True,
        "title": "Quy tắc sư phạm, an toàn trẻ em và giọng đọc tiếng Việt bắt buộc",
        "body": (
            "Toán phải chính xác tuyệt đối qua src/math/generator.ts (0..20, không âm, tôn trọng carry, có seed). "
            "Không phạt bé (không mất mạng, không màn thua, luôn >= 1 sao, gợi ý 3 bậc). "
            "Mọi câu hỏi/thoại có MP3 sinh sẵn (voice/lines.json, voice/profiles.json) + nút loa nghe lại. "
            "Không quảng cáo, không IAP, không tracking; micro chỉ dùng ở M29 Việc nhà sau cổng phép nhân phụ huynh."
        ),
        "tags": ["cat:game-requirements", "kids-safety", "math", "voice-vi"],
        "evidence_ref": "CLAUDE.md; ai-memory/quyet-dinh.md#4-7",
    },
    {
        "key": "dc-req-02",
        "type": "requirement",
        "verify": True,
        "title": "Quy tắc Đảo độc lập, Lối vào Bạn Thú chung & 4 nhóm Tủ đồ trên mọi bản đồ 2D",
        "body": (
            "Đảo Khủng Long mở ngay từ đầu (không yêu cầu hoàn thành chương 7 Vịnh Phiêu Lưu). "
            "Mọi bản đồ Phaser phải dùng addMapUtilities (src/ui/map-utilities.ts) gồm shortcut FriendsHome (addFriendsShortcut) "
            "và áp dụng đủ 4 nhóm Tủ đồ: tools (treat/magnifier/shield), fx & trail (celebrate/trailBurst), frame (drawFrame), ring (ringOnce)."
        ),
        "tags": ["cat:game-requirements", "islands", "friends-home", "cosmetics"],
        "evidence_ref": "AGENTS.md; CLAUDE.md; ai-memory/quyet-dinh.md",
    },
    {
        "key": "dc-req-03",
        "type": "requirement",
        "verify": True,
        "title": "Yêu cầu Trang trại 3D: tăng tốc toán trần 50%, đảo nối cầu, đi qua/chặn & đường sắt",
        "body": (
            "Cây lớn theo giờ thực (không héo). Chạm bong bóng -> Tăng tốc -> toán ngẫu nhiên +30 phút/lượt, trần 50% thời gian gốc (cà rốt 2 lượt, ngô 4 lượt). "
            "Hệ thống đảo nối cầu, lưới đặt đồ 0.5m, nhấn giữ-kéo để dời, tay nắm xoay, giữ nền trống orbit 360 độ, double tap đổi Đi/Chạy. "
            "Mọi vật catalog phải có nhãn đi qua được/chặn lối (luống/đường/hoa pass; nhà/chuồng/giếng/đá/rào block)."
        ),
        "tags": ["cat:game-requirements", "farm-3d", "boost-cap", "collision-rules"],
        "evidence_ref": "ai-memory/quyet-dinh.md#93p-93ze",
    },
    # 4. Implemented Features
    {
        "key": "dc-feat-01",
        "type": "note",
        "verify": True,
        "title": "Tính năng 2D hoàn thiện: 7 Chương (56 màn), Sân chơi 8 trò Noel, Đảo Khủng Long 1 & 2, Vườn Bạn Thú",
        "body": (
            "Đã triển khai đầy đủ 7 chương/56 màn Vịnh Phiêu Lưu; Sân chơi 8 trò (162 màn, 36 quà, Mức 4 Noel); "
            "Đảo Khủng Long Tập 1 & Tập 2 Núi Lửa Ngủ Say (Phần 1+2, 60 xương, hộp quà 3D); Vườn Bạn Thú 3D (Ba Sừng + Gia đình Kèn + bé Tân binh tương tác); "
            "77 tổ hợp áo cún, 32 phương tiện (gồm 3 xe Blender 3D), Cửa hàng, Việc nhà M28/M29, Khu phụ huynh M19."
        ),
        "tags": ["cat:implemented-features", "2d-core", "playground", "dino-island", "friends"],
        "evidence_ref": "AI-MEMORY.md#80-88",
    },
    {
        "key": "dc-feat-02",
        "type": "note",
        "verify": True,
        "title": "Tính năng Trang trại 3D mới nhất (PR #123-#132): Đường sắt 6 ga, Sân chơi 3D 9 trò, Chọn nhóm & 6 quán ăn",
        "body": (
            "Đã merge tới PR #132 (HEAD b9eb1e6): Đường sắt 3 đảo 6 trạm dừng (F13.5 PR #124), 4 toa tàu màu riêng + âm thanh còi/chug (F13.4 PR #123), "
            "chọn ghế & lên/xuống tàu quanh ga bán kính 5m tự động dừng (PR #126, #127); Sân chơi 3D 9 trò di chuyển được (F13.6-F13.8 PR #125, #128); "
            "Chọn nhóm nhân vật (group selection) với 7 bạn đi theo xếp đội hình riêng biệt và 6 quán ăn có nội thất 3D, thực đơn riêng, tư thế ăn theo rig (PR #132); "
            "tích hợp Chase 3D Tripo (PR #71)."
        ),
        "tags": ["cat:implemented-features", "farm-3d", "railway", "group-selection", "dining-island", "playground-3d"],
        "evidence_ref": "git-commits:b9eb1e6,ed3cc34,64b09a3,96fbe99,3a7605f,025cc12",
    },
    # 5. Known Bugs (kept as candidate until verified on latest HEAD)
    {
        "key": "dc-known-bug-01",
        "type": "note",
        "verify": False,
        "title": "Các test E2E cũ flow.spec.ts hỏng từ trước & tồn đọng kiểm tra thực tế máy S20/A17",
        "body": (
            "Ghi nhận tại ai-memory/viec-tiep-theo.md: tests/e2e/flow.spec.ts các dòng :165, :176/:197, :219, :238/:265/:300 "
            "bị lỗi cũ cần thẻ QA dọn (phân định lỗi test hay lỗi game). Ngoài ra hiệu năng RAM/FPS thực tế khu 3D trên máy Samsung S20/A17 "
            "và kiểm tra offline/audio hoàn chỉnh cần xác nhận trực tiếp trên thiết bị thật."
        ),
        "tags": ["cat:known-bugs", "e2e", "samsung-s20", "needs-verification"],
        "evidence_ref": "ai-memory/viec-tiep-theo.md#45; ai-memory/kinh-nghiem-trang-trai-3d.md",
    },
    # 6. Historical Bug Fixes
    {
        "key": "dc-bugfix-01",
        "type": "bugfix",
        "verify": True,
        "title": "Sửa lỗi đường sắt lên/xuống tàu, vùng ga 5m và giữ chỗ ngồi (PR #126, #127)",
        "body": (
            "Fix commit 52cb61e, f937f1c, 7f28a67, 1d61f07, 75a6007: dùng khoảng cách tới cả đầu máy lẫn toa tàu để lên/xuống tàu; "
            "mở rộng vùng tiếp cận ga 5m tự động dừng (auto-dock) quanh vòng ray có xử lý wrap-around; "
            "giữ nguyên đặt chỗ ghế (seat reservations) và danh tính hành khách khi lên tàu tại ga."
        ),
        "tags": ["cat:historical-bug-fixes", "railway", "boarding", "disembark", "seat-picker"],
        "evidence_ref": "PR#126; PR#127; commits:52cb61e,f937f1c,1d61f07,75a6007",
    },
    {
        "key": "dc-bugfix-02",
        "type": "bugfix",
        "verify": True,
        "title": "Sửa thanh công cụ đường sắt che bộ chọn nhân vật & phân bổ 7 vị trí đội hình (PR #132)",
        "body": (
            "Commit 593d00c: ngăn thanh công cụ đường sắt (railway toolbar) đè lên bộ chọn nhân vật (character selector). "
            "Commit 1b9b3b6: phân bổ đủ 7 nhân vật đi theo (followers) vào các vị trí đội hình tách biệt tránh chồng nhau. "
            "Commit 5652bb0: giữ lối vào quán ăn thông thoáng, cải thiện close-up và chỉ báo nhóm."
        ),
        "tags": ["cat:historical-bug-fixes", "character-selector", "group-selection", "ui-overlap"],
        "evidence_ref": "PR#132; commits:593d00c,1b9b3b6,5652bb0",
    },
    {
        "key": "dc-bugfix-03",
        "type": "bugfix",
        "verify": True,
        "title": "Sửa lỗi lịch sử Trang trại 3D: rò rỉ Mesh ghostCenter, footprint sai, slop tap, làm tròn đích & merge xương",
        "body": (
            "F05d (93l): xóa entry Map ghostCenter khi FX thu hoạch kết thúc tránh rò 3 Mesh/lượt. "
            "F09 (93r/93u/93zd): sửa kiểm chồng 2 trục AND thay OR, khóa double-boost callback, bind expectedSowId chống discard xóa sow mới, "
            "kiểm khoảng cách trước thu hoạch, sửa mergeSave không mất xương khi spent=0 và chống self-merge đảo vị trí giếng. "
            "F11/F12 (93w/93za/93zb): dùng bbox thực tế cho footprint, hủy tap khi drag vượt slop 8px, giữ exact goal không làm tròn làm mất thao tác thu hoạch, "
            "tách findNearestWalkablePoint để cún thoát khi vật đặt đè lên cún."
        ),
        "tags": ["cat:historical-bug-fixes", "farm-3d", "mesh-leak", "navigation", "save-merge"],
        "evidence_ref": "ai-memory/kinh-nghiem-trang-trai-3d.md#93l-93zj",
    },
    {
        "key": "dc-bugfix-04",
        "type": "bugfix",
        "verify": True,
        "title": "Sửa lỗi UI 2D Khủng Long, âm thanh tàu khi ẩn app và khôi phục sân chơi ngoài biên biển",
        "body": (
            "Commit 8cdddc6: dừng tiếng còi/chug tàu khi ứng dụng chạy nền (backgrounded). "
            "Commit 1244e26 (PR #128): khôi phục các trò chơi sân chơi 3D nằm ngoài biên điều hướng biển. "
            "Commit ef41cf6 & 9a4be39: lưu vị trí đồ chơi sau khi kéo trước khi ghi profile, chặn di chuyển đồ chơi đang có người dùng. "
            "UI 2D (04/11): hủy nút/hitbox pha cũ chống chồng nút cửa nhạc, căn alpha/eo Tân binh, tách vùng nút không che nhân vật."
        ),
        "tags": ["cat:historical-bug-fixes", "audio", "playground-3d", "ui-2d"],
        "evidence_ref": "commits:8cdddc6,1244e26,ef41cf6,9a4be39; document/kinh-nghiem-ui-va-phoi-hop.md",
    },
    # 7. Regression Rules
    {
        "key": "dc-reg-01",
        "type": "requirement",
        "verify": True,
        "title": "Quy tắc chống hồi quy UI, hình ảnh và phối hợp đa phiên bắt buộc",
        "body": (
            "Không coi build/E2E thao tác đạt là bằng chứng không che hình: phải kiểm cả vùng nhìn thấy + vùng chạm (hitbox >= 44px CSS / 88px logic) "
            "trên 3 kích thước (desktop 1280x800, điện thoại ngang 844x390, tablet 1024x768), gồm cả trạng thái mở lại có tiến độ và sau tương tác/orbit. "
            "Không dời toàn bộ nút sang một góc mà chưa kiểm từng cảnh. Khi nhiều phiên chạy song song, mỗi tệp chỉ có 1 chủ sửa; "
            "đọc lại tệp chung ngay trước khi vá."
        ),
        "tags": ["cat:regression-rules", "ui-qa", "hitbox", "multi-session"],
        "evidence_ref": "AGENTS.md; document/kinh-nghiem-ui-va-phoi-hop.md",
    },
    # 8. 3D Asset Specifications
    {
        "key": "dc-3d-spec-01",
        "type": "requirement",
        "verify": True,
        "title": "Quy chuẩn mỹ thuật 3D Toy World & Danh mục metadata tài nguyên game",
        "body": (
            "Chuẩn Blender -> Three.js: 1 unit = 1m, gốc z=0 giữa đáy, nhân vật nhìn -Y trong Blender (+Z Three.js). "
            "Ngân sách: cún <= 12.000 tris, 28-40 xương, GLB <= 3MB, texture 512-1024; cây/thú nhỏ <= 2.000 tris; công trình <= 6.000 tris; "
            "toàn cảnh <= 150MB RAM thêm, FPS >= 30 trên A17/S20. Kiểm render dùng môi trường xám #C8CCD2 <= 0.4; luôn đọc JSON GLB sau xuất. "
            "Thống kê metadata hiện có (không index binary): 118 .glb (96.9MB), 19 .gltf (38.8MB), 154 .blend (86.3MB), "
            "3.664 .png (2.12GB), 1.760 .webp (76.3MB), 5.741 .mp3 (94.1MB), 84 .webm (145.5MB), 67 .py scripts, 661 .ts files."
        ),
        "tags": ["cat:3d-asset-specifications", "blender", "glb", "asset-catalog"],
        "evidence_ref": "document/trang-trai-3d/style-guide-3d.md; local-inventory:2026-11-25",
    },
    # 9. Testing & Deployment
    {
        "key": "dc-test-deploy-01",
        "type": "note",
        "verify": True,
        "title": "Quy trình kiểm thử (Vitest, Playwright) và phát hành Cloudflare Pages",
        "body": (
            "Build: `npm run build` (chạy `prebuild` stage-f104-thumbnails -> `tsc && vite build`). "
            "Unit tests: `npm test` (`vitest run`). E2E: `npm run test:e2e` (`playwright test`, có các config riêng cho farm/dino/chase/drive). "
            "Deploy production: `npm run deploy` (`wrangler pages deploy dist --project-name doi-cun-sieu-tri-tue --branch main`). "
            "Dev không SW cho thử nghiệm: `npm run dev` hoặc `npm run dev:dino` (port 5197). Không tự deploy production khi chưa có lệnh chủ dự án."
        ),
        "tags": ["cat:testing-deployment", "vitest", "playwright", "cloudflare-pages"],
        "evidence_ref": "package.json; CLAUDE.md",
    },
    # 10. Pending Tasks (candidate until confirmed current)
    {
        "key": "dc-pending-01",
        "type": "note",
        "verify": False,
        "title": "Các hạng mục tồn đọng & kế hoạch mở rộng Trang trại 3D / PWA",
        "body": (
            "1) Đo hiệu năng RAM/FPS/cảm ứng thực tế trên Samsung S20 và Galaxy A17 cho khu Trang trại 3D. "
            "2) Giai đoạn tiếp theo Trang trại 3D: chăn nuôi, chế biến nông sản (bếp có giới hạn ô/đảo), xây thành phố nối cầu. "
            "3) Tách gói PWA precache theo chương (giảm tải lần đầu). "
            "4) Dọn các ca E2E cũ hỏng trong tests/e2e/flow.spec.ts. "
            "5) Kiểm tra giọng đọc & thu âm micro Việc nhà M29 trên máy thật."
        ),
        "tags": ["cat:pending-tasks", "roadmap", "samsung-s20", "farm-3d"],
        "evidence_ref": "ai-memory/viec-tiep-theo.md; ai-memory/quyet-dinh.md#93ze-93zg",
    },
    # 11. Agent Experiences
    {
        "key": "dc-exp-01",
        "type": "experience",
        "verify": True,
        "title": "Kinh nghiệm kỹ thuật từ các phiên agent: PowerShell UTF-8, static publicDir, GLB JSON & gesture priority",
        "body": (
            "1) PowerShell pipe sang Node làm mất dấu tiếng Việt nếu chưa đặt OutputEncoding UTF-8 -> dùng file UTF-8 không BOM hoặc apply_patch. "
            "2) Đường dẫn /voice/farm trả HTML fallback vì publicDir=static -> luôn dùng voiceUrl từ module data/voice, không chỉ check HTTP 200. "
            "3) Xuất GLB từ Blender: hàn đỉnh trước Subdivision, đặt active object trước modifier_apply, bật use_fake_user cho Action, luôn đọc JSON GLB sau xuất. "
            "4) Thứ tự ưu tiên cử chỉ 3D: UI > pinch > rotate > hold/drag > double tap > single tap."
        ),
        "tags": ["cat:agent-experiences", "powershell-utf8", "blender", "threejs-input"],
        "evidence_ref": "ai-memory/kinh-nghiem-trang-trai-3d.md; document/kinh-nghiem-ui-va-phoi-hop.md",
    },
    # 12. Reusable Workflows
    {
        "key": "dc-workflow-01",
        "type": "note",
        "verify": True,
        "title": "Các quy trình & script tái sử dụng trong dự án Đội Cún Siêu Trí Tuệ",
        "body": (
            "- Vẽ lại tài nguyên 2D SVG: sửa `tools/art/*.mjs` -> chạy `npm run assets` (`node tools/draw-assets.mjs`), xem `assets/preview.html`. "
            "- Sinh giọng đọc tiếng Việt MP3: sửa `voice/lines.json` / `voice/profiles.json` -> chạy `npm run voice` (`node tools/make-voice.mjs`). "
            "- Chạy Blender headless & kiểm tra GLB: `npm run blender` (`tools/blender/run.mjs`) và `node tools/farm/gltf-stat.mjs`. "
            "- Chuyển giao việc giữa agent: dùng AI Memory Hub MCP (`get_project_context`, `search_code`, `search_memory`, `save_task_result`, `get_handoff`) trước khi mở nhiều file."
        ),
        "tags": ["cat:reusable-workflows", "tools", "blender", "voice", "search-first"],
        "evidence_ref": "package.json; CLAUDE.md; AI-MEMORY.md",
    },
]


def main() -> None:
    env = load_env(HUB_ROOT / ".env")
    base_url = f"http://127.0.0.1:{env.get('BRIDGE_PORT', '8082')}"
    write_tok = env["BRIDGE_WRITE_TOKEN"]
    admin_tok = env["BRIDGE_ADMIN_TOKEN"]

    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    checkpoint: dict[str, Any] = {}
    if CHECKPOINT_FILE.is_file():
        checkpoint = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))

    created = 0
    verified = 0
    candidates = 0
    for item in MEMORIES:
        key = item["key"]
        payload = {
            "project_id": PROJECT_ID,
            "worktree_id": WORKTREE_ID,
            "type": item["type"],
            "title": item["title"],
            "body": item["body"],
            "tags": item["tags"],
            "source_agent": "lead-bootstrap",
            "source_commit": HEAD_COMMIT,
            "evidence_ref": item["evidence_ref"],
            "idempotency_key": f"bootstrap:{key}:v1",
        }
        res = api_call(f"{base_url}/v1/memories", write_tok, payload)
        mem_id = res["memory_id"]
        created += 1
        final_state = res.get("state", "candidate")
        if item["verify"]:
            vres = api_call(f"{base_url}/v1/memories/{mem_id}/verify", admin_tok, {"action": "verify"})
            final_state = vres["state"]
            verified += 1
        else:
            candidates += 1
        checkpoint[key] = {"memory_id": mem_id, "state": final_state, "tags": item["tags"]}
        CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"total": created, "verified": verified, "candidate": candidates}, ensure_ascii=False))


if __name__ == "__main__":
    main()
