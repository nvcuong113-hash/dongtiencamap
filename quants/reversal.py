def media_divergence_score(
    sentiment_score: float,
    relative_return_pct: float,
    attention_change_pct: float | None = None,
    net_flow_billion: float | None = None,
    is_real_deal: bool = False,
) -> float:
    """Đo lường độ phân kỳ Phản xạ (Reflexivity Divergence) giữa Tâm lý Media,
    Độ đột biến đám đông (Attention Crowding), Phản ứng giá (RS) và Dòng tiền thực.
    Tương thích ngược 100% khi chỉ truyền (sentiment_score, relative_return_pct).
    """
    sentiment, rel = _num(sentiment_score), _num(relative_return_pct)
    att_chg = _num(attention_change_pct, 0.0) if attention_change_pct is not None else 0.0
    flow = _num(net_flow_billion, 0.0) if net_flow_billion is not None else 0.0

    # Hệ số khuếch đại đám đông (Crowding): khi lượng bài viết tăng đột biến (>50%..200%),
    # sự thất bại của giá trước tin tức mang ý nghĩa phân phối/hấp thụ mạnh hơn nhiều.
    crowding_amp = 1.0 + 0.30 * max(0.0, min(2.0, att_chg / 100.0))

    # 1. Phân kỳ đỉnh (Bull Trap / Bơm tin xả hàng): Tin tích cực nhưng giá yếu hoặc dòng tiền thực xả
    if sentiment >= 0.25 and rel <= -1.0:
        base = 50.0 + min(42.0, (sentiment * 25.0 + abs(rel) * 8.0) * crowding_amp)
        if is_real_deal and flow < 0:
            base += 8.0
        return clamp(base, 0.0, 100.0)

    # Phát hiện sớm "PR Euphoria Stalling": Bơm tin rất tích cực + truyền thông đột biến nhưng giá đi ngang/dậm chân tại chỗ
    if sentiment >= 0.45 and att_chg >= 50.0 and -1.0 < rel <= 0.3:
        base = 52.0 + min(30.0, sentiment * 20.0 + (att_chg / 20.0))
        if is_real_deal and flow < 0:
            base += 10.0
        return clamp(base, 0.0, 100.0)

    # 2. Phân kỳ đáy (Bear Trap / Ra tin xấu gom hàng): Tin tiêu cực nhưng giá khỏe hơn thị trường
    if sentiment <= -0.25 and rel >= 1.0:
        base = 50.0 + min(42.0, (abs(sentiment) * 25.0 + rel * 8.0) * crowding_amp)
        if is_real_deal and flow > 0:
            base += 8.0
        return clamp(base, 0.0, 100.0)

    return clamp(abs(sentiment * rel) * 8.0 * crowding_amp, 0.0, 49.0)
