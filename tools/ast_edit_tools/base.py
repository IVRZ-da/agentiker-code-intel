"""ast-edit/ — AST-based code editing tools."""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("agentiker_code_intel")

def _find_symbol_in_ast(
    path: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> Optional[dict]:
    """Find a symbol in a source file using tree-sitter AST.

    Returns a dict with byte-exact boundaries:

        {name, kind, start_byte, end_byte, start_line, end_line, body}

    Supports name_path syntax: ``"ClassName/method_name"``.
    Returns ``None`` if the symbol is not found.
    """
    # Lazy imports from code_tools for shared infrastructure
    from pathlib import Path as _Path

    from ...code_tools import (
        _classify_symbol_kind,
        _detect_if_method,
        _setup_query,
        detect_language,
    )

    target = _Path(path).expanduser().resolve()
    if not target.exists():
        return None

    lang_key = detect_language(str(target), language)
    if lang_key is None:
        return None

    # Parse name_path
    name_parts = symbol_name.strip().split("/")
    leaf_name = name_parts[-1]
    parent_filter = name_parts[:-1]

    try:
        source = target.read_bytes()
    except (OSError, IOError) as e:
        logger.debug("Cannot read file %s: %s", target, e)
        return None

    from tree_sitter import QueryCursor as _QC

    setup = _setup_query(lang_key)
    if setup is None:
        return None
    parser, lang, query = setup

    tree = parser.parse(source)
    qc = _QC(query)

    for _pidx, caps in qc.matches(tree.root_node):
        name_nodes = caps.get("name", [])
        def_nodes = (
            caps.get("def")
            or caps.get("constant")
            or caps.get("field")
            or caps.get("arrow")
        )
        if not name_nodes or not def_nodes:
            continue

        name_node = name_nodes[0]
        def_node = def_nodes[0]

        try:
            name_text = name_node.text.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, IndexError, AttributeError) as e:
            logger.debug('find_node_by_name decode name_text: %s', e)
            continue

        if name_text != leaf_name:
            continue

        # If parent_filter specified, check parent hierarchy
        if parent_filter:
            _cur = def_node.parent
            _depth = 0
            _matched_parents = []
            while _cur and _depth < 10:
                try:
                    pname_node = None
                    for child in _cur.children:
                        if child.type in (
                            "identifier", "type_identifier",
                            "property_identifier",
                        ):
                            pname_node = child
                            break
                    if pname_node:
                        pn = pname_node.text.decode("utf-8", errors="replace")
                        _matched_parents.insert(0, pn)
                except (UnicodeDecodeError, IndexError) as e:
                    logger.debug('find_node_by_name decode parent_name: %s', e)
                    pass
                _cur = _cur.parent
                _depth += 1

            # Check if parents match the filter
            expected = list(parent_filter)  # e.g., ["ClassName"]
            match = True
            for i, exp in enumerate(expected):
                if i < len(_matched_parents):
                    if _matched_parents[-(i + 1)] != exp:
                        match = False
                        break
                else:
                    match = False
                    break
            if not match:
                continue

        # Found it — extract byte boundaries
        start_byte = def_node.start_byte
        end_byte = def_node.end_byte
        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        kind = _classify_symbol_kind(def_node)
        kind = _detect_if_method(def_node, kind)
        body = source[start_byte:end_byte].decode("utf-8", errors="replace")

        return {
            "name": name_text,
            "kind": kind,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "start_line": start_line,
            "end_line": end_line,
            "body": body,
        }

    return None


def _ast_search_references(
    project_root: str,
    symbol_name: str,
    language: Optional[str] = None,
) -> List[dict]:
    """Search for references to a symbol across a project.

    Returns a list of {file, line, context} for each reference found.
    Uses grep -rn with code-file extensions.
    """
    import re
    import subprocess as _sp

    references = []
    root = Path(project_root)
    if not root.is_dir():
        root = root.parent
    if not root.exists():
        return references

    ext_list = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h"]
    include_args = []
    for ext in ext_list:
        include_args.extend(["--include", f"*{ext}"])
    escaped = re.escape(symbol_name)

    try:
        cmd = ["grep", "-rn", "-C", "1"] + include_args + ["-e", escaped, str(root)]
        result = _sp.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        for line in result.stdout.splitlines():
            if not line.strip() or line.startswith("--"):
                continue
            parts = line.split(":", 2)
            if len(parts) >= 2:
                fpath = parts[0]
                try:
                    linenum = int(parts[1])
                except ValueError as e:
                    logger.debug('parse_ref_line int(linenum): %s', e)
                    continue
                context = parts[2] if len(parts) > 2 else ""
                references.append({
                    "file": fpath,
                    "line": linenum,
                    "context": context.strip(),
                })
    except (_sp.TimeoutExpired, OSError) as e:
        logger.debug("Reference search failed for %s: %s", symbol_name, e)

    return references


# ---------------------------------------------------------------------------
# code_replace_body — Replace symbol body via AST
# ---------------------------------------------------------------------------
