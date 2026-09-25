import ast
import pathlib
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "apps")
for path in sorted(root.rglob("*.py")):
    src = path.read_text()
    tree = ast.parse(src)
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                imported[alias.asname or alias.name] = node.lineno

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            cur = node
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name):
                used.add(cur.id)

    unused = sorted(
        name
        for name in imported
        if name not in used
        and f'"{name}"' not in src
        and f"'{name}'" not in src
    )
    if unused:
        print(f"{path}: {', '.join(unused)}")
