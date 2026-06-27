from __future__ import annotations

from typing import Optional

from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

# ---------------------------------------------------------------------------
# AST Type Hierarchy (Fallback für Python/TS ohne LSP-Support)
# ---------------------------------------------------------------------------

# Language → AST query for class extends/implements detection
_TYPE_HIERARCHY_FALLBACK_LANGS = {"python", "typescript", "tsx", "javascript"}

_PYTHON_CLASS_EXTENDS = """
(class_definition
    name: (identifier) @class_name
    (argument_list
        (identifier) @extends_name
    )
) @class_def
"""

_TS_CLASS_EXTENDS = """
; class Foo extends Bar { }
(class_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def

; interface Foo extends Bar { }
(interface_declaration
    name: (type_identifier) @class_name
    (class_heritage
        (identifier) @extends_name
    )
) @class_def
"""


def _ast_type_hierarchy_supertypes(path: str, line: int) -> Optional[list]:
    """AST-basierte Supertypes (Eltern-Klassen/Interfaces).

    Funktioniert für Python und TypeScript/JS als Fallback
    wenn der LSP-Server TypeHierarchy nicht unterstützt.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    from ..code_tools import _get_language, _get_parser, detect_language
    lang_key = detect_language(str(target))
    if not lang_key or lang_key not in _TYPE_HIERARCHY_FALLBACK_LANGS:
        return None

    if lang_key == "python":
        query_source = _PYTHON_CLASS_EXTENDS
    else:
        query_source = _TS_CLASS_EXTENDS

    try:
        from tree_sitter import Query, QueryCursor
        lang_obj = _get_language(lang_key)
        if lang_obj is None:
            return None
        query = Query(lang_obj, query_source)
    except Exception:
        logger.debug("type_hierarchy: root resolution failed")
        return None

    parser = _get_parser(lang_key)
    if parser is None:
        return None

    try:
        with open(str(target), "rb") as f:
            source = f.read()
    except (OSError, IOError):
        return None

    tree = parser.parse(source)
    if tree is None:
        return None

    # Finde die Klasse an der angegebenen Line
    target_class_name = None
    qc = QueryCursor(query)
    for _pi, cd in qc.matches(tree.root_node):
        for _n in cd.get("class_def", []):
            start_line = _n.start_point[0] if hasattr(_n, "start_point") else 0
            if start_line == line - 1:  # 0-based vs 1-based
                for name_node in cd.get("class_name", []):
                    target_class_name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                break
        if target_class_name:
            break

    if not target_class_name:
        return None

    # Suche nach Eltern-Klassen
    result = []
    qc2 = QueryCursor(query)
    for _pi, cd in qc2.matches(tree.root_node):
        for n in cd.get("extends_name", []):
            try:
                name = source[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
            except (UnicodeDecodeError, IndexError) as e:
                logger.debug("_ast_type_hierarchy_supertypes: decoding extends_name: %s", e)
                continue
            # Nur wenn diese extends-Klasse unser target_class_name ist,
            # und die definierende Klasse existiert
            for class_node in cd.get("class_name", []):
                try:
                    cn = source[class_node.start_byte:class_node.end_byte].decode("utf-8", errors="replace")
                except (UnicodeDecodeError, IndexError) as e:
                    logger.debug("_ast_type_hierarchy_supertypes: decoding class_name: %s", e)
                    continue
                if name != target_class_name and cn == target_class_name:
                    for def_node in cd.get("class_def", []):
                        start = def_node.start_point[0] if hasattr(def_node, "start_point") else 0
                        result.append({
                            "name": name,
                            "kind": "class" if "class" in str(def_node.type) else "interface",
                            "line": start + 1,
                            "file": str(target),
                        })

    return result if result else None


def _find_target_class_name(
    target_path: str, line: int, lang_key: str, parser, query, source: bytes
) -> Optional[str]:
    """Finde den Klassennamen an der angegebenen Zeile im AST."""
    from tree_sitter import QueryCursor as _QueryCursor

    tree = parser.parse(source)
    if tree is None:
        return None

    target_class_name = None
    qc = _QueryCursor(query)
    for _pi, cd in qc.matches(tree.root_node):
        for n in cd.get("class_def", []):
            start_line = n.start_point[0] if hasattr(n, "start_point") else 0
            if start_line == line - 1:
                for name_node in cd.get("class_name", []):
                    target_class_name = source[name_node.start_byte:name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                break
        if target_class_name:
            break
    return target_class_name


def _scan_subtypes_in_project(
    target_class_name: str, scan_dir, parser, query, lang_key: str
) -> list:
    """Scanne alle Dateien im Projekt nach Klassen, die von target_class_name erben."""
    from pathlib import Path as _Path

    from tree_sitter import QueryCursor as _QueryCursor

    scan_dir = _Path(scan_dir) if not isinstance(scan_dir, _Path) else scan_dir
    result = []
    for ext in [".py", ".ts", ".tsx", ".js"]:
        for f in scan_dir.glob(f"**/*{ext}"):
            if any(p in str(f) for p in ["node_modules", ".venv", "__pycache__", ".git"]):
                continue
            try:
                with open(f, "rb") as sf:
                    scan_source = sf.read()
            except (OSError, IOError) as e:
                logger.debug("_scan_subtypes_in_project: reading file: %s", e)
                continue
            scan_tree = parser.parse(scan_source)
            if scan_tree is None:
                continue
            qc3 = _QueryCursor(query)
            for _pi2, cd2 in qc3.matches(scan_tree.root_node):
                for n in cd2.get("extends_name", []):
                    try:
                        name = scan_source[n.start_byte:n.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                    except (UnicodeDecodeError, IndexError) as e:
                        logger.debug("_scan_subtypes_in_project: decoding extends_name: %s", e)
                        continue
                    if name == target_class_name:
                        for def_node in cd2.get("class_def", []):
                            start = def_node.start_point[0] if hasattr(def_node, "start_point") else 0
                            cn = "?"
                            for cn_node in cd2.get("class_name", []):
                                try:
                                    cn = scan_source[cn_node.start_byte:cn_node.end_byte].decode(
                                        "utf-8", errors="replace"
                                    )
                                except (UnicodeDecodeError, IndexError):
                                    cn = "?"
                            result.append({
                                "name": cn,
                                "kind": "class" if "class" in str(def_node.type) else "interface",
                                "line": start + 1,
                                "file": str(f),
                            })
    return result


def _ast_type_hierarchy_subtypes(path: str, line: int) -> Optional[list]:
    """AST-basierte Subtypes (Kind-Klassen/Interfaces).

    Findet alle Klassen die VON der Klasse an position line erben.
    """
    from pathlib import Path as _Path
    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    from ..code_tools import _get_language, _get_parser, detect_language
    lang_key = detect_language(str(target))
    if not lang_key or lang_key not in _TYPE_HIERARCHY_FALLBACK_LANGS:
        return None

    if lang_key == "python":
        query_source = _PYTHON_CLASS_EXTENDS
    else:
        query_source = _TS_CLASS_EXTENDS

    try:
        from tree_sitter import Query
        lang_obj = _get_language(lang_key)
        if lang_obj is None:
            return None
        query = Query(lang_obj, query_source)
    except Exception:
        logger.debug("type_hierarchy: root resolution failed")
        return None

    parser = _get_parser(lang_key)
    if parser is None:
        return None

    try:
        with open(str(target), "rb") as f:
            source = f.read()
    except (OSError, IOError):
        return None

    target_class_name = _find_target_class_name(
        str(target), line, lang_key, parser, query, source
    )
    if not target_class_name:
        return None

    result = _scan_subtypes_in_project(
        target_class_name, target.parent, parser, query, lang_key
    )
    return result if result else None
