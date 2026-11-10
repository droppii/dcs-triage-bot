# Playbook: lỗi ở web (fe) hoặc mobile

Nguồn: bảng Product → repo ở RULES (Bước 1a) và handbook `engineering-knowledge` (platform.md mục 1.5, platform-fe.md). Mọi chỗ ghi "thử" là chưa kiểm chứng trên hệ thống thật.

## 1. Lỗi ở lớp nào
1. **Có request tới backend không?** New Relic `Transaction` của entity backend đúng service, khoảng ±10 phút quanh giờ báo, lọc `request.uri` theo API mà màn hình đó gọi (tra ở `{ANCHORS}/api-calls.md` / `edges.md`).
   - Không có request nào → lỗi chặn ở client trước khi gọi (validate, trạng thái UI, crash). Lớp `fe`/`mobile`.
   - Có request, 5xx → backend (`TransactionError`). Có request, 2xx → xem response/dữ liệu: đúng thì lỗi ở client, sai thì backend.
2. **Client có ghi lỗi không?** Bugsnag/Sentry (bước 2). Có lỗi JS/native cùng giờ, cùng người dùng/phiên bản → client.
3. Cùng một API, người dùng khác hoặc phiên bản khác vẫn ổn → bất đối xứng ở phiên bản app hoặc dữ liệu người dùng; so phiên bản (bước 3).

## 2. Lỗi client: Bugsnag / Sentry
- **Biz App, Mall App** dùng Bugsnag (project Droppii Biz / Droppii Mall); **web-mall** dùng Sentry (project `web-mall`, org droppii-kp). Lọc theo môi trường: Bugsnag `app.release_stage` = `{BUGSNAG_STAGE}`; Sentry `environment` = {SENTRY_ENV}. Không dùng tên môi trường của mình ({ENV_NAME}) làm giá trị lọc.
- Bugsnag, kiểm 09/11/2026: bản staging của Biz có app id `com.droppii.business.staging`, error class thường có tiền tố `[STAGING]/`. Mall staging chưa gửi lỗi lên Bugsnag: không thấy thì ghi `not_found` kèm lý do, đừng kết luận là app không lỗi. Lọc thêm `user.id` = `{DB_PREFIX}identity."AspNetUsers"."Id"` của tài khoản trong ticket, và `event.since` quanh giờ người báo. Dùng `bugsnag_list_project_errors` trước (đủ `error_class`, `message`, `context`, `last_seen`, `events`); `bugsnag_get_error` có thể trả hơn 100 nghìn ký tự, chỉ gọi khi danh sách chưa đủ. Payload có header `Authorization` (token đăng nhập): không chép header, token hay payload vào câu trả lời.
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

## 4b. Chọn dòng nghi ngờ cho lỗi "dữ liệu hiển thị không cập nhật" (react-query / zustand)
Bài học từ D1110-009 (giỏ hàng bấm + không thấy tổng tiền đổi): bot từng chỉ vào `queryKey` của hook giá, mà dòng đó đúng.
- **Đọc đủ chuỗi hook phụ thuộc trước khi gán `code_location`.** Từ giá trị hiển thị, lần ngược: component hiển thị → hook trả giá trị → mọi hook mà `enabled` của nó phụ thuộc vào (ví dụ giá checkout phụ thuộc phí ship, voucher, quà). Đọc cả các hook đó, không dừng ở hook cuối.
- **`queryKey` có chứa tham số đổi không?** Nếu key có `JSON.stringify(params)` và params chứa giá trị người dùng vừa đổi (số lượng, địa chỉ…), thì "bị cache giá cũ" là SAI: react-query v5 với key mới trả `data = undefined` (UI hiện `-`/skeleton), không trả giá cũ. Loại giả thuyết cache bằng cách ghi vào `alternatives` với `refuted` và dòng chứng minh.
- **Dòng nghi ngờ nên là điều kiện chặn hoặc chỗ làm rỗng dữ liệu:** `enabled` của query, `.filter(Boolean)`/`?.[0]` làm mảng rỗng khi hook cha đang refetch, `providerId` rỗng. Câu hỏi cần trả lời: khi hook cha lỗi hoặc trả rỗng với giá trị mới, query giá có còn chạy không?
- **Tìm cap/clamp ở nơi thay đổi trạng thái** (ví dụ `quantity < maxQuantity ? 1 : 0`): thao tác có thể không đổi gì. Đưa thành câu hỏi cho người báo ("số lượng trên UI có tăng không?").
- Phân biệt 3 nguyên nhân: (a) request không chạy (`enabled` false/hook cha lỗi), (b) request chạy nhưng BE trả giá không đổi, (c) UI không render lại. Chỉ New Relic/Network/Sentry mới phân biệt được; không có runtime thì verdict giữ `?`. Priority theo mục Priority của RULES (dính tiền → High); không tự thêm lý do nâng mức ngoài mục đó.

## 5. Bug client hay gặp, để lập giả thuyết (không phải kết luận)
- Backend trả trường mới/đổi kiểu (null, thiếu) mà bản app cũ không xử lý.
- Mã lỗi backend chưa được ánh xạ sang thông báo trên UI nên hiện thông báo chung.
- Trạng thái cache/phiên cũ trong ứng dụng; bundle web cũ chứa cấu hình/endpoint cũ (handbook platform 3.x).
- Khác biệt do thiết bị/OS.
Mỗi giả thuyết cần một phép kiểm có thể loại nó (so phiên bản, so response, so thiết bị trong breadcrumb).
