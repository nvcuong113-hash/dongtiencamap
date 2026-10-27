"""Interactive VSA Candlestick & Combat R:R Chart helper for QUANTS PRO V16.4.

Read-only presentation module: consumes existing history bars, engine rows, and
decision report rows without mutating any quant, AI, or database state.
"""
from __future__ import annotations

import math
from typing import Any, Iterable


def _finite_pos(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool) or value == "":
            return None
        num = float(value)
        return round(num, 2) if math.isfinite(num) and num > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _finite_nonneg(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool) or value == "":
            return default
        num = float(value)
        return num if math.isfinite(num) and num >= 0 else default
    except (TypeError, ValueError, OverflowError):
        return default


def prepare_vsa_bars(
    bars: Iterable[dict] | None,
    live_row: dict | None = None,
    lookback: int = 90,
) -> list[dict]:
    """Normalize historical bars, append/update current session from live_row,
    and compute synthetic Open (when absent in provider history), MA20 Price,
    MA20 Volume, Spread, and Close Position for VSA inspection.
    """
    dedup: dict[str, dict] = {}
    for raw in bars or []:
        if not isinstance(raw, dict):
            continue
        date_str = str(raw.get("date") or raw.get("tradingDate") or "")[:10]
        close = _finite_pos(raw.get("close"))
        if len(date_str) != 10 or close is None:
            continue
        high = _finite_pos(raw.get("high")) or close
        low = _finite_pos(raw.get("low")) or close
        open_px = _finite_pos(raw.get("open"))
        high = max(high, close, open_px or close)
        low = min(low, close, open_px or close)
        vol = int(_finite_nonneg(raw.get("volume"), 0.0))
        dedup[date_str] = {
            "date": date_str,
            "open": open_px,
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": vol,
        }

    # Merge or append live/latest session from the active Radar row so the chart
    # always matches the exact price & volume displayed in the Radar table.
    if isinstance(live_row, dict):
        trade_date = str(live_row.get("trade_date") or "")[:10]
        live_price = _finite_pos(live_row.get("price"))
        if len(trade_date) == 10 and live_price is not None:
            live_high = _finite_pos(live_row.get("high")) or live_price
            live_low = _finite_pos(live_row.get("low")) or live_price
            live_ref = _finite_pos(live_row.get("ref_price"))
            live_vol = int(_finite_nonneg(live_row.get("volume"), 0.0))
            existing = dedup.get(trade_date)
            open_candidate = (
                (existing.get("open") if existing else None)
                or live_ref
                or live_price
            )
            high_merged = max(
                live_high,
                live_price,
                open_candidate,
                existing["high"] if existing else live_high,
            )
            low_merged = min(
                live_low,
                live_price,
                open_candidate,
                existing["low"] if existing else live_low,
            )
            vol_merged = (
                live_vol
                if live_vol > 0
                else (existing["volume"] if existing else 0)
            )
            dedup[trade_date] = {
                "date": trade_date,
                "open": round(open_candidate, 2),
                "high": round(high_merged, 2),
                "low": round(low_merged, 2),
                "close": round(live_price, 2),
                "volume": int(vol_merged),
            }

    ordered = [dedup[d] for d in sorted(dedup.keys())]
    if not ordered:
        return []

    for i, item in enumerate(ordered):
        prev_close = ordered[i - 1]["close"] if i > 0 else item["close"]
        if item["open"] is None or item["open"] <= 0:
            # Provider history (VNDirect/TCBS) in providers.py stores C/H/L/V;
            # use prior close bounded inside the session's [low, high] range,
            # or midpoint if the bar gapped completely outside prior close.
            if item["low"] <= prev_close <= item["high"]:
                inferred_open = prev_close
            else:
                inferred_open = (item["high"] + item["low"]) / 2.0
            item["open"] = round(inferred_open, 2)
        item["high"] = round(max(item["high"], item["open"], item["close"]), 2)
        item["low"] = round(min(item["low"], item["open"], item["close"]), 2)
        item["prev_close"] = round(prev_close, 2)
        item["change_pct"] = (
            round((item["close"] / prev_close - 1.0) * 100.0, 2)
            if prev_close > 0
            else 0.0
        )

        win_closes = [x["close"] for x in ordered[max(0, i - 19) : i + 1]]
        item["ma20"] = (
            round(sum(win_closes) / len(win_closes), 2)
            if len(win_closes) >= 5
            else None
        )

        win_vols = [
            x["volume"]
            for x in ordered[max(0, i - 19) : i + 1]
            if x["volume"] > 0
        ]
        item["ma20_vol"] = (
            round(sum(win_vols) / len(win_vols), 0) if win_vols else None
        )
        item["vol_ratio"] = (
            round(item["volume"] / item["ma20_vol"], 2)
            if item["ma20_vol"] and item["ma20_vol"] > 0 and item["volume"] > 0
            else None
        )

        spread = max(item["high"] - item["low"], item["close"] * 0.003)
        item["close_pos_pct"] = round(
            (item["close"] - item["low"]) / spread * 100.0, 0
        )

    limit = max(20, min(int(lookback or 90), 250))
    return ordered[-limit:]


