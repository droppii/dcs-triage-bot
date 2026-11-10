# Playbook: hành vi phụ thuộc cấu hình/flag

1. **Triệu chứng → tính năng → cấu hình ứng viên.** Grep code (nơi đọc flag), đặc tả PO, ticket tương tự song song.
2. **Với mỗi ứng viên, đọc giá trị thật ở mọi cấp** có thể đặt, ghi cấp quyết định.
3. **So giá trị thật với điều code yêu cầu** để có hành vi mong đợi; trích code đọc nó (`file:dòng`).
4. **Lịch sử thay đổi** của cấu hình (bảng audit nếu có, git log của file cấu hình) trong khoảng sự cố.
5. **Báo:** tên cấu hình, cấp quyết định, giá trị hiện tại, giá trị cần, ai/cái gì đổi và khi nào, đổi ở đâu.
