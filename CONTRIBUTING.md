# Đóng góp

Repo private trong org `droppii`. Mọi thay đổi đi qua **branch + Pull Request**. Owner (QA) review và merge.

## Ai góp gì

| Bên | Góp gì | Ở đâu |
|---|---|---|
| **Vận hành** | Ticket thật đã đóng, kèm câu trả lời đúng mà mình mong bot đưa ra | Mở issue, QA dựng lại dữ liệu trên staging thành case |
| **Dev BE / FE / ME** | Bản đồ tra cứu (tên bảng, tên app New Relic, API, nơi có log); xác nhận case là bug hay đúng thiết kế; **case bug có đáp án `file:dòng` từ PR đã sửa**; kiểm `knowledge/_anchors/` có đúng với code thật | `agent/RULES.md` (mục "Bản đồ tra cứu"), `agent/playbooks/`, `cases/`, `tools/gen_anchors.py`, `tools/dbhub-stg.py` |
| **PO / BA** | Tiêu chí Priority; đặc tả "đúng thiết kế" | `agent/RULES.md` (mục Priority); đặc tả sửa ở repo `engineering-knowledge` (`handbook/product/packs/`) |
| **QA** | Owner bộ case, chạy đánh giá, vận hành bot, duyệt PR | cả repo |

## Luật khi sửa `agent/RULES.md`

- **Chỉ ghi dữ kiện đã kiểm chứng.** Mô tả PR phải kèm nguồn: câu query đã chạy, `đường dẫn:dòng` code, hoặc tên người xác nhận. Không ghi suy đoán.
- Giữ ngắn: model đọc toàn bộ file này cho mỗi ticket, dài thêm thì tốn thêm tiền.
- Đổi luật thì chạy lại các case liên quan: `python3 bot.py --dry-ticket cases/<case>.json`, rồi ghi kết quả vào PR.

## Thêm case

File `cases/<tên>.json`:

```json
{
 "ticket": {"Title": "...", "Description": "...", "Priority": "High", "Product": "Biz App", "Feature": "Voucher", "ReportedBy": "QA test"},
 "expect": {
  "verdict_any_of": ["A", "B"],
  "must_contain": ["KM03028706", "ClaimVoucherCommandHandler"],
  "must_not_contain": ["hỏi vận hành"],
  "forbidden_conclusions": ["chuỗi sai mà bot không được kết luận (case sentinel)"],
  "code_location": {"file_suffix": "ClaimVoucherCommandHandler.java", "line_range": [70, 80]},
  "max_tool_calls": 60, "max_cost_usd": 1.5,
  "valid_until": "..."
 },
 "created": "YYYY-MM-DD"
}
```

- `verdict_any_of`: loại bot được phép công bố (loại sau cổng, `?` nếu chưa đủ bằng chứng). Case chỉ hỏi thông tin: `"missing_info": true`.
- `must_contain` / `must_not_contain` / `forbidden_conclusions`: **chuỗi nguyên văn** để máy khớp. Các khóa mô tả như `must_mention`, `must_not`, `why` chỉ là ghi chú cho người đọc.
- `code_location`: đáp án `file:dòng` (lấy từ diff của PR fix nếu là bug đã sửa). Bot phải trích đúng file và dòng chồng lên khoảng này, và trích dẫn phải khớp code thật.
- **Lỗi FE/mobile**: copy `cases/_TEMPLATE-fe-mobile-bug.json` (có `layer` và `app_version`); với mobile ghi tag của phiên bản app, vì dòng lỗi có thể không còn ở HEAD.
- **Case bug có đáp án** (quan trọng nhất): copy `cases/_TEMPLATE-bug-from-pr.json`, dựng lại dữ liệu staging cho bug Jira đã đóng, ghi PR fix vào `ground_truth`. Mỗi bộ case nên có ít nhất một case *sentinel* (giả thuyết hiển nhiên là sai).
- Chấm: `python3 tools/eval.py cases/<tên>.json --runs 3`.

- Chỉ dùng **dữ liệu staging** (mã đơn, tài khoản test `nhudoan*`). Không chép dữ liệu production hay thông tin cá nhân.
- Case dựa vào log New Relic thì ghi `valid_until`: log chỉ giữ khoảng 30 ngày.
- Dữ liệu cần tạo mới thì dùng `tools/stg_case.py` (tài khoản QA, creds trong `.env`).

## Thêm nguồn dữ liệu

- Chỉ quyền **đọc**. Database staging: thêm vào `DATABASES` trong `tools/dbhub-stg.py`. MCP mới: thêm vào `mcp_config()` và **chỉ các tool đọc** vào `READ_TOOLS` trong `bot.py`.
- Sau đó chạy `python3 tools/check_sandbox.py` và ghi kết quả vào PR.
- Công cụ MCP tự viết (như `repo_git_mcp.py`) phải có test từ chối đầu vào nguy hiểm trong `tests/`.

## Trước khi mở PR

```sh
python3 tests/run_all.py            # bắt buộc (không gọi mạng)
python3 tools/check_sandbox.py       # khi đổi tool, quyền, MCP, DBHub
```

Logic mới trong `bot.py` / `rca.py` cần có test trong `tests/`. Đổi `agent/RULES.md` hoặc `config.json` → `gate`: chạy `tools/eval.py` và ghi điểm trước/sau vào PR.

## Không bao giờ commit

`logs/`, `data/`, `.env`, `knowledge/`, nội dung ticket production, token, mật khẩu, chuỗi kết nối. `.gitignore` đã chặn các thư mục này, nhưng vẫn kiểm `git status` trước khi commit.
