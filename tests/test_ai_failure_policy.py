import sys
import types as pytypes

import pytest
from quants import ai_engine


@pytest.fixture(autouse=True)
def reset_ai_provider_state(monkeypatch):
    monkeypatch.setattr(ai_engine, '_PROVIDER_COOLDOWN_UNTIL', {'Gemini': 0.0, 'OpenAI': 0.0})
    monkeypatch.setattr(ai_engine, '_PROVIDER_COOLDOWN_REASON', {'Gemini': '', 'OpenAI': ''})
    monkeypatch.setattr(ai_engine, '_PROVIDER_COOLDOWN_SECONDS', 300)
    monkeypatch.setattr(ai_engine.time, 'sleep', lambda *_: None)


def test_provider_errors_are_classified_without_calling_everything_overload():
    assert ai_engine._provider_error_kind('429 RESOURCE_EXHAUSTED quota exceeded') == 'quota'
    assert ai_engine._provider_error_kind('429 Rate limit reached') == 'rate_limit'
    assert ai_engine._provider_error_kind('503 UNAVAILABLE') == 'overload'
    assert ai_engine._provider_error_kind('Read timed out') == 'timeout'
    assert ai_engine._provider_error_kind('401 invalid API key') == 'auth'
    assert ai_engine._provider_error_kind('invalid JSON schema') == 'unknown'


def test_user_facing_error_distinguishes_rate_limit_quota_auth_and_timeout():
    assert 'hạn mức/quota' in ai_engine._summarize_provider_failure('RESOURCE_EXHAUSTED')
    assert 'giới hạn tốc độ' in ai_engine._summarize_provider_failure('429 Rate limit reached')
    assert 'API key' in ai_engine._summarize_provider_failure('401 API key invalid')
    assert 'hết thời gian chờ' in ai_engine._summarize_provider_failure('Read timeout')


def _fake_google(monkeypatch, generate_content):
    class FakeHttpOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeHttpRetryOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeGenerateConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    types_mod = pytypes.ModuleType('google.genai.types')
    types_mod.HttpOptions = FakeHttpOptions
    types_mod.HttpRetryOptions = FakeHttpRetryOptions
    types_mod.GenerateContentConfig = FakeGenerateConfig
    genai_mod = pytypes.ModuleType('google.genai')
    state = {'http_options': None, 'calls': []}

    class FakeModels:
        def generate_content(self, **kwargs):
            state['calls'].append(kwargs)
            return generate_content(kwargs, state)

    class FakeClient:
        def __init__(self, **kwargs):
            state['http_options'] = kwargs.get('http_options')
            self.models = FakeModels()

    genai_mod.Client = FakeClient
    genai_mod.types = types_mod
    google_mod = pytypes.ModuleType('google')
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, 'google', google_mod)
    monkeypatch.setitem(sys.modules, 'google.genai', genai_mod)
    monkeypatch.setitem(sys.modules, 'google.genai.types', types_mod)
    monkeypatch.setattr(ai_engine, '_setting', lambda name, default='': 'test-key' if name == 'GEMINI_API_KEY' else default)
    return state


def test_gemini_request_has_bounded_timeout_and_retries_transient_error_once(monkeypatch):
    count = {'n': 0}

    def generate(kwargs, state):
        count['n'] += 1
        if count['n'] == 1:
            raise RuntimeError('503 UNAVAILABLE')
        return pytypes.SimpleNamespace(text='reply')

    state = _fake_google(monkeypatch, generate)
    monkeypatch.setattr(ai_engine, 'GEMINI_MODELS', ['gemini-test'])
    text, model = ai_engine._call_gemini('short prompt')
    assert (text, model) == ('reply', 'gemini-test')
    assert count['n'] == 2
    assert state['http_options'].kwargs['timeout'] == 25_000
    assert state['http_options'].kwargs['retry_options'].kwargs['attempts'] == 1


def test_gemini_rate_limit_opens_temporary_circuit_without_model_fanout(monkeypatch):
    def generate(kwargs, state):
        raise RuntimeError('429 Rate limit reached')

    state = _fake_google(monkeypatch, generate)
    monkeypatch.setattr(ai_engine, 'GEMINI_MODELS', ['gemini-a', 'gemini-b', 'gemini-c'])
    with pytest.raises(ai_engine.AIUnavailable, match='giới hạn tốc độ'):
        ai_engine._call_gemini('prompt')
    assert len(state['calls']) == 1
    assert ai_engine._provider_is_cooling_down('Gemini')
    with pytest.raises(ai_engine.AIUnavailable, match='tạm nghỉ'):
        ai_engine._call_gemini('prompt')
    assert len(state['calls']) == 1


def test_openai_client_uses_short_timeout_and_no_hidden_sdk_retries(monkeypatch):
    state = {'client_kwargs': None, 'calls': 0}
    class FakeCompletions:
        def create(self, **kwargs):
            state['calls'] += 1
            raise RuntimeError('503 server unavailable')
    class FakeClient:
        def __init__(self, **kwargs):
            state['client_kwargs'] = kwargs
            self.chat = pytypes.SimpleNamespace(completions=FakeCompletions())
    openai_mod = pytypes.ModuleType('openai')
    openai_mod.OpenAI = FakeClient
    monkeypatch.setitem(sys.modules, 'openai', openai_mod)
    monkeypatch.setattr(ai_engine, '_setting', lambda name, default='': 'test-key' if name == 'OPENAI_API_KEY' else default)
    with pytest.raises(RuntimeError, match='lỗi sau số lần thử giới hạn'):
        ai_engine._call_openai('prompt')
    assert state['client_kwargs']['timeout'] == 25.0
    assert state['client_kwargs']['max_retries'] == 0
    assert state['calls'] == 2


def test_chat_lock_rejects_overlapping_chat_without_calling_provider(monkeypatch):
    lock = ai_engine._CHAT_REQUEST_LOCK
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(ai_engine.AIUnavailable, match='đang xử lý'):
            ai_engine.generate_chat_response('Gemini', 'prompt')
    finally:
        lock.release()


def test_fallback_switches_provider_after_primary_overload_without_model_fanout(monkeypatch):
    calls = []
    def fail_gemini(prompt, json_mode=False):
        calls.append('Gemini')
        raise RuntimeError('503 UNAVAILABLE')
    def succeed_openai(prompt, json_mode=False):
        calls.append('OpenAI')
        return 'answer', 'gpt-fixture'
    monkeypatch.setattr(ai_engine, '_call_gemini', fail_gemini)
    monkeypatch.setattr(ai_engine, '_call_openai', succeed_openai)
    monkeypatch.setattr(ai_engine, 'event', lambda *args, **kwargs: None)
    result = ai_engine._generate_with_fallback('Gemini', 'prompt')
    assert result == ('answer', 'OpenAI', 'gpt-fixture')
    assert calls == ['Gemini', 'OpenAI']


def test_gemini_timeout_goes_to_provider_fallback_without_retrying_all_models(monkeypatch):
    def generate(kwargs, state):
        raise RuntimeError('Read timed out')

    state = _fake_google(monkeypatch, generate)
    monkeypatch.setattr(ai_engine, 'GEMINI_MODELS', ['gemini-a', 'gemini-b', 'gemini-c'])
    with pytest.raises(RuntimeError, match=r'lỗi tạm thời \(timeout\)'):
        ai_engine._call_gemini('prompt')
    assert len(state['calls']) == 1
