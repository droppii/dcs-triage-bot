# Chuyển sang PROD: tiêu chí và checklist

Hiện bot chỉ chạy staging (quyết định của Như, 06/11/2026). Tài liệu này là **đề xuất** để chuyển có kiểm soát, cần Như và Head of Engineering duyệt trước khi bot đọc dữ liệu production.

## 1. Cái nào đã tách khỏi môi trường

| Việc | Ở đâu | Khi sang PROD |
|---|---|---|
| Tên môi trường, nhãn `[STG]`, tiền tố app New Relic, tiền tố schema DB, nhánh code, câu "theo dữ liệu staging" | `config.json` → `env` | Đổi giá trị. `agent/RULES.md` lấy từ đây (`{ENV_NAME}`, `{NR_PREFIX}`, `{DB_PREFIX}`, `{CODE_BRANCH}`, `{DATA_NOTE}`) |
| Điều kiện kết luận theo loại | `config.json` → `gate` | Giữ hoặc siết |
| Kết nối DB | `tools/dbhub-stg.py` (`DATABASES`, tên DB `vn_stg_*`) | **Viết bản riêng cho PROD**, xem mục 3. Không sửa file staging |
| Repo/nhánh code | `config.json` → `knowledge` | Đọc đúng bản đang chạy, xem mục 3 |
| Đích ghi (Base + group) | `config.json` → `sandbox`, `lark_write()` | Xem mục 4 |

Còn hai chỗ vẫn nhắc staging trong `RULES.md` mà chưa là biến: danh sách database trong "Nguồn dữ liệu được phép" và các ví dụ giờ `2026-11-06`. Khi viết bản PROD, rà lại toàn bộ file.

## 2. Tiêu chí "oke" (đề xuất, chỉnh theo thực tế)

Đo bằng `tools/eval.py`, chạy mỗi case ≥ 5 lần, trên ≥ 10 case trong đó ≥ 5 case **bug có đáp án** (dựng từ bug Jira đã đóng có PR fix) và ≥ 2 case sentinel.

| Chỉ số | Ngưỡng đề xuất |
|---|---|
| Bịa trích dẫn (`no_fabrication` fail) | 0 trên mọi lần chạy |
| Kết luận sai (verdict ngoài `verdict_any_of`, hoặc dính `forbidden_conclusions`) | 0 trên case sentinel và case "đúng thiết kế" |
| Case bug có đáp án: trích đúng file và dòng chồng khoảng đáp án | ≥ 4/5 lần chạy cho ≥ 70% số case |
| Trả lời "Chưa kết luận" khi có thể kết luận | theo dõi, không chặn |
| Chi phí trung bình / ticket | ≤ ngân sách đã duyệt (hiện trần `max_budget_usd` = 1,5 USD) |
| Số lần gọi tool | ≤ 40 ở ≥ 90% lần chạy |

Chưa có con số nào ở trên được đo: hiện **chưa có case bug có đáp án**.

## 3. Việc kỹ thuật phải làm trước khi chạm dữ liệu PROD

**Dữ liệu cá nhân (rủi ro lớn nhất).** Staging không có khách thật, PROD có.
- [ ] DBHub PROD dùng tài khoản **read-only trên replica**, và chỉ thấy các bảng/cột đã duyệt (view đã bỏ SĐT, địa chỉ, CCCD, ngân hàng) thay vì cả schema. `max_rows`/`query_timeout` chỉ giới hạn khối lượng, không che dữ liệu.
- [ ] Bật `"redact_logs": true`: `logs/*.jsonl` lưu kết quả tool nguyên văn. Quyết định thời hạn lưu và nơi lưu (thư mục cục bộ không phù hợp cho PROD).
- [ ] `sanitize()` (bộ che SĐT/email/CCCD/token) chỉ chạy trên câu trả lời và ghi chú. Rà lại với mẫu dữ liệu PROD thật (tên, địa chỉ, tài khoản ngân hàng chưa được che).
- [ ] Case/fixture lấy từ ticket thật phải ẩn danh (mã đơn, tài khoản, SĐT) trước khi commit.

**Đúng phiên bản code.** `file:dòng` sai phiên bản trông vẫn hợp lý, nguy hiểm hơn là không có.
- [ ] Xác định cách lấy SHA/nhánh đang chạy trên PROD (deployment marker New Relic, tag image, hoặc nhánh release) và đặt `knowledge[].branch` cho đúng. `rca.code_version` ghi kết quả đối chiếu cho từng ticket.
- [ ] Nếu bản đang chạy cũ hơn lịch sử đã clone (`history_days`), tăng `history_days` hoặc ghi rõ "ngoài lịch sử".

**Quan sát.**
- [ ] Xác nhận account New Relic, tên entity PROD, thời gian giữ log; log PROD có thể khác staging (ví dụ promotion-service).
- [ ] Kiểm chứng các cách lấy stack trace ghi trong `RULES.md` ("Tìm stack trace") trên New Relic thật và cập nhật mục đó.
- [ ] Chạy `tools/gen_anchors.py` trên mono-backend thật, so ≥ 10 route và ≥ 5 mã lỗi đã biết với `knowledge/_anchors/*.md`; sửa regex nếu thiếu.

## 4. Lộ trình chuyển (đề xuất)

1. **Hoàn thiện trên staging:** đạt tiêu chí mục 2.
2. **Chế độ bóng:** bot đọc ticket DCS thật nhưng chỉ ghi vào sandbox; người trực vẫn làm như cũ. So kết luận của bot với kết quả thật trong ≥ 2 tuần, ghi lại sai/đúng. *Chưa có trong code*: hiện bot đọc ticket từ chính Base sandbox. Cần thêm cấu hình nguồn ticket tách khỏi đích ghi, và giữ `lark_write()` khóa vào sandbox.
3. **Trả lời thật cho 1–2 Product** có người trực theo dõi và nút "bot sai" để đo.
4. Mở rộng dần; bất kỳ lần đổi `gate`/`RULES.md` đều chạy lại eval.

## 5. Quyết định cần người có thẩm quyền

- Cho phép bot đọc dữ liệu PROD đến mức nào (replica nào, bảng/cột nào). Hiện README ghi chỉ staging.
- Chính sách lưu `logs/` và dữ liệu gửi tới mô hình AI (tài khoản công ty thay vì login cá nhân).
- Cổng của loại C không còn đòi bước DB (khác quyết định Dev BE 06/11): cần Dev BE xác nhận.
- Có đọc bảng ticket DCS thật để tra ticket cũ (`prior_art`) hay không.
