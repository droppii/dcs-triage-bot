-- Quyền DB cho bot trên PROD. DBA chạy trên PRIMARY của từng database dịch vụ (view và role được replicate sang REPLICA);
-- bot chỉ kết nối vào REPLICA. Đây là lớp bảo vệ CHÍNH: allowlist SQL (sqlguard.py) và bộ che dữ liệu chỉ là lớp phụ.
--
-- Nguyên tắc: bot không có quyền gì trên bảng gốc. Nó chỉ SELECT được các VIEW đã bỏ cột cá nhân (SĐT, email, địa chỉ,
-- CCCD, ngân hàng, token, họ tên người nhận...). Mỗi view phải khớp 1-1 với một mục trong config/db_allowlist.json.
-- Thay <db>, <password> (lấy từ secret manager, không ghi vào repo) và danh sách view theo từng service.

-- 1. Role: chỉ đọc, ít kết nối, tự ngắt truy vấn nặng
CREATE ROLE dcs_bot LOGIN PASSWORD '<password>'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
ALTER ROLE dcs_bot SET default_transaction_read_only = on;
ALTER ROLE dcs_bot SET statement_timeout = '15s';
ALTER ROLE dcs_bot SET lock_timeout = '2s';
ALTER ROLE dcs_bot SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE dcs_bot SET search_path = dcs_bot_view;        -- tên không kèm schema chỉ trỏ vào view

-- 2. Chỉ kết nối vào DB này, không đụng schema nào khác
REVOKE ALL ON DATABASE <db> FROM PUBLIC;
GRANT CONNECT ON DATABASE <db> TO dcs_bot;
REVOKE ALL ON SCHEMA public FROM dcs_bot;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dcs_bot;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM dcs_bot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM dcs_bot;

-- 3. Schema chứa view đã che
CREATE SCHEMA IF NOT EXISTS dcs_bot_view;
GRANT USAGE ON SCHEMA dcs_bot_view TO dcs_bot;

-- 4. Ví dụ một view: liệt kê CỘT ĐƯỢC PHÉP, không dùng SELECT *. Thêm cột mới vào bảng gốc không tự lộ ra cho bot.
CREATE VIEW dcs_bot_view.orders AS
    SELECT id, code, status, created_at, updated_at, total_amount, shipping_fee, voucher_code
    FROM public.orders;
GRANT SELECT ON dcs_bot_view.orders TO dcs_bot;

-- 5. Kiểm tra (chạy bằng tài khoản DBA; mọi dòng đều phải đúng như ghi chú)
SELECT has_table_privilege('dcs_bot', 'public.orders', 'SELECT');           -- false
SELECT has_table_privilege('dcs_bot', 'dcs_bot_view.orders', 'SELECT');     -- true
SELECT has_table_privilege('dcs_bot', 'dcs_bot_view.orders', 'INSERT');     -- false
SELECT has_column_privilege('dcs_bot', 'public.orders', 'receiver_phone', 'SELECT');  -- false (tên cột minh họa)
-- Rồi đăng nhập bằng dcs_bot trên replica và thử: SELECT * FROM public.orders;  -> phải báo permission denied
