#!/usr/bin/env python3
"""Replace _json.dumps(...) with fmt_ok()/fmt_err() in lsp_bridge.py and code_intel.py."""
import re
import sys

def fix_file(filepath: str):
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    # ─── Helper: add _fmt import if needed ───
    if 'from ._fmt import' not in content:
        content = content.replace(
            'from ._logging import setup_logger as _setup_lsp_bridge_logger',
            'from ._logging import setup_logger as _setup_lsp_bridge_logger\nfrom ._fmt import fmt_ok, fmt_err, fmt_info, fmt_warn, fmt_tree'
        )
    
    # ─── Phase 1: Simple single-line error patterns ───
    simple_errors = [
        ('Path not found: path', 
         'return _json.dumps({"error": f"Path not found: {path}"})',
         'return fmt_err(f"Path not found: {path}")'),
        ('Could not auto-detect language, path=path',
         'return _json.dumps({"error": "Could not auto-detect language", "path": path})',
         'return fmt_err(f"Could not auto-detect language: {path}")'),
        ('No LSP bridge for lang',
         'return _json.dumps({"error": f"No LSP bridge for {lang}"})',
         'return fmt_err(f"No LSP bridge for {lang}")'),
        ('No LSP bridge for lang or auto-detected',
         'return _json.dumps({"error": f"No LSP bridge for {lang or \'auto-detected\'}"})',
         'return fmt_err(f"No LSP bridge for {lang or \'auto-detected\'}")'),
        ('Could not auto-detect language',
         'return _json.dumps({"error": "Could not auto-detect language"})',
         'return fmt_err("Could not auto-detect language")'),
        ('Path not found: anchor',
         'return _json.dumps({"error": f"Path not found: {anchor}"})',
         'return fmt_err(f"Path not found: {anchor}")'),
        ('Failed to resolve references',
         'return _json.dumps({"error": "Failed to resolve references for caller analysis"})',
         'return fmt_err("Failed to resolve references for caller analysis")'),
        ('None, None, Path not found',
         'return None, None, _json.dumps({"error": f"Path not found: {path}"})',
         'return None, None, fmt_err(f"Path not found: {path}")'),
        ('No LSP bridge available for lang',
         'return _json.dumps({"error": f"No LSP bridge available for language={lang}"})',
         'return fmt_err(f"No LSP bridge available for language={lang}")'),
        ('No hover info at position',
         'return _json.dumps({"error": "No hover info at position", "path": str(target), "line": line})',
         'return fmt_err(f"No hover info at position: {str(target)}:{line}")'),
        ('type_definition failed',
         'return _json.dumps({"error": f"type_definition failed: {exc}"})',
         'return fmt_err(f"type_definition failed: {exc}")'),
        ('No type definition found',
         'return _json.dumps({"error": "No type definition found at position"})',
         'return fmt_err("No type definition found at position")'),
        ('implementations failed',
         'return _json.dumps({"error": f"implementations failed: {exc}"})',
         'return fmt_err(f"implementations failed: {exc}")'),
        ('No implementations found',
         'return _json.dumps({"error": "No implementations found at position"})',
         'return fmt_err("No implementations found at position")'),
        ('apply_index out of range',
         'return _json.dumps({"error": f"apply_index {apply_index} out of range (0..{len(actions)-1})"})',
         'return fmt_err(f"apply_index {apply_index} out of range (0..{len(actions)-1})")'),
    ]
    
    for name, old, new in simple_errors:
        if old in content:
            content = content.replace(old, new)
    
    # ─── Phase 2: return _json.dumps(VAR, indent=2) and return _json.dumps(VAR) ───
    # Must do these AFTER simple_errors to avoid double-replacing
    
    # return _json.dumps(variable_name, indent=2)
    content = re.sub(
        r'return _json\.dumps\((\w+),\s*indent=2\)',
        r'return fmt_ok(\1)',
        content
    )
    
    # return _json.dumps(variable_name)
    content = re.sub(
        r'return _json\.dumps\((\w+)\)',
        r'return fmt_ok(\1)',
        content
    )
    
    # return None, _json.dumps(variable)
    content = re.sub(
        r'return None,\s*_json\.dumps\((\w+)\)',
        r'return None, fmt_ok(\1)',
        content
    )
    
    # ─── Phase 3: Single-line dict patterns ───
    # return _json.dumps({...}, indent=2) — single line
    content = re.sub(
        r'return _json\.dumps\(\{(.*?)\},\s*indent=2\)',
        lambda m: 'return fmt_ok({' + m.group(1) + '})',
        content
    )
    
    # return _json.dumps({...}) single line (must have a comma, to avoid trivial dicts)
    content = re.sub(
        r'return _json\.dumps\((\{[^}]+,[^}]*\})\)',
        lambda m: 'return fmt_ok(' + m.group(1) + ')',
        content
    )
    
    # ─── Phase 4: Multiline dict patterns ───
    # Match "return _json.dumps({" to "})" or "}, indent=2)" spanning multiple lines
    lines = content.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect multiline return _json.dumps({...})
        m = re.match(r'^(\s*)return _json\.dumps\(\{', line)
        if m:
            indent = m.group(1)
            # Check if this is already closed on the same line
            if '})' in line:
                result.append(line)
                i += 1
                continue
            
            # Collect lines until brace depth returns to 0
            brace_depth = 1
            block = [line]
            j = i + 1
            closing_line_found = -1
            while j < len(lines):
                l = lines[j]
                open_before = brace_depth
                for ch in l:
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                block.append(l)
                if brace_depth == 0:
                    closing_line_found = j
                    break
                j += 1
            
            if closing_line_found >= 0:
                # Replace the opening
                block[0] = re.sub(
                    r'^(\s*)return _json\.dumps\(\{',
                    r'\1return fmt_ok({',
                    block[0]
                )
                # Fix the closing: }, indent=2) -> })  (keep closing paren)
                last = block[-1]
                if ', indent=2)' in last:
                    last = last.replace(', indent=2)', ')')
                block[-1] = last
                
                result.extend(block)
                i = closing_line_found + 1
                continue
        
        result.append(line)
        i += 1
    
    content = '\n'.join(result)
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"✅ {filepath}: modified successfully")
    else:
        print(f"ℹ️  {filepath}: no changes")
    
    # Verify compilation
    import py_compile
    try:
        py_compile.compile(filepath, doraise=True)
        print(f"   ✅ Syntax check PASSED")
    except py_compile.PyCompileError as e:
        print(f"   ❌ Syntax error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    for fp in sys.argv[1:]:
        fix_file(fp)
