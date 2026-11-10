# Playbook: "trước đây vẫn được"

1. **Chặn khoảng thời gian.** Lần cuối còn tốt, lần đầu hỏng (ticket, log, New Relic).
2. **Liệt kê thay đổi trong khoảng đó.** Deploy của service sở hữu và service phụ thuộc (`list_change_events`), migration, bật flag, sửa cấu hình, đổi bên đối tác.
3. **Giao với đường đã lần.** Thay đổi nào chạm file/hàm trên đường đó? Dùng `git_log` file đó với `since`/`until`, và so `git_head` với mốc deploy.
4. **Đọc diff** của commit ứng viên (`git_show`); nói đúng dòng nào đổi hành vi.
5. **Xác nhận.** Dấu hiệu lỗi bắt đầu tại hoặc sau thời điểm deploy. Lịch sử đã clone ngắn: nếu không thấy commit thì ghi "ngoài lịch sử đã clone", không suy đoán.
6. **Đã có bản sửa sau đó chưa?** Nếu có (commit mới hơn trên nhánh), kết luận là đã sửa: trích commit và so với mốc deploy của môi trường.
