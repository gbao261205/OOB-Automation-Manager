---
name: web-researcher
description: Tự động tìm kiếm, tra cứu tài liệu mới nhất và đọc nội dung web/API specs khi giải quyết vấn đề hoặc nâng cấp code.
---

# Web Researcher & Documentation Fetcher Guidelines

## Khi nào kích hoạt
- Khi gặp thư viện, framework, lỗi lạ hoặc cú pháp công nghệ mới chưa có trong ngữ cảnh.
- Khi người dùng yêu cầu tra cứu tài liệu chính thống hoặc tìm kiếm giải pháp kỹ thuật mới nhất.

## Quy trình thực hiện
1. **Xác định từ khóa**: Rút trích chính xác tên thư viện, phiên bản và thông báo lỗi.
2. **Thu thập tài liệu**:
   - Sử dụng tool duyệt web có sẵn của hệ thống hoặc chạy script curl/python requests để đọc nội dung tài liệu.
   - Ưu tiên các nguồn chính thống: GitHub Releases, PyPI, Official Docs, RFC.
3. **Tổng hợp và suy luận**:
   - Trích xuất cú pháp mẫu, cấu trúc payload hoặc giải pháp sửa lỗi.
   - Cập nhật giải pháp vào context và áp dụng trực tiếp vào mã nguồn của dự án.