# DCS Triage Bot · STAGING

Bot đọc ticket trong **Base sandbox**, tra cứu dữ liệu **staging** (chỉ đọc), trả lời trong thread của **group sandbox**. Không đụng Base, group hay automation DCS thật.

| | |
|---|---|
| Base sandbox | `DCS Ticket [STG Sandbox]` · https://droppiivn.sg.larksuite.com/base/AHDmbnt6eaAa6asZLHAlfOmkgpd |
| Group sandbox | `DCS Sandbox (STG)` · `oc_1861966f1b33af544be0bfadd6ebe6d3` |
| Lark app | `cli_aa6193fa00f8df02` (app lark-cli của chị, danh tính bot) |
| Não | `claude -p` (login Claude của chị), model `sonnet` (đổi bằng `DCS_MODEL=haiku`) |
| Đọc từ prod | chỉ bảng Schedule (lịch trực), để ghi tên người sẽ được chuyển |

## Chạy

```sh
export PATH="$HOME/.nvm/versions/node/$(ls $HOME/.nvm/versions/node | tail -1)/bin:$PATH"
cd ~/dcs-bot
python3 bot.py --once                          # xử lý 1 lượt
python3 bot.py                                 # chạy liên tục, 60 giây/lượt
python3 bot.py --dry-ticket data/x.json        # chỉ chạy phần não, không ghi gì lên Lark
python3 test_bot.py                            # test logic, không gọi mạng
python3 tools/check_sandbox.py /tmp/canary     # kiểm lớp chặn: đọc file ngoài vùng, ghi DB
```

Cần tunnel DB staging (cloudflared `127.0.0.1:15431`) đang chạy và lark-cli đã login.

## Bot làm gì với 1 ticket

1. Ticket mới (cột `Bot Status` trống) → tìm thẻ do automation đăng trong group sandbox; sau 3 phút không thấy thì bot tự đăng.
2. Trả lời "đang kiểm tra", gọi não, trả lời trong thread, ghi các cột `Bot Status`, `AI Type`, `AI Priority`, `AI Note`, `Bot Thread`.
3. Điều tra theo flow dev: **scan source BE → trace DB (service + Odoo) → trace New Relic**. Chỉ kết luận khi cả 3 bước khớp (100%); code của bot kiểm điều này, không dựa vào điểm model tự chấm. Thiếu bước nào → "Chưa kết luận", chuyển người trực kèm bằng chứng.
4. Thiếu thông tin → chỉ hỏi mục bắt buộc, `Chờ người báo`; người báo trả lời trong thread → bot chạy lại.
4. Lỗi → `Lỗi bot` + nhắn "người trực xử lý theo cách thường".

## An toàn

- Code chặn mọi lệnh ghi ngoài Base sandbox / group sandbox (`lark_write`).
- Não chỉ có tool đọc: DBHub `readonly=true`, 100 dòng, 15 giây (giống VM); Sentry/Bugsnag/New Relic chỉ tool đọc; Read/Grep chỉ trong `knowledge/` và `droppii-odoo`.
- Câu trả lời đi qua bộ che SĐT, email, CCCD, mật khẩu/token trước khi đăng.
- Mọi lần chạy lưu toàn bộ tool call/SQL vào `logs/` (nhật ký kiểm tra).

## Chưa làm (vòng sau)

Nhắc +4h / đóng +1 ngày, nút bấm trên thẻ, watchdog, ngân sách AI, gom ticket trùng, chạy nền bằng launchd.
