"""Regression: pre_llm_call liest den Host-Vertrag (F17).

Der Host ruft ``pre_llm_call`` mit ``user_message`` und ``conversation_history``
auf (``hermes_cli/hooks.py::_DEFAULT_PAYLOADS``). 0.6.40 las nur ``messages`` —
der Hook injizierte nie Kontext und meldete keinen Fehler.
"""

from __future__ import annotations

import pytest

from code_intel import hooks


@pytest.fixture()
def fake_fetch(monkeypatch):
    """Ersetzt die teure Symbol-Abfrage durch eine feste Antwort."""
    calls: list[str] = []

    def _fake(ref):
        calls.append(ref)
        return f"CTX für {ref}"

    monkeypatch.setattr(hooks, "_process_file_ref", _fake)
    hooks._pre_llm_call_cache.clear()
    return calls


def test_user_message_is_used(fake_fetch):
    out = hooks.on_pre_llm_call(
        user_message="schau bitte in app/main.py rein",
        conversation_history=[{"role": "user", "content": "früher"}],
    )
    assert out and "app/main.py" in out
    assert fake_fetch == ["app/main.py"]


def test_messages_still_work(fake_fetch):
    out = hooks.on_pre_llm_call(
        messages=[{"role": "user", "content": "lies tools/base.py"}],
    )
    assert out and "tools/base.py" in out


def test_conversation_history_is_last_resort(fake_fetch):
    out = hooks.on_pre_llm_call(
        conversation_history=[{"role": "user", "content": "und jetzt bugfix in hooks.py"}],
    )
    assert out and "hooks.py" in out


def test_without_any_message_no_output(fake_fetch):
    assert hooks.on_pre_llm_call() is None
    assert fake_fetch == []


def test_non_code_message_is_ignored(fake_fetch):
    assert hooks.on_pre_llm_call(user_message="wie ist das Wetter?") is None
    assert fake_fetch == []


def test_dict_user_message(fake_fetch):
    out = hooks.on_pre_llm_call(user_message={"content": "patch in lsp/server.py"})
    assert out and "lsp/server.py" in out
