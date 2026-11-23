"""Regression: ein Symbol-Cache für alle Module (F20).

``tools/cache.py`` legte eine zweite ``_SYMBOL_CACHE``-Instanz an und
überschrieb damit den Import aus ``tools/base.py``. ``_invalidate_cache`` räumte
nur eine davon — capsule/export/completion lieferten nach Edits veraltete
Symbole.
"""

from __future__ import annotations

from code_intel.tools import base, cache, symbols_extractor


def test_one_cache_instance_everywhere():
    assert base._SYMBOL_CACHE is cache._SYMBOL_CACHE
    assert symbols_extractor._SYMBOL_CACHE is base._SYMBOL_CACHE


def test_set_in_cache_is_visible_in_base():
    base._SYMBOL_CACHE.clear()
    cache._set_cache("probe.py|1|python|||False", [{"name": "alpha"}])
    assert "probe.py|1|python|||False" in base._SYMBOL_CACHE
    base._SYMBOL_CACHE.clear()


def test_invalidate_clears_the_shared_cache(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    key = f"{target}|1|python|||False"
    cache._set_cache(key, [{"name": "alpha"}])
    assert key in base._SYMBOL_CACHE

    cache._invalidate_cache(str(target))
    assert key not in base._SYMBOL_CACHE
