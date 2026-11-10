# Playbook: "không làm được X" / "thiếu nút, thiếu tùy chọn"

Thường là một cổng điều kiện, không phải crash. Đi qua từng cổng theo thứ tự, dừng ở cổng đầu tiên chặn:

1. **Danh sách cho phép (allowlist).** Danh sách rỗng hoặc bị thu hẹp = chặn im lặng. Đối tượng này có trong danh sách không?
2. **Ánh xạ.** Đối tượng có map tới sản phẩm/hãng vận chuyển/vùng có cung cấp X không?
3. **Thứ tự ưu tiên phạm vi.** Đọc giá trị ở mọi cấp từ trên xuống: toàn cục → quốc gia/vùng → nhóm → tài khoản. Giá trị tường minh ở cấp cụ thể nhất thắng. Nêu cấp quyết định.
4. **Khả năng của đối tác.** Hãng vận chuyển/nhà cung cấp có hỗ trợ X cho đối tượng này không (xem log request ở `order_delivery_log`/`history_request_api`)?
5. **Flag tính năng.** Tính năng bật ở môi trường này, cho nhóm này chưa?

Kiểm bất đối xứng: nếu đối tượng cùng nhóm làm được X, so giá trị ở từng cấp với đối tượng này; báo cấp đầu tiên khác nhau. Thường kết luận là đúng thiết kế (`B`) hoặc chỉnh dữ liệu (`D`): nêu cài đặt, cấp, nơi đổi.
