# DCS Triage Bot

Bot trên Lark nhận ticket DCS trước người trực. Nó đọc ticket, tự điều tra **chỉ đọc** (code backend, database, New Relic), rồi:
- **trả lời luôn** nếu chắc chắn 100%,
- **hỏi lại** nếu thiếu thông tin bắt buộc,
- **chuyển người trực** kèm bằng chứng nếu chưa chắc hoặc là bug.

> **Trạng thái (06/11/2026):** chạy thử trên **staging + bản clone DCS**. Chưa gắn vào group DCS thật, chưa đọc dữ liệu production. Đề xuất gốc: [DCS Triage Bot](https://claude.ai/artifact/N5KavFdbhvS9JU4GCU3JMd). Owner: Đoàn Cẩm Như (QA).

## Bot xử lý một ticket thế nào

```
Ticket mới trong Base sandbox ──► automation gửi thẻ vào group sandbox
        │
        ▼  (mỗi 60 giây)
bot.py: nhận ticket ─► "đang kiểm tra" trong thread
        │
        ▼
claude -p + agent/RULES.md (chỉ có công cụ đọc)
   0. Thẻ triệu chứng     (chuỗi lỗi nguyên văn, mã, giờ; đọc cả ảnh đính kèm nếu có)
   1. Định vị code        (chỉ mục route/mã lỗi → handler, rồi đọc mono-backend / droppii-odoo)
   2. Dấu hiệu runtime    (New Relic TransactionError → Transaction → Log; DB staging qua DBHub read-only)
   3. Bảng giả thuyết     (≥2, gồm cả "không phải bug"; mỗi cái một phép kiểm rẻ nhất)
   4. Lịch sử + phiên bản (git blame/log qua `repogit`; so bản clone với mốc deploy New Relic)
        │
        ▼
bot.py kiểm lại, không tin model tự khai:
   • mọi file:dòng + đoạn code trích có khớp file thật trong knowledge/ không
   • câu trích log/DB có nằm trong kết quả tool thật của lần chạy không
   • không khớp → hạ trạng thái về "chưa kiểm"
        │
        ▼
Cổng kết luận theo loại (config.json "gate"):
   • C bug code  → dòng code lỗi + dấu hiệu runtime + cơ chế + đã loại giả thuyết khác
                   (+ verifier độc lập đọc lại bản ghi và cố bác bỏ). Không bắt buộc bước DB.
   • A / B       → code + DB cùng khớp (không cần New Relic)
   • D           → đủ 3/3: code, DB, New Relic cùng khớp
   • thiếu       → "Chưa kết luận", chuyển người trực KÈM ứng viên nguyên nhân (nếu đã có) + việc cần làm tiếp
   • không có mã tham chiếu → chỉ hỏi phần bắt buộc; ticket High vẫn chuyển người trực ngay
        │
        ▼
Trả lời trong thread (gói nguyên nhân gốc do bot dựng từ trường đã kiểm chứng, có link GitHub ghim theo SHA)
+ ghi cột Bot Status / AI Type / AI Priority / AI Note / Bot Thread
```

Gói cho người trực (loại C hoặc ứng viên chưa đủ bằng chứng) gồm: code `file:dòng` kèm đoạn trích và link, cơ chế, dấu hiệu runtime trích nguyên văn, chuỗi nhân quả, các giả thuyết đã xét, phạm vi ảnh hưởng, commit đưa vào (nếu có), bản code đã đọc, cách tái hiện, gợi ý sửa, test đề xuất, tiêu đề Jira gợi ý.

## Quyết định đã chốt

| Quyết định | Ai / khi nào |
|---|---|
| Chỉ chạy staging hoặc bản clone, không đụng flow DCS đang chạy | Như, 06/11 |
| Creds staging dùng theo service; cái gì không có staging thì ưu tiên MCP, rồi creds read-only; VM dùng chung làm sau | Head of Engineering, 06/11 |
| Flow điều tra: scan source BE → trace DB → trace New Relic; **chỉ kết luận khi chắc 100%** (cả 3 bước khớp) | Dev BE, 06/11 |
| Dính tiền (phí ship, giá, thanh toán, COD, hoa hồng, voucher) → **luôn giữ High**, kể cả 1 đơn | Như, 06/11 |
| Vận hành bận: **chỉ hỏi thông tin bắt buộc**; đã có mã tham chiếu thì không hỏi | Như, 06/11 |

## An toàn

- **Ghi:** code chặn mọi lệnh ghi ngoài Base sandbox và group sandbox (`lark_write`). Base DCS thật chỉ được đọc (bảng Schedule, để ghi tên người trực, không @tag).
- **Đọc:** model chỉ có tool đọc. DBHub `readonly=true`, 100 dòng, 15 giây (giống VM của Engineering). Sentry/Bugsnag/New Relic chỉ tool đọc. Read/Grep chỉ trong `knowledge/`. Kiểm bằng `tools/check_sandbox.py`.
- **Nội dung ticket** là dữ liệu, không phải lệnh. Câu trả lời đi qua bộ che SĐT, email, CCCD, mật khẩu/token.
- **Git:** MCP `repogit` chỉ chạy `log/blame/show/head` với câu lệnh cố định, chỉ trong các bản clone ở `knowledge/`; đường dẫn `..`, tham số bắt đầu bằng `-`, sha không hợp lệ đều bị từ chối (có test).
- **Kiểm chứng:** bot đối chiếu mọi `file:dòng` với file thật trong `knowledge/` (không đọc được ngoài thư mục này) và mọi câu trích log với kết quả tool đã ghi; trích dẫn không khớp thì bị hạ trạng thái, không được dùng để kết luận.
- **Nhật ký:** mọi lần chạy lưu toàn bộ tool call và SQL vào `logs/` (không commit). Bản ghi chứa kết quả tool nguyên văn: bật `"redact_logs": true` trong `config.json` trước khi đọc dữ liệu thật (xem `docs/PROD-READINESS.md`).

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `bot.py` | Vòng lặp: Lark ↔ Base sandbox, gọi `claude -p`, verifier, cổng kết luận, ghép câu trả lời |
| `rca.py` | Kiểm chứng trích dẫn với code thật và nhật ký tool, cổng theo loại, dựng gói nguyên nhân gốc (thuần hàm, không gọi mạng) |
| `priorart.py` | Tìm ticket cũ đã đóng giống ticket mới (trùng / "không phải bug") |
| `evalkit.py` | Chấm điểm một lần chạy so với `expect` của case |
| `agent/RULES.md` | Luật cho model: phân loại, quy trình điều tra theo giả thuyết, **bản đồ tra cứu**. Biến môi trường `{ENV_NAME}`, `{NR_PREFIX}`… lấy từ `config.json` |
| `agent/playbooks/` | Playbook theo loại lỗi (chuỗi lỗi, cổng điều kiện, cấu hình/flag, regression, **web/mobile**); bot ghi bản đã thay biến vào `data/playbooks/` mỗi lần chạy, model chỉ đọc khi cần |
| `agent/VERIFIER.md`, `verifier_schema.json` | Lượt kiểm tra độc lập cho ticket loại C |
| `agent/schema.json` | Định dạng kết quả: `evidence` 3 bước + `rca` (code_location, mechanism, decisive_signal, causal_chain, alternatives…) |
| `config.json` | ID sandbox, nguồn lịch trực, `env`, `gate`, `verifier`, ngân sách, ảnh đính kèm, prior art, repo tri thức |
| `cases/` | Case thử có kết quả mong đợi (`_TEMPLATE-bug-from-pr.json`: mẫu case bug có đáp án) |
| `evals/` | `baseline.json` (điểm chuẩn) và `fixtures/` (lần chạy đã đóng băng) |
| `tests/` | Test logic, không gọi mạng; `python3 tests/run_all.py` chạy tất cả |
| `tools/setup.sh` | Clone tri thức (chỉ đọc, kèm lịch sử git có giới hạn) + sinh anchors (`_anchors/`) + cài DBHub |
| `tools/gen_anchors.py`, `tools/anchors_client.py` | Sinh `knowledge/_anchors/`: backend (service, route → handler, mã lỗi, bảng, SHA đã clone) và client (chuỗi UI, lời gọi API, màn hình, cạnh client → backend, mã lỗi ↔ chuỗi UI) |
| `tools/repo_git_mcp.py` | MCP git chỉ đọc (`git_head/log/blame/show/tags/file`; `ref` để đọc code đúng phiên bản app) cho các bản clone |
| `tools/eval.py` | Chạy case N lần, chấm điểm, so baseline |
| `tools/eval_closed.py` | Giai đoạn 1a: phân loại ticket DCS đã đóng chỉ từ nội dung (không tool), so với RC Category của dev |
| `usage.py`, `tools/usage_report.py` | Ghi và tổng hợp token/chi phí mỗi lần phân tích |
| `tools/dbhub-stg.py` | Chạy DBHub trên Postgres staging, read-only |
| `tools/check_sandbox.py` | Kiểm lớp chặn quyền |
| `tools/stg_case.py` | Tạo dữ liệu thử trên staging bằng tài khoản QA |
| `tools/bot_fields.json` | Các cột bot cần trong Base sandbox |
| `docs/PROD-READINESS.md` | Tiêu chí và checklist trước khi chuyển PROD |

## Chạy thử

Cần: Python 3.9+, Node 18+, `git` có quyền org `droppii`, [`lark-cli`](https://www.npmjs.com/package/@larksuite/cli) đã login, Claude Code CLI đã login, tunnel Postgres staging.

```sh
git clone https://github.com/droppii/dcs-triage-bot.git && cd dcs-triage-bot
tools/setup.sh                       # clone knowledge/ (~500MB) + DBHub
cp .env.example .env                 # điền creds DB staging (hoặc trỏ tới file .env sẵn có)
# MCP cho Claude Code, mỗi người login bằng tài khoản của mình:
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
claude mcp add --transport http bugsnag https://bugsnag.mcp.smartbear.com/mcp
claude mcp add --transport http newrelic https://mcp.newrelic.com/mcp/
claude   # rồi gõ /mcp để login từng server

python3 tests/run_all.py                                 # test logic (không gọi mạng)
python3 tools/check_sandbox.py                           # kiểm lớp chặn quyền
python3 bot.py --dry-ticket cases/stg-voucher-ended.json # chạy phần não trên 1 case, không ghi gì
python3 bot.py --once                                    # 1 lượt trên sandbox (chỉ owner sandbox)

python3 tools/eval.py --runs 3                           # chấm cả bộ case (tốn tiền), so với evals/baseline.json
python3 tools/eval.py --from-log cases/X.json=logs/Y.jsonl   # chấm lại một lần chạy đã lưu, không gọi model
python3 tools/eval_closed.py data/<export>.ndjson --n 100  # giai đoạn 1a; export Base DCS để trong data/ (không commit)
```

Windows: dùng `python` thay `python3`; chạy `tools/setup.sh` bằng Git Bash. Đặt `PYTHONIOENCODING=utf-8` nếu terminal báo lỗi mã hóa.

**Mỗi sandbox chỉ nên có một người chạy `bot.py`**, nếu không bot sẽ trả lời trùng. Người khác dùng `--dry-ticket` hoặc tạo sandbox riêng.

### Tạo sandbox riêng

1. Copy cấu trúc Base DCS (không lấy dữ liệu): `lark-cli base +base-copy --base-token <DCS base> --name "DCS Ticket [STG Sandbox]" --without-content --time-zone Asia/Bangkok`. Lark từ chối `Asia/Ho_Chi_Minh`.
2. Tạo group topic: `lark-cli im +chat-create --as user --chat-mode topic --type private --name "DCS Sandbox (STG)" --bots <app_id>`.
3. **Trước khi tạo ticket nào:** mở Automations của bản copy, sửa "UPDATE - Notify new ticket" cho gửi tới group sandbox; giữ "Notify new ticket" tắt. API không đọc/sửa được loại automation này, phải làm trên giao diện.
4. Thêm cột bot: `lark-cli base +field-create --base-token <sandbox> --table-id <DCS Tickets> --as user --json @tools/bot_fields.json`.
5. Bảng Schedule của sandbox: thêm 1 dòng **chỉ có tên mình** (Person, BA-QA), để thẻ không @tag người thật.
6. Ghi ID vào một file config riêng và chạy với `DCS_CONFIG=my-config.json`.

## Kết quả thử 06/11/2026

| Case | Code BE · DB · New Relic | Bot làm |
|---|---|---|
| `stg-voucher-ended` | ✓ ✓ ✓ | Tự trả lời: voucher bị kết thúc thủ công, báo lỗi là đúng thiết kế |
| `stg-delivery-options-500` | ✓ · không thấy · ✓ | Chưa kết luận: thấy NPE trong New Relic và vị trí code, thiếu stack trace |
| `stg-stock-odoo-vs-erp` | ✓ · ngược · không thấy | Chưa kết luận, kèm số tồn Odoo và ERP |
| `stg-ship-fee-old-order` | ✓ · không thấy · không thấy | Chưa kết luận: đơn cũ, log đã hết hạn |
| `stg-voucher-no-code`, `stg-order-stuck-no-code` | (không điều tra) | Chỉ hỏi mã tham chiếu và thời điểm; ticket High vẫn chuyển người trực |

Chi phí khoảng $0,03–0,7 mỗi ticket (sonnet). Cùng một case có thể ra kết luận khác nhau giữa các lần chạy, nên khi đánh giá cần chạy nhiều lần.

### Giai đoạn 1a (06/11/2026): 100 ticket đã đóng, chỉ đọc nội dung

`tools/eval_closed.py`, 100 ticket đóng gần nhất có RC Category (21/08–28/10/2026), sonnet, $0,47. Chỉ từ nội dung, bot **không phân biệt được "Not a bug" với bug** (21/29 ticket Not a bug bị xếp là bug), nhưng an toàn: chỉ 1/24 bug thật bị xếp là tra cứu. Muốn bot tự trả lời thì phải có bằng chứng dữ liệu và runtime, không thể chỉ đọc chữ.

## Chi phí: cái gì thật sự tốn tiền (đo 10/11/2026)

Chi phí mỗi lần phân tích gần như tỷ lệ với **số lượt × đầu vào cố định mỗi lượt**, không phải với lượng code đọc. Đầu vào cố định là luật + định nghĩa tool của mọi MCP, và model đọc lại nó ở **mọi** lượt:

| Cấu hình MCP | Token cố định mỗi lượt |
|---|---|
| Tất cả (Sentry 73 tool, Bugsnag 19, New Relic 33, + DB, git) | **132k** |
| Sentry riêng | 86k |
| New Relic riêng | 32k |
| Bugsnag riêng | 26k |
| Chỉ `repogit` | vài k |
| Cắt tool không bao giờ dùng (`--disallowedTools`) | Sentry 86k → 23k, New Relic 32k → 20k, Bugsnag 26k → 16k |

Vì vậy bot chỉ bật MCP phù hợp với Product của ticket (`config.json` → `products.map.<Product>.mcp`: Bugsnag cho Biz/Mall App, Sentry cho Mall Web, backend chỉ New Relic + DB + git) và cắt các tool ngoài `READ_TOOLS` (danh sách tool tự khám phá từ sự kiện init của CLI, lưu `data/mcp_tools.json`). Cùng case voucher: 0,6-1,4M token và $0,32 mỗi lần chạy xuống 0,2-0,4M token và $0,10-0,18 (cache nóng).

**Verifier tắt mặc định** (`verifier.enabled: false`): mỗi lần chạy thêm $0,17-0,25 (có lần 600k token) và chưa đáng tin trên DMW-2203 (bắt đúng 1 lần, FAIL nhầm 1 lần đúng, PASS nhầm 1 lần sai). Việc chặn kết luận thiếu bằng chứng do `rca.py` làm bằng code, không tốn token.

**Không dùng chỉ mục mã nguồn** (đã thử rồi bỏ): một chỉ mục cục bộ SQLite FTS5 (tìm theo định danh/chuỗi lỗi, mỗi lần tra 600-750 token) cho độ đúng và chi phí tương đương việc Grep + đọc đúng đoạn, ở 6 lần chạy voucher và 8 lần DMW-2203. Nó còn thêm khoảng 181 MB, phải build lại khi repo đổi (hit cũ phải gắn STALE) và làm model dễ dừng ở hit đầu bảng (mã lỗi 4064 được ném ở nhiều handler, lần chạy có index chọn nhầm handler 2/3 lần). Cái quyết định độ đúng là quy trình điều tra (`RULES.md`, playbook) và bằng chứng runtime; cái quyết định chi phí là số lượt × đầu vào cố định mỗi lượt. Định vị code dùng `_anchors/` (route, mã lỗi, chuỗi UI, cầu nối client → route → handler) rồi Grep hẹp và Read đúng đoạn. Nếu sau này cần thử lại cho repo FE/mobile rất lớn thì đo trước bằng case có đáp án (`tools/eval.py`).

**DMW-2203 (Jira, bug BE mức High, đã sửa ở commit `c986f3c0`; `cases/dmw-2203-flash-sale-min-discount.json`)**: thiếu kiểm tra mức giảm tối thiểu 1.000đ ở Flash Sale PDP. Đáp án: `ValidateProductFlashSale` trong `DiscountCampaign.cs` không gọi `GetMinDiscountValue()`. Chạy trên bản clone lùi về trước commit sửa:

| Lần chạy | Vị trí bot trả | Verdict bot đăng |
|---|---|---|
| 4 lần đầu (chưa có playbook, có verifier) | 1/4 đúng; 3/4 trỏ vào `CreatePdpPortalDiscountCampaignCommandValidator` | 1 lần **`C` sai mà tự tin**, còn lại `?` |
| 4 lần sau (playbook `missing-rule.md`, dấu hiệu runtime phải từ tool runtime) | **4/4 đúng** | `?` (đúng: product-service không có New Relic ở staging, DB không kết nối được) |

Hai thay đổi sau lần đầu: (1) `decisive_signal` chỉ được tính khi chuỗi trích nằm trong kết quả của tool runtime (New Relic, DB, Bugsnag, Sentry), một dòng code vừa `Read` không còn được coi là "dấu hiệu runtime"; (2) playbook `agent/playbooks/missing-rule.md` bắt lần đủ mọi tầng (route → validator → domain → service) và tìm luồng anh em đang thực thi quy tắc. **Lưu ý:** playbook được viết sau khi thấy ticket này sai, nên 4/4 chưa phải kiểm chứng độc lập; cần thêm bug có đáp án khác.

Giới hạn còn lại: với loại bug "thiếu kiểm tra" ở service không có New Relic, bot chỉ có thể trả "ứng viên nguyên nhân", không bao giờ `C`, vì bot chỉ đọc và không được tạo dữ liệu để tái hiện.

## Backend nhiều repo, web và mobile

Bot đọc code ở **đúng repo của lớp có lỗi**, khai trong `config.json` → `knowledge` (mỗi repo có `layer`, `branch` riêng) và ghép Product → repo → công cụ lỗi client ở `products.map`:

| Lớp | Repo | Ghi chú |
|---|---|---|
| `be` | `mono-backend` (nhánh `dev`: 24 service C#, Java/Kotlin, TypeScript), `openim-server` (Go), `assistant-service`, `recommender-service` (Python) | Danh sách repo backend lấy từ handbook `templates/repository-context.md`. Nhánh mặc định khác nhau (dev hoặc main) |
| `odoo` | `droppii-odoo` | WMS |
| `mobile` | `biz-mobile` (Biz App), `customer-mobile` (Mall App) | Bugsnag. Mỗi người dùng một phiên bản (binary + bundle CodePush) |
| `fe` | `web-mall` (Mall Web), `b2b-client` (Biz Web), `dashboards` (PDP Portal, OBEFE), `erp-dashboard`, `gamification` (app web Next.js) | Sentry (web-mall); các web khác chưa rõ |

Ghép Product ↔ repo **suy ra từ handbook, chưa được Dev FE/Mobile xác nhận** (`confirmed: false`). Nhánh của repo FE/mobile là nhánh mặc định, chưa chắc là bản đang chạy trên staging.

`tools/gen_anchors.py` sinh `knowledge/_anchors/` để bot định vị mà không phải Grep mù: `services.md` (service → repo/thư mục, theo tiền tố gateway `/<tên>-service`), `routes.md`, `errors.md`, `tables.md`, và cho client: `ui-texts.md` (chuỗi UI → key i18n → component), `api-calls.md`, `screens.md`, **`edges.md`** (nơi client gọi API → route → handler ở backend, có `confidence`), `error-map.md` (mã lỗi backend ↔ chuỗi UI ↔ nơi client xử lý). Đây là cách làm tương đương "cạnh xuyên repo" của Brain trong brain-agent-kit, bằng chỉ mục tĩnh thay cho graph. Chỉ là chỗ định vị, không phải bằng chứng.

Lỗi mobile bắt buộc có `rca.client_version` (phiên bản app/bundle lấy từ Bugsnag/Sentry) mới được kết luận; `git_tags`/`git_file`/`git_blame ref` đọc code đúng bản đó và bot kiểm trích dẫn ở đúng `ref`.

**Đã kiểm chứng trên code thật (10/11/2026, clone nông các repo trên):** 24 service, 2.351 route, 644 hằng số mã lỗi, 12.892 chuỗi UI, 368 lời gọi API (phân giải cả `END_POINTS.X.Y`), 273 cạnh client → backend (61 `high`, 104 `medium`, 12 `low`, 96 chỉ biết service). Ví dụ `error-map`: `4064 PROMOTION_NOT_FOUND_OR_UNAVAILABLE` = "Khuyến mãi không tồn tại hoặc không khả dụng" nối tới `promotion-service` và nơi client xử lý. Chạy model thật trên case `stg-voucher-ended` chỉ từ chuỗi UI trong ticket: bot lần ra `ClaimVoucherCommandHandler.java:73-77` (đáp án 73-76), kèm link ghim SHA; dừng ở `?` vì máy thử không có DB staging (A/B đòi 3/3).

**Giới hạn đã biết** (xem cột `Routes found` trong `services.md`): bộ quét chưa hiểu route của `schedule-service`, `search-service`, `serverless`, `strapi-service` (0 route) và `api-gateway` (1), Go chỉ thấy route ghi trực tiếp (không giải nhóm `Group("/v1")`), FastAPI không giải `APIRouter(prefix)`. `web-mall` hard-code tiếng Việt trong component nên không có bảng key: Grep thẳng. Lời gọi Strapi (`${STRAPI_BASE_URL}/api/...`) không có controller tương ứng nên không nối được. Chưa chạy trên `droppii-odoo`, `dashboards`, `erp-dashboard`.

## Bằng chứng RCA và cách đo

- **Cổng theo loại ticket** nằm ở `config.json` → `gate`. Đây là thay đổi so với quyết định "cả 3 bước khớp" của Dev BE (06/11): với **bug code (C)** cổng không còn đòi bước DB (NPE trong New Relic + dòng code gây lỗi đã đủ nếu có cơ chế và đã loại giả thuyết khác). **A và B cũng chỉ còn đòi code + DB** (10/11, từ ticket D1110-011: giải thích phí thanh toán bằng công thức trong `PaymentMethod.java` và dữ liệu đơn, New Relic không có gì để chứng minh thêm mà vẫn bị ghi `?`); D vẫn đòi 3/3. Muốn quay lại luật cũ cho A/B: thêm `"newrelic"` vào hai danh sách đó. Muốn quay lại luật cũ cho C: đặt `"C": ["source_be", "db", "newrelic"]`. **Cần Dev BE xác nhận thay đổi này.**
- **Đo:** `tools/eval.py` chấm từng lần chạy (verdict, chuỗi bắt buộc, `file:dòng` đúng, không bịa trích dẫn, ngân sách tool/chi phí), chạy N lần vì cùng một case có thể ra kết luận khác nhau. Điểm 1.0 / 0.5 / 0.05; fail nếu một case tụt quá 0.2 hoặc điểm trung bình tụt so với `evals/baseline.json`.
- **Chưa có case bug có đáp án** (BE, FE lẫn mobile; mẫu: `cases/_TEMPLATE-bug-from-pr.json`, `cases/_TEMPLATE-fe-mobile-bug.json` có thêm `layer` và phiên bản app). 4 case hiện tại đều ra `?` hoặc "đúng thiết kế". Dựng case từ bug Jira đã đóng có PR fix theo `cases/_TEMPLATE-bug-from-pr.json` (đáp án `file:dòng` lấy từ diff của PR); đây là việc quan trọng nhất để biết bot có tìm đúng dòng code lỗi hay không.
- **Fixture:** log New Relic hết hạn sau ~30 ngày. `tools/eval.py --freeze case=log` lưu lần chạy tốt vào `evals/fixtures/`, `--from-fixtures` chấm lại miễn phí. Lưu ý: nó chấm lại câu trả lời đã ghi, không chạy lại agent trên kết quả tool đã ghi.

## Token và chi phí mỗi lần phân tích

Sau mỗi lần phân tích bot ghi lại số token đã tốn (lấy từ `modelUsage` của `claude -p`):
- **Cột `AI Note` của ticket:** `token: 1.416 vào · 5.136 ra (thinking 1.613) · cache đọc 280.830 / ghi 14.195 · tổng 301.577 · ~$0.040 · 4 lượt · 24s · <model>`. Nếu có verifier thì kèm phần chia brain/verifier, và `cả ticket: N lần chạy, X tok` khi ticket được phân tích nhiều lần (sau khi người báo trả lời). Run lỗi cũng ghi số đã tốn.
- **`logs/usage.jsonl`:** mỗi lượt chạy `claude -p` một dòng (ticket, vai trò brain/verifier, từng loại token, chi phí, số lượt, thời gian). Không chứa nội dung ticket. Gồm cả `--dry-ticket` và `tools/eval.py`.
- **Cột riêng (tùy chọn):** thêm cột `AI Usage` vào Base (`tools/bot_fields.json` đã có) rồi đặt `config.json` → `usage.field` = `"AI Usage"`; mặc định để trống vì ghi vào cột chưa tồn tại sẽ làm lệnh cập nhật ticket lỗi.
- **Tổng hợp:** `python3 tools/usage_report.py [--since 2026-11-01] [--ticket CODE]` cho tổng token/chi phí, trung bình mỗi ticket, theo vai trò, theo ngày, và các ticket tốn nhất.

Lưu ý: "tổng" gồm token vào, ra, ghi cache và đọc cache (thinking đã nằm trong token ra). Phần lớn token thường là cache đọc, giá rẻ hơn nhiều; vì vậy hãy nhìn `~$` (giá niêm yết do CLI ước tính, không phải hóa đơn). Trần mỗi lần chạy: `max_budget_usd`.

## Còn thiếu

- Nguồn staging: Redis, MongoDB (chat/OpenIM), Elasticsearch, Kafka/Service Bus, log pod K8s. Thiếu creds nên các ticket cần chúng luôn "Chưa kết luận".
- Luồng sau: nhắc +4 giờ / đóng +1 ngày, nút bấm trên thẻ, watchdog, chạy nền (launchd). Đã có: trần chi phí mỗi lần chạy (`max_budget_usd`) và bộ chấm điểm `tools/eval.py`.
- Đã viết nhưng **chưa kiểm chứng với hệ thống thật** (xem `docs/PROD-READINESS.md`): `tools/gen_anchors.py` chạy trên mono-backend thật (test mới dùng mã giả lập), cách lấy stack trace từ New Relic (`trace.id` → Log, `analyze_errors`), mốc deploy từ `list_change_events`, ảnh đính kèm (cần điền tên cột vào `config.json` → `attachments.fields`), tra ticket cũ (`prior_art`, tắt mặc định, cần quyết định đọc bảng ticket của Base DCS thật).
- Chưa làm: tra trùng ticket Jira qua Atlassian MCP (cần URL MCP và tên tool đọc để thêm vào `READ_TOOLS`), chế độ "bóng" đọc ticket PROD nhưng chỉ ghi vào sandbox.
- Hạ tầng: app Lark riêng cho bot (đang mượn app lark-cli của owner), tài khoản AI của công ty (đang dùng login Claude cá nhân), chạy trên VM agent của Engineering.

Đóng góp: xem [CONTRIBUTING.md](CONTRIBUTING.md).
