---
name: fetch-docs
description: Cào nhanh nội dung trang tài liệu hoặc GitHub URL thành định dạng text/markdown để bổ sung context.
---

# Fetch Documentation Skill

## Hướng dẫn thực thi
Khi cần đọc một đường link tài liệu hoặc tham chiếu từ web:
1. Chạy lệnh: `python .agent/skills/fetch-docs/fetch_page.py <URL>`
2. Đọc kết quả markdown trả về từ stdout.
3. Phân tích nội dung và áp dụng vào tác vụ hiện tại.