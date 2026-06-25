"""Tests for formatter functions: _format_definitions, _format_references."""

from code_intel.lsp_bridge import _format_definitions, _format_references


class TestFormatDefinitions:
    def test_empty_returns_no_definition(self):
        result = _format_definitions([])
        assert "No definition found" in result

    def test_none_returns_no_definition(self):
        result = _format_definitions(None)
        assert "No definition found" in result

    def test_single_definition(self):
        defs = [{"file": "/tmp/project/src/app.ts", "line": 42, "text": "  myFunc() {"}]
        result = _format_definitions(defs)
        assert "app.ts" in result
        assert "42" in result
        assert "myFunc" in result

    def test_multiple_definitions(self):
        defs = [
            {"file": "/tmp/project/src/a.ts", "line": 10, "text": "  fnA()"},
            {"file": "/tmp/project/src/b.ts", "line": 20, "text": "  fnB()"},
        ]
        result = _format_definitions(defs)
        assert "a.ts" in result
        assert "b.ts" in result

    def test_definition_with_context(self):
        defs = [
            {
                "file": "/tmp/project/src/app.ts",
                "line": 5,
                "text": "  myFunc() {",
                "context": ["  // comment", "  myFunc() {", "    return 1;"],
            }
        ]
        result = _format_definitions(defs)
        assert "comment" in result


class TestFormatReferences:
    def test_empty_refs(self):
        result = _format_references([], {})
        assert "No references found" in result

    def test_none_refs(self):
        result = _format_references(None, {})
        assert "No references found" in result

    def test_single_file_refs(self):
        refs = [{"file": "/tmp/src/app.ts", "line": 10, "text": "doSomething()"}]
        by_file = {"/tmp/src/app.ts": refs}
        result = _format_references(refs, by_file)
        assert "app.ts" in result
        assert "doSomething" in result
        assert "reference" in result

    def test_multiple_files(self):
        refs = [
            {"file": "/tmp/src/a.ts", "line": 1, "text": "fn()"},
            {"file": "/tmp/src/b.ts", "line": 2, "text": "fn()"},
        ]
        by_file = {"/tmp/src/a.ts": [refs[0]], "/tmp/src/b.ts": [refs[1]]}
        result = _format_references(refs, by_file)
        assert "a.ts" in result
        assert "b.ts" in result
        assert "reference" in result
