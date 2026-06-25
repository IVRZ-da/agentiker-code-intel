"""Tests for tools/base.py — parser init, cache, and infrastructure."""

from __future__ import annotations

from code_intel.tools.base import (
    _SYMBOL_CACHE,
    _get_language,
    _get_parser,
    _init_languages,
    clear_symbol_cache,
    detect_language,
)


class TestInitLanguages:
    """_init_languages — initialization paths."""

    def test_init_twice(self):
        """Calling _init_languages twice doesn't crash."""
        _init_languages()
        _init_languages()  # should be no-op
        assert True


class TestGetLanguage:
    """_get_language — language resolution."""

    def test_known_language(self):
        """Python language object is available after init."""
        _init_languages()
        lang = _get_language("python")
        assert lang is not None

    def test_unknown_language(self):
        """Unknown language returns None."""
        _init_languages()
        lang = _get_language("brainfuck")
        assert lang is None

    def test_none_language(self):
        """None language returns None."""
        _init_languages()
        lang = _get_language(None)
        assert lang is None


class TestGetParser:
    """_get_parser — parser resolution."""

    def test_known_parser(self):
        """Python parser is available after init."""
        _init_languages()
        parser = _get_parser("python")
        assert parser is not None

    def test_unknown_parser(self):
        """Unknown language returns None."""
        _init_languages()
        parser = _get_parser("brainfuck")
        assert parser is None


class TestDetectLanguage:
    """detect_language — file extension detection."""

    def test_python(self):
        _init_languages()
        assert detect_language("test.py") == "python"

    def test_typescript(self):
        _init_languages()
        assert detect_language("test.ts") == "typescript"

    def test_unknown_ext(self):
        _init_languages()
        assert detect_language("test.xyz") is None

    def test_no_extension(self):
        _init_languages()
        assert detect_language("Makefile") is None


class TestSymbolCache:
    """_SYMBOL_CACHE and clear_symbol_cache."""

    def test_clear_empty_cache(self):
        """Clearing an empty cache doesn't crash."""
        clear_symbol_cache()
        assert len(_SYMBOL_CACHE) == 0

    def test_clear_populated_cache(self):
        """Clearing a populated cache resets it."""
        _SYMBOL_CACHE["test_key"] = "test_value"
        clear_symbol_cache()
        assert len(_SYMBOL_CACHE) == 0