def extract_chart_levels(
    live_row: dict | None,
    decision_row: dict | None = None,
) -> dict[str, float | None]:
    """Extract verified structural and combat levels without fabricating missing values."""
    row = live_row if isinstance(live_row, dict) else {}
    dec = decision_row if isinstance(decision_row, dict) else {}
    key_levels = (
        row.get("key_levels") if isinstance(row.get("key_levels"), dict) else {}
    )

    price = _finite_pos(row.get("price"))
    support = _finite_pos(row.get("support") or key_levels.get("support"))
    resistance = _finite_pos(
        row.get("resistance") or key_levels.get("resistance")
    )
    vwap = (
        _finite_pos(row.get("vwap"))
        if row.get("vwap_available", True) is not False
        else None
    )
    stop_loss = _finite_pos(
        dec.get("Điểm vô hiệu/cắt lỗ") or row.get("stop_loss_price")
    )
    target_price = _finite_pos(dec.get("Mục tiêu 1") or row.get("target_price"))
    entry_trigger = _finite_pos(dec.get("Giá trigger"))
    if entry_trigger is None and price is not None and resistance is not None:
        candidate_entry = max(price, vwap or price)
        if candidate_entry < resistance:
            entry_trigger = round(candidate_entry, 2)

    ma10w = _finite_pos(row.get("ma10w") or key_levels.get("ma10w"))
    ma20w = _finite_pos(row.get("ma20w") or key_levels.get("ma20w"))

    return {
        "price": price,
        "support": support,
        "resistance": resistance,
        "vwap": vwap,
        "entry_trigger": entry_trigger,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "ma10w": ma10w,
        "ma20w": ma20w,
    }


