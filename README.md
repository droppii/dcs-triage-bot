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
   1. Scan source BE      (mono-backend, droppii-odoo)
   2. Trace DB            (Postgres staging qua DBHub read-only, gồm DB Odoo)
   3. Trace New Relic     (TransactionError → Transaction → Log của app vn-stg-*-service)
        │
        ▼
bot.py tự quyết (không tin điểm model tự chấm):
   • đủ 3/3 khớp            → trả lời (A tra cứu / B đúng thiết kế) hoặc chuyển (C bug / D chỉnh dữ liệu)
   • thiếu bước nào         → "Chưa kết luận", chuyển người trực kèm bằng chứng + bước tiếp theo
   • không có mã tham chiếu → chỉ hỏi phần bắt buộc; ticket High vẫn chuyển người trực ngay
        │
        ▼
Trả lời trong thread + ghi cột Bot Status / AI Type / AI Priority / AI Note / Bot Thread
```

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
- **Nhật ký:** mọi lần chạy lưu toàn bộ tool call và SQL vào `logs/` (không commit).

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `bot.py` | Vòng lặp: Lark ↔ Base sandbox, gọi `claude -p`, cổng 100%, ghép câu trả lời |
| `agent/RULES.md` | Luật cho model: phân loại, priority, quy trình điều tra, **bản đồ tra cứu** (tên app/bảng đã kiểm chứng) |
| `agent/schema.json` | Định dạng kết quả, gồm `evidence` cho 3 bước |
| `config.json` | ID sandbox, nguồn lịch trực, model, repo tri thức |
| `cases/` | Case thử có kết quả mong đợi |
| `tests/test_bot.py` | Test logic, không gọi mạng |
| `tools/setup.sh` | Clone tri thức (chỉ đọc) + cài DBHub |
| `tools/dbhub-stg.py` | Chạy DBHub trên Postgres staging, read-only |
| `tools/check_sandbox.py` | Kiểm lớp chặn quyền |
| `tools/stg_case.py` | Tạo dữ liệu thử trên staging bằng tài khoản QA |
| `tools/bot_fields.json` | Các cột bot cần trong Base sandbox |

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

python3 tests/test_bot.py                                # test logic
python3 tools/check_sandbox.py                           # kiểm lớp chặn quyền
python3 bot.py --dry-ticket cases/stg-voucher-ended.json # chạy phần não trên 1 case, không ghi gì
python3 bot.py --once                                    # 1 lượt trên sandbox (chỉ owner sandbox)
```

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

## Còn thiếu

- Nguồn staging: Redis, MongoDB (chat/OpenIM), Elasticsearch, Kafka/Service Bus, log pod K8s. Thiếu creds nên các ticket cần chúng luôn "Chưa kết luận".
- Luồng sau: nhắc +4 giờ / đóng +1 ngày, nút bấm trên thẻ, watchdog, ngân sách AI, chạy nền (launchd), bộ chạy đánh giá tự động cho `cases/`.
- Hạ tầng: app Lark riêng cho bot (đang mượn app lark-cli của owner), tài khoản AI của công ty (đang dùng login Claude cá nhân), chạy trên VM agent của Engineering.

Đóng góp: xem [CONTRIBUTING.md](CONTRIBUTING.md).
