# Playbook: có chuỗi/mã lỗi nguyên văn

1. **Chép đúng nguyên văn**: chuỗi hoặc mã lỗi, ngôn ngữ, môi trường, giờ (+0700). Chuỗi chỉ là bản diễn đạt lại của người báo thì coi là anchor yếu; có ảnh đính kèm thì Read ảnh.
2. **Tìm nơi raise.** Grep chuỗi trong `{ANCHORS}/errors.md` rồi trong code. Nếu từ file dịch (i18n), tìm key rồi tìm code dùng key. Thường có vài kết quả: thông báo ở frontend, mã lỗi backend, bảng ánh xạ.
3. **Chốt cơ chế.** Điều kiện nào làm raise? Đọc nhánh, ghi mọi input nó phụ thuộc.
4. **Dữ liệu hay cấu hình?** Điều kiện do cấu hình/flag chung hay do dữ liệu riêng của đối tượng? Quyết định phép kiểm tiếp theo.
5. **Nhiều đường cùng thông báo?** Dùng route, stack trace, log (cùng trace.id) để biết đường nào đã chạy.
6. **Xác nhận giá trị thật** (đọc dòng DB/cấu hình của đối tượng này, hoặc dòng log cùng request) rồi mới kết luận.