def build_vsa_plotly_figure(
    symbol: str,
    prepared_bars: list[dict],
    live_row: dict | None = None,
    decision_row: dict | None = None,
):
    """Build a 2-row interactive Plotly figure (Candlestick + R:R zones + VSA Volume)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    sym = str(symbol or "").strip().upper()
    row = live_row if isinstance(live_row, dict) else {}
    levels = extract_chart_levels(row, decision_row)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.72, 0.28],
        subplot_titles=(
            f"{sym} — Cấu Trúc Giá VSA, VWAP & Khung R:R Thực Chiến",
            "Khối Lượng Khớp Lệnh & Trung Bình 20 Phiên (MA20 Vol)",
        ),
    )

    if not prepared_bars:
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#081827",
            plot_bgcolor="#081827",
            height=520,
            annotations=[
                dict(
                    text=f"Chưa có dữ liệu nến lịch sử cho {sym}",
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=15, color="#9fb4ce"),
                )
            ],
        )
        return fig

    dates = [b["date"] for b in prepared_bars]
    opens = [b["open"] for b in prepared_bars]
    highs = [b["high"] for b in prepared_bars]
    lows = [b["low"] for b in prepared_bars]
    closes = [b["close"] for b in prepared_bars]
    volumes = [b["volume"] for b in prepared_bars]
    ma20_price = [b.get("ma20") for b in prepared_bars]
    ma20_vol = [b.get("ma20_vol") for b in prepared_bars]

    hover_texts = [
        (
            f"<b>{sym} • {b['date']}</b><br>"
            f"Mở cửa: {b['open']:,.0f} đ | Cao: {b['high']:,.0f} đ<br>"
            f"Thấp: {b['low']:,.0f} đ | Đóng cửa: <b>{b['close']:,.0f} đ</b> ({b['change_pct']:+.2f}%)<br>"
            f"Vị thế đóng cửa nến (Close Pos): {b['close_pos_pct']:.0f}%<br>"
            f"Khối lượng: {b['volume']:,.0f} CP"
            + (
                f" ({b['vol_ratio']:.2f}x MA20)"
                if b.get("vol_ratio") is not None
                else ""
            )
        )
        for b in prepared_bars
    ]

    # 1. Nến Nhật VSA
    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="Nến Giá (OHLC)",
            increasing_line_color="#22c55e",
            increasing_fillcolor="rgba(34, 197, 94, 0.78)",
            decreasing_line_color="#ef4444",
            decreasing_fillcolor="rgba(239, 68, 68, 0.78)",
            hovertext=hover_texts,
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )

    # 2. Đường MA20 Giá
    if any(v is not None for v in ma20_price):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=ma20_price,
                mode="lines",
                name="MA20 Ngày",
                line=dict(color="#60a5fa", width=1.6),
                hovertemplate="MA20: %{y:,.0f} đ<extra></extra>",
            ),
            row=1,
            col=1,
        )

    # 3. Điểm VWAP phiên hiện tại (nếu có VWAP thực/ước tính hợp lệ)
    if levels["vwap"] is not None:
        fig.add_trace(
            go.Scatter(
                x=[dates[-1]],
                y=[levels["vwap"]],
                mode="markers+text",
                name=f"VWAP ({levels['vwap']:,.0f})",
                marker=dict(
                    color="#eab308",
                    size=11,
                    symbol="diamond",
                    line=dict(color="#fef08a", width=1.5),
                ),
                text=[f"  VWAP {levels['vwap']:,.0f}"],
                textposition="middle right",
                textfont=dict(color="#fde047", size=11),
                hovertemplate=f"VWAP phiên: {levels['vwap']:,.0f} đ<extra></extra>",
            ),
            row=1,
            col=1,
        )

    # 4. Hộp視覺 R:R (Vùng Lợi nhuận mục tiêu màu xanh & Vùng Rủi ro cắt lỗ màu đỏ)
    ref_entry = levels["entry_trigger"] or levels["price"]
    if (
        ref_entry is not None
        and levels["target_price"] is not None
        and levels["target_price"] > ref_entry
    ):
        fig.add_hrect(
            y0=ref_entry,
            y1=levels["target_price"],
            fillcolor="rgba(34, 197, 94, 0.07)",
            line_width=0,
            row=1,
            col=1,
        )
    if (
        ref_entry is not None
        and levels["stop_loss"] is not None
        and levels["stop_loss"] < ref_entry
    ):
        fig.add_hrect(
            y0=levels["stop_loss"],
            y1=ref_entry,
            fillcolor="rgba(239, 68, 68, 0.08)",
            line_width=0,
            row=1,
            col=1,
        )

    # 5. Các đường ngang Hỗ trợ / Kháng cự / Cắt lỗ / Mục tiêu / Trigger
    horizontal_specs = [
        ("target_price", "🎯 Mục tiêu", "#22c55e", "dash"),
        ("resistance", "Kháng cự 20P", "#f97316", "dot"),
        ("entry_trigger", "⚡ Trigger", "#38bdf8", "dashdot"),
        ("support", "Hỗ trợ 20P", "#a855f7", "dot"),
        ("stop_loss", "🛑 Cắt lỗ", "#ef4444", "dash"),
    ]
    for key, label, color, dash in horizontal_specs:
        val = levels.get(key)
        if val is not None:
            fig.add_hline(
                y=val,
                line_dash=dash,
                line_color=color,
                line_width=1.35,
                annotation_text=f"{label}: {val:,.0f}",
                annotation_position="top left"
                if key in ("target_price", "resistance", "entry_trigger")
                else "bottom left",
                annotation_font=dict(color=color, size=11),
                row=1,
                col=1,
            )

    # 6. Nhãn tín hiệu Wyckoff / Đáy-Đỉnh sớm trên cây nến cuối cùng
    wyckoff_tag = str(row.get("wyckoff") or "").strip()
    early_sig = str(row.get("early_signal") or "").strip()
    badge_parts = [
        p
        for p in (wyckoff_tag, early_sig)
        if p and "Chưa" not in p and "Thiếu" not in p
    ]
    if badge_parts:
        fig.add_annotation(
            x=dates[-1],
            y=highs[-1],
            text=f"<b>{badge_parts[0][:38]}</b>",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1.2,
            arrowcolor="#93c5fd",
            ax=0,
            ay=-28,
            bgcolor="rgba(15, 23, 42, 0.88)",
            bordercolor="#3b82f6",
            borderwidth=1,
            font=dict(size=11, color="#e0f2fe"),
            row=1,
            col=1,
        )

    # 7. Cột Volume tô màu theo nến + Đường MA20 Volume
    vol_colors = [
        "rgba(34, 197, 94, 0.72)"
        if c >= o
        else "rgba(239, 68, 68, 0.72)"
        for o, c in zip(opens, closes)
    ]
    fig.add_trace(
        go.Bar(
            x=dates,
            y=volumes,
            name="Khối lượng",
            marker_color=vol_colors,
            hovertemplate="Ngày %{x}<br>KL: %{y:,.0f} CP<extra></extra>",
        ),
        row=2,
        col=1,
    )
    if any(v is not None for v in ma20_vol):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=ma20_vol,
                mode="lines",
                name="TB20 Vol",
                line=dict(color="#f59e0b", width=1.6),
                hovertemplate="TB20 Vol: %{y:,.0f} CP<extra></extra>",
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#081827",
        plot_bgcolor="#081827",
        height=610,
        margin=dict(l=16, r=24, t=48, b=20),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1.0,
            bgcolor="rgba(8, 24, 39, 0.75)",
            font=dict(size=11, color="#dbeafe"),
        ),
    )
    # Dùng trục category để bỏ khoảng trống ngày Thứ Bảy/Chủ Nhật/Ngày lễ
    fig.update_xaxes(
        type="category",
        categoryorder="category ascending",
        nticks=12,
        showgrid=True,
        gridcolor="rgba(41, 69, 99, 0.32)",
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(41, 69, 99, 0.35)",
        tickformat=",.0f",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(41, 69, 99, 0.25)",
        tickformat=",.0f",
        row=2,
        col=1,
    )
    return fig