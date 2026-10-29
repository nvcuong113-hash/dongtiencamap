"""Local stdio MCP server used by QUANTS PRO's in-app AI assistant.

Run as: python -m quants.mcp_server
The tool surface is read-only and backed by the existing local QUANTS database.
"""
from __future__ import annotations

from .mcp_tools import dispatch_tool


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only in deployment environment
        raise SystemExit("Thiếu package MCP SDK; cài dependencies trong requirements.txt.") from exc

    server = FastMCP("QUANTS PRO Internal Data Tools")

    @server.tool()
    def get_radar(limit: int = 30, symbol: str = "") -> str:
        """Lấy Radar mới nhất đã lưu theo mã; không gọi lại nhà cung cấp dữ liệu."""
        return dispatch_tool("get_radar", {"limit": limit, "symbol": symbol})

    @server.tool()
    def data_health(symbol: str = "") -> str:
        """Kiểm tra timestamp nguồn, freshness, feature thiếu và data gate của từng mã."""
        return dispatch_tool("data_health", {"symbol": symbol})

    @server.tool()
    def explain_exclusion(symbol: str) -> str:
        """Giải thích bằng chứng đã lưu về lý do một mã bị chặn/loại khỏi dự báo."""
        return dispatch_tool("explain_exclusion", {"symbol": symbol})

    @server.tool()
    def get_system_logs(component: str = "", level: str = "", limit: int = 30) -> str:
        """Truy vấn log gần đây theo component/level; bí mật được che trước khi trả về."""
        return dispatch_tool("get_system_logs", {"component": component, "level": level, "limit": limit})

    @server.tool()
    def compare_sources(symbol: str, limit: int = 10) -> str:
        """Đối chiếu metadata nguồn đã lưu, timestamp và freshness; không tự refetch nguồn."""
        return dispatch_tool("compare_sources", {"symbol": symbol, "limit": limit})

    @server.tool()
    def get_ticker_history(symbol: str, limit: int = 40) -> str:
        """Lấy lịch sử snapshot theo phiên của một mã từ database hiện có."""
        return dispatch_tool("get_ticker_history", {"symbol": symbol, "limit": limit})

    @server.tool()
    def get_flow_features(symbol: str) -> str:
        """Lấy feature dòng tiền, VWAP, RS, tín hiệu đáy/đỉnh và tình trạng dữ liệu."""
        return dispatch_tool("get_flow_features", {"symbol": symbol})

    @server.tool()
    def media_search(query_text: str = "", symbol: str = "", hours: int = 48, limit: int = 10) -> str:
        """Tìm bài báo đã lưu trong kho media hiện hữu; tool không truy cập domain bên ngoài."""
        return dispatch_tool("media_search", {"query_text": query_text, "symbol": symbol, "hours": hours, "limit": limit})

    @server.tool()
    def macro_snapshot(factor: str = "") -> str:
        """Lấy snapshot vĩ mô gần nhất và metadata nguồn/unit đã lưu."""
        return dispatch_tool("macro_snapshot", {"factor": factor})

    @server.tool()
    def run_backtest(horizon: int = 3, symbol: str = "", limit: int = 300) -> str:
        """Tóm tắt các dự báo đã chốt; không retrain và không thay đổi mô hình đang chạy."""
        return dispatch_tool("run_backtest", {"horizon": horizon, "symbol": symbol, "limit": limit})

    @server.tool()
    def get_model_performance(horizon: int = 3) -> str:
        """Đọc bảng hiệu năng đã lưu theo horizon; không tự tạo/diễn giải sample mới."""
        return dispatch_tool("get_model_performance", {"horizon": horizon})

    # MCP stdio reserves stdout for JSON-RPC framing; don't print status messages here.
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
