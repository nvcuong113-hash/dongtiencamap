# Smart Money Flow – Media Intelligence V2 patch

## Mục tiêu

Triển khai theo đúng thứ tự ưu tiên đã thống nhất:

- **P0:** nâng chất lượng nội dung bài báo; tổng hợp Market Narrative xuyên nhiều bài; đối chiếu tin tức với giá và dòng tiền.
- **P1:** phát hiện sự kiện/cảnh báo sớm; event-study kiểm định độ trễ và sức mạnh dự báo theo hướng.
- **P2:** chưa đưa kết quả Media vào điểm số, xác suất, phiếu biểu quyết hay quyết định của SMF. Chỉ xem xét sau khi có kiểm định độc lập đủ mạnh và phê duyệt riêng.

## Những gì có trong patch

1. `quants/media_intelligence_v2.py` (mới)
   - Truy xuất nội dung HTML bài báo công khai, giới hạn dung lượng, timeout, redirect; từ chối scheme/host/port không an toàn và không vượt paywall/captcha.
   - Trích xuất tối đa 16.000 ký tự/bài; đánh dấu `FULLTEXT`, `FULLTEXT_TRUNCATED` hoặc `INSUFFICIENT_BODY`; lưu lỗi truy xuất để quan sát.
   - Khi có toàn văn tốt hơn RSS, lưu semantic cũ vào `media_semantic_history`, cập nhật hash và đưa bài về `PENDING` để AI phân tích lại.
   - Gom cụm bài thành Market Narrative theo loại sự kiện, token tiêu đề/chuỗi nhân quả và ngành; hiển thị mức độ corroboration, nguồn, bằng chứng và phản chứng.
   - Đối chiếu thông tin Media với OHLCV đã kiểm định, VWAP và flow snapshot có provenance; flow proxy không được nâng thành dòng tiền thực.
   - Sinh cảnh báo candidate về mức độ chú ý bất thường và phân kỳ tin–giá–flow.
   - Event-study với các horizon 1/3/5/10/20 phiên. Giá vào là close hợp lệ đầu tiên **sau** ngày đăng tin; không sử dụng close cùng ngày.
   - Tất cả API nghiên cứu trả `decision_impact="NONE"`; không ghi vào bảng `predictions`, không sửa điểm SMF/P_WIN.

2. `quants/media.py`
   - Prompt semantic dùng `raw_text` khi bài có `FULLTEXT`, `FULLTEXT_TRUNCATED` hoặc `RSS_FULL_CONTENT`.
   - Khi chỉ có tiêu đề/tóm tắt RSS, gửi đúng phần tóm tắt và buộc mô hình nhận biết chất lượng nguồn văn bản; không giả định đã đọc toàn văn.

3. `quants/intelligence_jobs.py`
   - Bổ sung bước enrich toàn văn trước semantic; sau semantic chạy narrative, đối chiếu giá/flow và early warning.
   - Giữ nguyên quota và cơ chế batch AI hiện có; không tự tăng quota Gemini.

4. `quants/db.py`
   - Migration additive cho trạng thái toàn văn và bảng lịch sử semantic.
   - Tăng schema version lên `20261031_v181_media_intelligence_v2`.
   - Backfill `fulltext_status='NOT_ATTEMPTED'` cho dữ liệu cũ còn NULL; migration idempotent, không xóa bài cũ.

5. `app.py`
   - Bổ sung giao diện Market Narrative, đối chiếu tin–giá–flow, Early Warning và Event Study trong tab Media Intelligence.
   - Event Study chỉ chạy khi người dùng bấm nút; không tự chạy backtest nặng mỗi lần render.

6. `tests/test_media_intelligence_v2.py`
   - 12 test mới cho extraction, fetch, lưu lịch sử semantic, narrative, price/flow, cảnh báo, event-study chống lookahead và loại trùng phiên OHLCV, migration, prompt và bảo vệ bảng dự báo.

## Cách triển khai

