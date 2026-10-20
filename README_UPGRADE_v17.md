QUANTS PRO V16.1 — PATCH AI RECOVERY + MARKET DATA LABELS v17

Chỉ cập nhật các file trong gói:
1. app.py (thư mục gốc)
2. quants/ai_engine.py (thư mục quants/)
3. README_UPGRADE_v17.md (tài liệu tùy chọn)

AI:
- Lỗi trên ảnh là Gemini API trả INVALID_ARGUMENT vì timeout 9 giây thấp hơn mức tối thiểu 10 giây.
- Khôi phục timeout SDK Gemini lên 30 giây và OpenAI lên 30 giây, theo cấu hình ở bản AI-FIX-v7 trước đó.
- Trở lại danh sách model fallback từ bản v7.
- Không thay API key hoặc Secrets. Không ghi API key vào mã nguồn.

GTGD/khối ngoại:
- Đổi nhãn thành GTGD thị trường và khối ngoại ròng thị trường, tránh nhầm với rổ theo dõi.
- Không bịa số liệu toàn thị trường. Nếu nguồn chỉ có dữ liệu từng mã, số toàn thị trường vẫn là N/A.
- Ảnh hiện tại cho thấy VPS VNINDEX endpoint không trả bản ghi chỉ số; bản vá này KHÔNG giả định GTGD/khối ngoại đã được khắc phục. Cần kiểm chứng endpoint thống kê thị trường riêng trước khi điền hai trường này.

Triển khai:
- Ghi đè đúng app.py và quants/ai_engine.py.
- Giữ nguyên các file khác và Streamlit Secrets.
- Commit rồi đợi Streamlit Cloud redeploy.
- Kiểm tra nút Chạy AI 2 tầng. Nếu còn lỗi, gửi log mới, gồm tên model và thông báo lỗi (không gửi API key).

Không tuyên bố đã xác minh API live vì môi trường tạo patch không truy cập được Internet.
