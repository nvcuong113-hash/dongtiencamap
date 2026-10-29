"""Runtime MCP protocol smoke test; skipped where the MCP SDK isn't installed."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from quants import db


@pytest.mark.skipif(sys.platform == "emscripten", reason="Subprocess transport is unavailable")
def test_local_mcp_server_starts_and_exposes_all_read_only_tools(tmp_path, monkeypatch):
    database_path = tmp_path / "mcp_runtime.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    db.init_db()

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["QUANTS_DB_PATH"] = str(database_path)
    env["PYTHONPATH"] = str(root) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    params = StdioServerParameters(command=sys.executable, args=["-m", "quants.mcp_server"], env=env)

    async def exercise():
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                expected = {
                    "get_radar", "data_health", "explain_exclusion", "get_system_logs",
                    "compare_sources", "get_ticker_history", "get_flow_features", "media_search",
                    "macro_snapshot", "run_backtest", "get_model_performance",
                }
                assert expected <= names
                response = await session.call_tool("data_health", {})
                texts = [item.text for item in response.content if getattr(item, "text", None)]
                assert texts
                payload = json.loads(texts[0])
                assert payload["ok"] is True
                assert payload["data"]["symbols_checked"] == 0

    asyncio.run(exercise())
