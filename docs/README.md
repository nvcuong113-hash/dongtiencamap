# Money Flow Story tab – 2026-10-31

## Mục tiêu
Chuyển tab từ bảng kỹ thuật/đầu ra debug sang câu chuyện dòng tiền dễ đọc theo trình tự vi mô → vĩ mô.

## Thay đổi
- Đổi tên tab thành `CÂU CHUYỆN DÒNG TIỀN 48H`.
- Cửa sổ tin tức, semantic và narrative: 48 giờ; giá: 3 phiên; bối cảnh vĩ mô: 20 phiên khi đủ dữ liệu.
- Trình bày theo thứ tự: từng cổ phiếu (giá/volume/flow) → tâm lý đám đông → câu chuyện lan sang ngành → bối cảnh vĩ mô → cảnh báo và điều cần theo dõi.
- Thay các bảng raw `event_type`, `direction_score`, `conflict_ratio` bằng diễn giải tiếng Việt.
- Đưa nguồn gốc, phản chứng, quota, giới hạn dữ liệu và bảng kỹ thuật vào các expander đóng mặc định.
- Nếu không có nguồn xã hội/diễn đàn, nói rõ chưa có dữ liệu tâm lý nhỏ lẻ; không lấy báo chí làm đại diện.
- Phân biệt dòng tiền thực đã xác minh với proxy/ước tính; không suy luận chắc chắn ý đồ của tổ chức/cá nhân.
- Toàn bộ lớp trình bày trả `decision_impact=NONE`; không sửa SMF score, P_WIN, P1–P7 hoặc quyết định giao dịch.

## Files
- `app.py`
- `quants/money_flow_story.py` (mới)
- `tests/test_money_flow_story.py` (mới)

## Triển khai
Giải nén patch vào thư mục gốc repo, giữ nguyên cấu trúc. Không có migration database. Khởi động lại Streamlit.
