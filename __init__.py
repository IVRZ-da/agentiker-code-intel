from hermes_cli.plugins import PluginContext
import toolsets

def register(ctx: PluginContext) -> None:
    # Inject the code_intel toolset definition
    if "code_intel" not in toolsets.TOOLSETS:
        toolsets.TOOLSETS["code_intel"] = {
            "description": "AST-aware code intelligence: symbol extraction, structural search, safe refactoring, LSP go-to-definition and find-all-references (tree-sitter + ast-grep + LSP)",
            "tools": ["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"],
            "includes": []
        }

    # Inject into core platforms so it's globally available
    if "code_symbols" not in toolsets._HERMES_CORE_TOOLS:
        toolsets._HERMES_CORE_TOOLS.extend(["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"])

    if "hermes-acp" in toolsets.TOOLSETS:
        tools = toolsets.TOOLSETS["hermes-acp"]["tools"]
        if "code_symbols" not in tools:
            tools.extend(["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"])

    if "hermes-api-server" in toolsets.TOOLSETS:
        tools = toolsets.TOOLSETS["hermes-api-server"]["tools"]
        if "code_symbols" not in tools:
            tools.extend(["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"])

    # Load our tools
    from . import code_intel

    # Inject steering hints directly into the registry schemas of the builtin tools!
    import tools.registry
    
    sf_entry = tools.registry.registry.get_entry("search_files")
    if sf_entry and "description" in sf_entry.schema:
        hint = (
            "\n\nFor AST-aware structural search inside source files "
            "(find function calls, imports, decorators, etc.), prefer code_search — "
            "it understands syntax and won't match comments or strings."
        )
        if hint not in sf_entry.schema["description"]:
            sf_entry.schema["description"] += hint

    rf_entry = tools.registry.registry.get_entry("read_file")
    if rf_entry and "description" in rf_entry.schema:
        hint = (
            "\n\nFor understanding what a file contains (list of functions, classes, "
            "methods with line numbers and signatures), prefer code_symbols — "
            "much more token-efficient than reading the entire file."
        )
        if hint not in rf_entry.schema["description"]:
            rf_entry.schema["description"] += hint

    p_entry = tools.registry.registry.get_entry("patch")
    if p_entry and "description" in p_entry.schema:
        hint = (
            "\n\nFor AST-aware structural replacement (rename patterns, wrap "
            "functions, add parameters across a file), prefer code_refactor — "
            "matches by syntax tree, not raw text. Dry-run by default."
        )
        if hint not in p_entry.schema["description"]:
            p_entry.schema["description"] += hint
