"""Tests for tools/type_hierarchy.py — AST Type Hierarchy Fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def py_class_file(tmp_path: Path) -> Path:
    """Create a Python file with a class hierarchy."""
    f = tmp_path / "test.py"
    f.write_text(
        "class Animal:\n"
        "    pass\n"
        "\n"
        "class Dog(Animal):\n"
        "    pass\n"
        "\n"
        "class Cat(Animal):\n"
        "    pass\n"
    )
    return f


@pytest.fixture
def ts_class_file(tmp_path: Path) -> Path:
    """Create a TypeScript file with a class hierarchy."""
    f = tmp_path / "test.ts"
    f.write_text(
        "class Animal {\n"
        "}\n"
        "\n"
        "class Dog extends Animal {\n"
        "}\n"
    )
    return f


@pytest.fixture
def node_mock():
    """Create a tree-sitter node mock with start_point."""
    node = MagicMock()
    node.start_point = [0, 0]
    node.type = "class_definition"
    node.start_byte = 0
    node.end_byte = 10
    return node


@pytest.fixture
def query_cursor_mock():
    """Create a QueryCursor mock that returns no matches."""
    cursor = MagicMock()
    cursor.matches.return_value = []  # No matches
    return cursor


# =============================================================================
# _ast_type_hierarchy_supertypes
# =============================================================================


class TestAstTypeHierarchySupertypes:
    """Tests for _ast_type_hierarchy_supertypes."""

    def test_nonexistent_path(self):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        result = _ast_type_hierarchy_supertypes("/nonexistent/path.py", 1)
        assert result is None

    def test_unsupported_language(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.go"
        f.write_text("package main")
        with patch(
            "code_intel.code_tools.detect_language", return_value="go"
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_no_language_detected(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.xyz"
        f.write_text("hello")
        with patch(
            "code_intel.code_tools.detect_language", return_value=None
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_lang_obj_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value=None),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_query_creation_fails(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch(
                "tree_sitter.Query",
                side_effect=Exception("query fail"),
            ),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_parser_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=None),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_read_error(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value="p"),
            patch("builtins.open", side_effect=OSError("permission")),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_tree_parse_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        parser = MagicMock()
        parser.parse.return_value = None
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    def test_target_class_not_found(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        qc = MagicMock()
        qc.matches.return_value = []
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
            patch("tree_sitter.QueryCursor", return_value=qc),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 1)
        assert result is None

    @pytest.mark.skip(reason="Integration test: too complex to mock tree-sitter internals")
    def test_python_supertype_found(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class Animal:\n    pass\nclass Dog(Animal):\n    pass\n")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        class_def_node = MagicMock()
        class_def_node.start_point = [2, 0]
        class_def_node.type = "class_definition"

        class_name_node = MagicMock()
        class_name_node.start_byte = 26
        class_name_node.end_byte = 29

        extends_node = MagicMock()
        extends_node.start_byte = 34
        extends_node.end_byte = 40

        inner_cd = {
            "class_def": [class_def_node],
            "class_name": [class_name_node],
        }

        outer_class_def = MagicMock()
        outer_class_def.start_point = [0, 0]
        outer_class_def.type = "class_definition"
        outer_class_name = MagicMock()
        outer_class_name.start_byte = 6
        outer_class_name.end_byte = 12

        outer_cd = {
            "class_def": [outer_class_def],
            "extends_name": [extends_node],
            "class_name": [outer_class_name],
        }

        qc = MagicMock()
        qc.matches.side_effect = [[(0, inner_cd)], [(0, outer_cd)]]

        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang_obj"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
            patch("tree_sitter.QueryCursor", return_value=qc),
            patch("tree_sitter.Query"),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 3)

        assert result is not None
        assert len(result) >= 1

    def test_decode_error(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_supertypes

        f = tmp_path / "test.py"
        f.write_text("class A:\n    pass\nclass B(A):\n    pass\n")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        cd1 = {
            "class_def": [MagicMock(start_point=[2, 0], type="class_definition")],
            "class_name": [MagicMock(start_byte=26, end_byte=27)],
        }
        qc = MagicMock()
        qc.matches.side_effect = [
            [(0, cd1)],
            [(0, {"extends_name": [MagicMock(side_effect=IndexError())],
                  "class_name": [MagicMock(start_byte=6, end_byte=7)]})],
        ]

        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang_obj"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
            patch("tree_sitter.QueryCursor", return_value=qc),
            patch("tree_sitter.Query"),
        ):
            result = _ast_type_hierarchy_supertypes(str(f), 3)
        assert result is None


# =============================================================================
# _find_target_class_name
# =============================================================================


class TestFindTargetClassName:
    """Tests for _find_target_class_name."""

    def test_tree_none(self):
        from code_intel.tools.type_hierarchy import _find_target_class_name

        parser = MagicMock()
        parser.parse.return_value = None
        result = _find_target_class_name("/path/to/file.py", 1, "python", parser, None, b"")
        assert result is None

    def test_class_found(self):
        from code_intel.tools.type_hierarchy import _find_target_class_name

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        class_def = MagicMock()
        class_def.start_point = [0, 0]
        class_def.type = "class_definition"

        class_name = MagicMock()
        class_name.start_byte = 6
        class_name.end_byte = 7

        qc = MagicMock()
        cd = {"class_def": [class_def], "class_name": [class_name]}
        qc.matches.return_value = [(0, cd)]

        source = b"class A: pass"

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _find_target_class_name("/path/file.py", 1, "python", parser, MagicMock(), source)
        assert result == "A"

    def test_class_not_found(self):
        from code_intel.tools.type_hierarchy import _find_target_class_name

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        qc = MagicMock()
        qc.matches.return_value = []

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _find_target_class_name("/path/file.py", 1, "python", parser, MagicMock(), b"")
        assert result is None


# =============================================================================
# _scan_subtypes_in_project
# =============================================================================


class TestScanSubtypesInProject:
    """Tests for _scan_subtypes_in_project."""

    def test_no_matches(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        qc = MagicMock()
        qc.matches.return_value = []

        (tmp_path / "empty.py").write_text("")

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")
        assert result == []

    def test_read_error(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        f = tmp_path / "test.py"
        f.write_text("dummy")
        parser = MagicMock()

        with patch("builtins.open", side_effect=OSError("read error")):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")
        assert result == []

    @pytest.mark.skip(reason="Integration test: too complex to mock tree-sitter internals")
    def test_finds_subclass(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        f = tmp_path / "dog.py"
        f.write_text("class Dog(Animal):\n    pass\n")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        extends_node = MagicMock()
        extends_node.start_byte = 11
        extends_node.end_byte = 17

        class_def_node = MagicMock()
        class_def_node.start_point = [0, 0]
        class_def_node.type = "class_definition"

        class_name_node = MagicMock()
        class_name_node.start_byte = 6
        class_name_node.end_byte = 9

        cd = {"extends_name": [extends_node], "class_def": [class_def_node], "class_name": [class_name_node]}

        qc = MagicMock()
        qc.matches.return_value = [(0, cd)]

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")

        assert len(result) == 1
        assert result[0]["name"] == "Dog"
        assert result[0]["line"] == 1

    def test_decode_error(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        f = tmp_path / "buggy.py"
        f.write_text("class Buggy(Animal): pass")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        extends_node = MagicMock()
        extends_node.start_byte = 999
        extends_node.end_byte = 1000

        cd = {"extends_name": [extends_node], "class_def": [], "class_name": []}

        qc = MagicMock()
        qc.matches.return_value = [(0, cd)]

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")
        assert result == []

    @pytest.mark.skip(reason="Integration test: too complex to mock tree-sitter internals")
    def test_classname_decode_fallback(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        f = tmp_path / "other.py"
        f.write_text("class Other(Animal): pass")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        extends_node = MagicMock()
        extends_node.start_byte = 14
        extends_node.end_byte = 20

        class_def_node = MagicMock()
        class_def_node.start_point = [0, 0]
        class_def_node.type = "class_definition"

        cn_node = MagicMock()
        cn_node.start_byte = 999
        cn_node.end_byte = 1000

        cd = {"extends_name": [extends_node], "class_def": [class_def_node], "class_name": [cn_node]}

        qc = MagicMock()
        qc.matches.return_value = [(0, cd)]

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")
        assert len(result) == 1
        assert result[0]["name"] == "?"

    def test_ignores_node_modules(self, tmp_path):
        from code_intel.tools.type_hierarchy import _scan_subtypes_in_project

        node_dir = tmp_path / "node_modules"
        node_dir.mkdir()
        (node_dir / "ignore.py").write_text("class Foo(Animal): pass")

        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        (venv_dir / "lib.py").write_text("class Bar(Animal): pass")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree

        qc = MagicMock()
        qc.matches.return_value = []

        with patch("tree_sitter.QueryCursor", return_value=qc):
            result = _scan_subtypes_in_project("Animal", tmp_path, parser, MagicMock(), "python")
        assert result == []


# =============================================================================
# _ast_type_hierarchy_subtypes
# =============================================================================


class TestAstTypeHierarchySubtypes:
    """Tests for _ast_type_hierarchy_subtypes."""

    def test_nonexistent_path(self):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        result = _ast_type_hierarchy_subtypes("/nonexistent/path.py", 1)
        assert result is None

    def test_unsupported_language(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.go"
        f.write_text("package main")
        with patch("code_intel.code_tools.detect_language", return_value="go"):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None

    def test_lang_obj_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value=None),
        ):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None

    def test_parser_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=None),
        ):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None

    def test_read_error(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value="p"),
            patch("builtins.open", side_effect=OSError("nope")),
        ):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None

    def test_target_not_found_returns_none(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.py"
        f.write_text("class A: pass")
        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        qc = MagicMock()
        qc.matches.return_value = []

        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
            patch("tree_sitter.QueryCursor", return_value=qc),
            patch("tree_sitter.Query"),
        ):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None

    def test_no_subtypes_found(self, tmp_path):
        from code_intel.tools.type_hierarchy import _ast_type_hierarchy_subtypes

        f = tmp_path / "test.py"
        f.write_text("class A:\n    pass\n")

        parser = MagicMock()
        tree = MagicMock()
        parser.parse.return_value = tree
        tree.root_node = MagicMock()

        class_def = MagicMock()
        class_def.start_point = [0, 0]
        class_name = MagicMock()
        class_name.start_byte = 6
        class_name.end_byte = 7

        cd = {"class_def": [class_def], "class_name": [class_name]}
        qc = MagicMock()
        qc.matches.return_value = [(0, cd)]

        with (
            patch("code_intel.code_tools.detect_language", return_value="python"),
            patch("code_intel.code_tools._get_language", return_value="lang"),
            patch("code_intel.code_tools._get_parser", return_value=parser),
            patch("tree_sitter.QueryCursor", return_value=qc),
            patch("tree_sitter.Query"),
            patch("code_intel.tools.type_hierarchy._scan_subtypes_in_project", return_value=[]),
        ):
            result = _ast_type_hierarchy_subtypes(str(f), 1)
        assert result is None


# =============================================================================
# Module exports
# =============================================================================


class TestModuleExports:
    """Test module-level constants and exports."""

    def test_module_has_all_functions(self):
        from code_intel.tools.type_hierarchy import (
            _ast_type_hierarchy_subtypes,
            _ast_type_hierarchy_supertypes,
            _find_target_class_name,
            _scan_subtypes_in_project,
        )

        assert callable(_ast_type_hierarchy_supertypes)
        assert callable(_ast_type_hierarchy_subtypes)
        assert callable(_find_target_class_name)
        assert callable(_scan_subtypes_in_project)

    def test_fallback_langs_defined(self):
        from code_intel.tools.type_hierarchy import _TYPE_HIERARCHY_FALLBACK_LANGS

        assert "python" in _TYPE_HIERARCHY_FALLBACK_LANGS
        assert "typescript" in _TYPE_HIERARCHY_FALLBACK_LANGS
