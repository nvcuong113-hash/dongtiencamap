"""Runtime MCP protocol smoke test; skipped where the MCP SDK isn't installed."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from quants import db

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(MCP_AVAILABLE, "MCP SDK is not installed in this test environment")
@unittest.skipIf(sys.platform == "emscripten", "Subprocess transport is unavailable")
class MCPRuntimeSmokeTests(unittest.TestCase):
    def test_local_mcp_server_starts_and_exposes_all_read_only_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        with tempfile.TemporaryDirectory(prefix="smf-mcp-test-") as tmp_dir:
            database_path = Path(tmp_dir) / "mcp_runtime.sqlite3"
            original_db_path = db.DB_PATH
            db.DB_PATH = database_path
            try:
                db.init_db()
                root = Path(__file__).resolve().parents[1]
                env = os.environ.copy()
                env["QUANTS_DB_PATH"] = str(database_path)
                env["PYTHONPATH"] = str(root) + (
                    os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
                )
                params = StdioServerParameters(
                    command=sys.executable, args=["-m", "quants.mcp_server"], env=env
                )

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
                            self.assertTrue(expected <= names)
                            response = await session.call_tool("data_health", {})
                            texts = [item.text for item in response.content if getattr(item, "text", None)]
                            self.assertTrue(texts)
                            payload = json.loads(texts[0])
                            self.assertTrue(payload["ok"])
                            self.assertEqual(payload["data"]["symbols_checked"], 0)

                asyncio.run(exercise())
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
