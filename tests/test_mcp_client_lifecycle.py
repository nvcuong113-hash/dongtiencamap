from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

from quants import mcp_chat


class _Content:
    def __init__(self, role="user", parts=None):
        self.role = role
        self.parts = list(parts or [])


class _Part:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def from_text(cls, text):
        return cls(text=text)

    @classmethod
    def from_function_call(cls, name, args):
        return cls(function_call=SimpleNamespace(name=name, args=args))

    @classmethod
    def from_function_response(cls, name, response):
        return cls(function_response=SimpleNamespace(name=name, response=response))


class _FakeAsyncClient:
    def __init__(self, client):
        self.client = client
        self.models = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.client.closed = True

    async def generate_content(self, *, model, contents, config):
        if self.client.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.client.call_count += 1
        if getattr(config, "tools", None):
            call = SimpleNamespace(name="data_health", args={})
            content = _Content(role="model", parts=[_Part(function_call=call)])
            return SimpleNamespace(function_calls=[call], candidates=[SimpleNamespace(content=content)], text=None)
        return SimpleNamespace(text="Báo cáo tổng hợp thành công.", candidates=[])


class _FakeClient:
    def __init__(self, api_key, http_options=None):
        self.closed = False
        self.call_count = 0
        self.aio = _FakeAsyncClient(self)


class _FakeTool:
    name = "data_health"
    description = "Read-only data health"
    inputSchema = {"type": "object", "properties": {}}


class _FakeSession:
    async def call_tool(self, name, args):
        assert name == "data_health"
        return SimpleNamespace(content=[SimpleNamespace(text='{"ok":true,"data":{"symbols_checked":1}}')])


def test_gemini_final_synthesis_uses_fresh_client(monkeypatch):
    clients = []

    def client_factory(api_key, http_options=None):
        if clients:
            # The tool-loop client must already have left its context before a new
            # client is created for final synthesis.
            assert clients[-1].closed is True
        client = _FakeClient(api_key, http_options)
        clients.append(client)
        return client

    types_mod = ModuleType("google.genai.types")
    types_mod.Content = _Content
    types_mod.Part = _Part
    types_mod.FunctionDeclaration = lambda **kwargs: SimpleNamespace(**kwargs)
    types_mod.Tool = lambda **kwargs: SimpleNamespace(**kwargs)
    types_mod.HttpOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    types_mod.HttpRetryOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    types_mod.GenerateContentConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    types_mod.AutomaticFunctionCallingConfig = lambda **kwargs: SimpleNamespace(**kwargs)

    genai_mod = ModuleType("google.genai")
    genai_mod.Client = client_factory
    genai_mod.types = types_mod
    google_mod = ModuleType("google")
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    monkeypatch.setattr(mcp_chat, "_setting", lambda key: "test-key" if key == "GEMINI_API_KEY" else "")
    monkeypatch.setattr(mcp_chat, "_provider_is_cooling_down", lambda name: False)
    monkeypatch.setattr(mcp_chat, "GEMINI_MODELS", ["fake-model"])
    monkeypatch.setattr(mcp_chat, "MAX_TOOL_ROUNDS", 2)
    monkeypatch.setattr(mcp_chat, "MAX_TOOL_CALLS", 2)

    result, model = asyncio.run(mcp_chat._gemini_with_session(_FakeSession(), [_FakeTool()], "Tổng hợp dữ liệu"))

    assert result == "Báo cáo tổng hợp thành công."
    assert model == "fake-model"
    assert len(clients) == 2
    assert clients[0].closed is True
    assert clients[1].closed is True
