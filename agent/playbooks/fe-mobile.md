# Playbook: lỗi ở web (fe) hoặc mobile

Nguồn: bảng Product → repo ở RULES (Bước 1a) và handbook `engineering-knowledge` (platform.md mục 1.5, platform-fe.md). Mọi chỗ ghi "thử" là chưa kiểm chứng trên hệ thống thật.

## 1. Lỗi ở lớp nào
1. **Có request tới backend không?** New Relic `Transaction` của entity backend đúng service, khoảng ±10 phút quanh giờ báo, lọc `request.uri` theo API mà màn hình đó gọi (tra ở `{ANCHORS}/api-calls.md` / `edges.md`).
   - Không có request nào → lỗi chặn ở client trước khi gọi (validate, trạng thái UI, crash). Lớp `fe`/`mobile`.
   - Có request, 5xx → backend (`TransactionError`). Có request, 2xx → xem response/dữ liệu: đúng thì lỗi ở client, sai thì backend.
2. **Client có ghi lỗi không?** Bugsnag/Sentry (bước 2). Có lỗi JS/native cùng giờ, cùng người dùng/phiên bản → client.
3. Cùng một API, người dùng khác hoặc phiên bản khác vẫn ổn → bất đối xứng ở phiên bản app hoặc dữ liệu người dùng; so phiên bản (bước 3).

## 2. Lỗi client: Bugsnag / Sentry
- **Biz App, Mall App** dùng Bugsnag (project Droppii Biz / Droppii Mall); **web-mall** dùng Sentry (project `web-mall`, org droppii-kp). Lọc theo môi trường: `releaseStage` (Bugsnag) hoặc `environment` (Sentry) = {ENV_NAME}.
- Lấy: stack frame, breadcrumb (thao tác cuối trước lỗi), `app.version`, bundle/CodePush (RN: thử `app.codeBundleId`), platform/OS, thời điểm. Chép nguyên văn dòng lỗi làm `decisive_signal.quote`.
- **Source map:** stack của web/RN bản release có thể là mã đã nén (tên file bundle, số dòng của bundle). Nếu frame đã được map về `src/...` thì dùng; nếu chưa, **đừng ghi số dòng bundle làm `code_location`**. Tìm hàm/chuỗi trong repo bằng Grep theo tên hàm, thông báo lỗi hoặc chuỗi UI, và ghi vào `could_not_confirm` rằng stack chưa map.
- Không có sự kiện nào cho người dùng/khoảng giờ đó: kiểm lại project, môi trường, giờ (+0700) trước khi ghi `not_found`. Ticket staging có thể không bắn lỗi lên tracker.

## 3. Phiên bản app (bắt buộc với mobile)
- Mobile chạy đồng thời nhiều phiên bản; một backend phục vụ nhiều phiên bản; người chưa cập nhật binary hoặc chưa nhận bundle vẫn chạy bản cũ (handbook platform 1.5). Tên môi trường backend không đủ để tái hiện hành vi trên thiết bị.
- Lấy từ ticket hoặc từ Bugsnag/Sentry: platform, app version, bundle/CodePush, `releaseStage`. Ghi vào `rca.client_version`. Chưa xác định: ghi `không rõ: <lý do>`; bot **không kết luận** lỗi mobile khi thiếu.
- Biz Mobile: nhánh `main` → kênh `appstore`, `beta` → `beta`, `release/*` hoặc chọn `staging` → `staging`, còn lại `dev`; mỗi kênh có file `.env.<kênh>` và application ID khác nhau (handbook). Bản clone là nhánh mặc định, nên **luôn** đổi phiên bản thành commit: `git_tags` (mẫu tag `v3.12*`, tag có thể không có trong bản clone) rồi đọc đoạn code ở bản đó bằng `git_file`/`git_blame` với `ref`, và ghi `ref` vào `rca.code_location.ref`. Không tìm được tag thì nói rõ, đừng dùng HEAD thay cho nó.
- Bản cập nhật OTA (CodePush) có thể khác bản binary: ghi cả hai nếu tìm được.

## 4. Lần theo chuỗi màn hình → API → backend
1. Chuỗi người dùng thấy: Grep `{ANCHORS}/ui-texts.md` → key → "Used at" (component). Mã lỗi: `error-map.md`.
2. Từ component, đọc hàm gọi API (Read đúng `file:dòng`), hoặc tra `api-calls.md`/`edges.md` bằng đường dẫn API → route và handler ở backend. `confidence` thấp/trung bình: mở cả hai đầu để xác nhận, đừng tin.
3. Backend: tiếp tục theo quy trình chung (New Relic, DB, handler). Đưa mọi mắt xích vào `causal_chain` với `đường dẫn:dòng`: màn hình → hàm gọi API → route → handler → bảng.
4. Điều kiện hiển thị (cờ, remote config, trường API) thường là nguyên nhân của "thiếu nút/tùy chọn": Read đoạn render, ghi giá trị cờ/trường quyết định (xem thêm `eligibility-gates.md`).

## 5. Bug client hay gặp, để lập giả thuyết (không phải kết luận)
- Backend trả trường mới/đổi kiểu (null, thiếu) mà bản app cũ không xử lý.
- Mã lỗi backend chưa được ánh xạ sang thông báo trên UI nên hiện thông báo chung.
- Trạng thái cache/phiên cũ trong ứng dụng; bundle web cũ chứa cấu hình/endpoint cũ (handbook platform 3.x).
- Khác biệt do thiết bị/OS.
Mỗi giả thuyết cần một phép kiểm có thể loại nó (so phiên bản, so response, so thiết bị trong breadcrumb).
