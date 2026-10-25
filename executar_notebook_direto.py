"""Executa células de código de um notebook sem kernel Jupyter (uso experimental)."""
from __future__ import annotations

import sys
from pathlib import Path

import nbformat

sys.stdout.reconfigure(encoding="utf-8")


def display(obj):
    """Substituto simples para as tabelas exibidas no notebook."""
    print(obj)


path = Path(sys.argv[1])
notebook = nbformat.read(path, as_version=4)
namespace = {"__name__": "__main__", "display": display}
for number, cell in enumerate(notebook.cells, 1):
    if cell.cell_type != "code":
        continue
    print(f"\n--- célula {number} ---", flush=True)
    exec(compile(cell.source, f"{path.name}:cell-{number}", "exec"), namespace)