1. Sao lưu database production trước khi cập nhật.
2. Giải nén ZIP tại thư mục gốc repository, giữ nguyên cấu trúc đường dẫn; cho phép ghi đè các file cùng tên.
3. Không chép database thử nghiệm hoặc database trong ZIP patch (patch không chứa database).
4. Restart ứng dụng. `db.init_db()` sẽ chạy migration additive khi khởi động.
5. Kiểm tra tab **Media Intelligence**: trạng thái AI queue, fulltext status, Market Narrative, bảng alignment, cảnh báo và Event Study.
6. Kiểm tra log khởi động và `app_meta` có schema version `20261031_v181_media_intelligence_v2`.

## Lưu ý vận hành và giới hạn

- Tải toàn văn chỉ áp dụng với URL HTTP(S) công khai. Không bypass paywall, captcha, đăng nhập hoặc kiểm soát truy cập. Một số publisher có thể chặn bot; những bài đó vẫn giữ RSS và có trạng thái lỗi riêng.
- Fetch fulltext giới hạn 16 bài/lượt job, tối đa 1.5 MB HTML/bài, timeout 6 giây, tối đa 4 redirect và worker giới hạn. Không bảo đảm mọi publisher đều trích xuất được toàn văn.
- AI semantic vẫn bị giới hạn bởi quota cấu hình hiện tại và batch tối đa 8 bài/request; patch không tăng ngân sách API. Với bài FULLTEXT, prompt gửi toàn bộ phần đã trích xuất (tối đa 16.000 ký tự/bài), không còn cắt ở 3.500 ký tự; bài dài hơn 16.000 ký tự bị gắn `FULLTEXT_TRUNCATED` và bị giảm trọng số/chỉ số tin cậy.
- Market Narrative hiện là clustering có trọng số/deterministic từ semantic đã lưu, chưa gọi thêm LLM để viết báo cáo narrative dài. Số nhóm nguồn là nhóm nguồn độc lập theo metadata, không phải xác minh độc lập tuyệt đối.
- Price/flow alignment phụ thuộc dữ liệu có sẵn trong `market_bars`, `stock_snapshots`, `flow_snapshots`. Thiếu hoặc stale data sẽ trả trạng thái chưa đủ bằng chứng; không nội suy thành flow thực.
- Early warning là candidate để điều tra, không phải lệnh mua/bán.
- Event-study hiện là kiểm định direction/latency trên OHLCV, chưa phải backtest chiến lược thực thi. Chưa trừ phí, thuế, trượt giá, market impact; chưa chuẩn hóa benchmark/ngành/regime, survivorship bias và walk-forward. Không dùng kết quả hiện tại để tự động chỉnh SMF.
- P2 **chưa được bật**. Media không được phép tự động thay đổi score, P_WIN, P1–P7, sizing, prediction hoặc quyết định giao dịch.

## File thay đổi

- `app.py`
- `quants/db.py`
- `quants/intelligence_jobs.py`
- `quants/media.py`
- `quants/media_intelligence_v2.py` (mới)
- `tests/test_media_intelligence_v2.py` (mới)

## Kiểm tra tích hợp trên dữ liệu backup đã cung cấp

Đã sao chép `quants_pro_backup(20261031-073823).sqlite3` sang file tạm, chạy migration và gọi các lớp phân tích trên bản sao (không sửa file backup gốc):

- Migration giữ nguyên 1.099 bài Media, 1.905 OHLCV bars và 45 prediction records.
- 8 bài đã có semantic; 1.091 bài còn PENDING tại thời điểm backup. Narrative engine tạo 8 nhóm từ 8 bài semantic hiện có.
- Có 11 mã được ghép với thông tin Media và OHLCV; 0 flow snapshot và 0 stock snapshot trong database backup nên không thể xác nhận real money flow.
- Event Study trả 0 tín hiệu đủ điều kiện. Với 8 bài semantic hiện có và điều kiện tối thiểu nhiều bài/nhiều nhóm nguồn cho cùng mã/ngày, dữ liệu hiện tại chưa đủ mẫu để ước lượng predictive accuracy. Không diễn giải kết quả này thành accuracy 0%.

Đây là kiểm tra tương thích dữ liệu thật trên bản sao; không phải kiểm chứng API publisher/Gemini trực tiếp hay backtest đủ mẫu.
