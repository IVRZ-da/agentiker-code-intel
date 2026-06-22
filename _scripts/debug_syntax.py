"""Debug code_tools.py syntax error"""
with open('/home/jo/.hermes/plugins/code_intel/code_tools.py') as f:
    lines = f.readlines()

# Count bracket/quote depths up to line 507
parenthesis = 0
brackets = 0
braces = 0
triple_q = False

for i, line in enumerate(lines[:507]):
    stripped = line.strip()
    for ch in line:
        if ch == '(':
            parenthesis += 1
        elif ch == ')':
            parenthesis -= 1
        elif ch == '[':
            brackets += 1
        elif ch == ']':
            brackets -= 1
        elif ch == '{':
            braces += 1
        elif ch == '}':
            braces -= 1

    if '"""' in stripped and not stripped.startswith('#'):
        triple_q = not triple_q


# If triple quote is open, find where it was opened
if triple_q:
    for i, line in enumerate(lines[:507]):
        if '"""' in line.strip() and not line.strip().startswith('#'):
            pass
