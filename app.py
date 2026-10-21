# ============================================================================
# HÀM ĐỊNH DẠNG SỐ & TÔ MÀU CHUẨN CHỨNG KHOÁN (XANH / ĐỎ / VÀNG)
# ============================================================================
def style_financial_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    styler = df.style

    # 1. Hàm tô màu theo giá trị số (Tăng xanh, Giảm đỏ, Tham chiếu vàng)
    def color_values(val):
        if pd.isna(val) or str(val).strip() in ("", "---", "N/A", "KHÔNG DỰ BÁO", "Chưa có AI"):
            return "color: #94a3b8"  # Màu xám cho dữ liệu trống
        try:
            # Bóc tách chuỗi số phần trăm hoặc tỷ đồng
            clean_str = str(val).replace("%", "").replace("T", "").replace("+", "").replace(",", "").strip()
            num = float(clean_str)
            if num > 0.0001:
                return "color: #22c55e; font-weight: bold;"  # Xanh lá cây
            elif num < -0.0001:
                return "color: #ef4444; font-weight: bold;"  # Đỏ
            else:
                return "color: #eab308; font-weight: bold;"  # Vàng tham chiếu
        except (ValueError, TypeError):
            return "color: #dbeafe"

    # 2. Danh sách các cột cần tô màu xanh / đỏ / vàng
    color_cols = [
        "Biến Động", "Lãi / Lỗ (%)", "Lãi / Lỗ (VND)", "Thay Đổi (%)", "RS vs VNI", 
        "RS vs Ngành", "Ước tính Net Flow (Tỷ)", "Δ Flow vs Đầu Phiên", "Ngoại Net (CP)", 
        "Ngoại 5P (CP)", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)"
    ]
    target_color_cols = [c for c in color_cols if c in df.columns]
    if target_color_cols:
        styler = styler.applymap(color_values, subset=target_color_cols)

    # 3. Định dạng phân cách hàng nghìn và tối đa 2 chữ số sau dấu phẩy
    format_dict = {}
    for col in df.columns:
        if col in ["Giá Vốn", "Giá Hiện Tại", "Giá Khớp", "Khối Lượng", "Tổng KL", "TB20 Vol", "Vol Dự Phóng", "Ước tính Mua CĐ (CP)", "Ước tính Bán CĐ (CP)"]:
            format_dict[col] = lambda x: f"{x:,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Thay Đổi (%)", "Lãi / Lỗ (%)", "Tỷ Lệ M/B", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)", "RS vs VNI", "RS vs Ngành"]:
            format_dict[col] = lambda x: f"{x:+.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ước tính Net Flow (Tỷ)", "Δ Flow vs Đầu Phiên"]:
            format_dict[col] = lambda x: f"{x:+.2f} Tỷ" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ngoại Net (CP)", "Ngoại 5P (CP)"]:
            format_dict[col] = lambda x: f"{x:+,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)

    styler = styler.format(format_dict, na_rep="---")
    return styler
