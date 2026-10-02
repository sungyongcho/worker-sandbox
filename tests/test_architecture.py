"""Import hygiene only; behavioral tests establish experiment invariants."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "benchkit"
MODULES = tuple(path.stem for path in sorted(ROOT.glob("*.py")))


def source_trees():
    return {name: ast.parse((ROOT / (name + ".py")).read_text(encoding="utf-8"), filename=name)
            for name in MODULES}


def imported_dependencies(node):
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return [node.module.split(".")[0]] if node.module else [item.name for item in node.names]
        if node.module and node.module.startswith("benchkit."):
            return [node.module.split(".")[1]]
        if node.module == "benchkit":
            return [item.name for item in node.names]
    if isinstance(node, ast.Import):
        return [item.name.split(".")[1] for item in node.names if item.name.startswith("benchkit.")]
    return []


class ArchitectureTests(unittest.TestCase):
    def test_active_modules_have_no_unused_imports(self):
        problems = []
        for name, tree in source_trees().items():
            used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                    used.update(child.value for child in ast.walk(node.value) if isinstance(child, ast.Constant) and isinstance(child.value, str))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)) or getattr(node, "module", None) == "__future__":
                    continue
                for item in node.names:
                    bound = item.asname or (item.name.split(".")[0] if isinstance(node, ast.Import) else item.name)
                    if bound != "*" and bound not in used:
                        problems.append(f"{name}:{node.lineno}: unused import {bound}")
        self.assertEqual(problems, [])

    def test_import_graph_is_acyclic(self):
        graph = {name: set() for name in MODULES}
        for name, tree in source_trees().items():
            for node in ast.walk(tree):
                for dependency in imported_dependencies(node):
                    if (ROOT / (dependency + ".py")).is_file():
                        self.assertIn(dependency, graph, f"{name} imports non-shipped module {dependency}")
                        graph[name].add(dependency)
        complete = set()
        def visit(name, active):
            self.assertNotIn(name, active, "import cycle: " + " -> ".join((*active, name)))
            if name in complete:
                return
            for dependency in sorted(graph[name]):
                visit(dependency, (*active, name))
            complete.add(name)
        for name in graph:
            visit(name, ())


if __name__ == "__main__":
    unittest.main()
