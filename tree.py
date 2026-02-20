import os

IGNORE = {'.git', '__pycache__', 'venv', '.venv'}

for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in IGNORE]

    level = root.count(os.sep)
    indent = "    " * level
    folder = os.path.basename(root) if os.path.basename(root) else "."
    print(f"{indent}{folder}/")

    for f in files:
        print(f"{indent}    {f}")
