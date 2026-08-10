"""Shared test constants and helpers (M1.0).

NOT a test module: test files must never import other test files (the
test_perf_budget -> test_capabilities coupling broke under pytest's
importlib import mode). Anything two test modules need lives here, next to
refenv.py, importable under any pytest import mode.
"""

import ast

# One frozen parameter set for the golden pipeline — changing any of these is
# a deliberate, reviewed golden regeneration (tests/golden/README.md). Shared
# by test_capabilities (golden byte compare), test_perf_budget (solve-count
# baseline), and tools/baseline.py — drift between them cannot happen because
# all three import it from here.
GOLDEN_SEED = 0
GOLDEN_MENU_KW = dict(n=6, seed=GOLDEN_SEED, iters=600, shortlist=8)
# M1.13: the frozen parameter set of the DISH-MODE golden (fixture
# solo_dishes — 3 authored dishes, so the search space is tiny on purpose).
GOLDEN_DISH_MENU_KW = dict(n=3, seed=GOLDEN_SEED, iters=200, shortlist=4)


def strip_comments_and_docstrings(src: str) -> str:
    """Return only the EXECUTABLE text of ``src``: comments are gone
    (ast.unparse never emits them) and docstring statements are removed from
    every module/class/function body.

    Used by the dead-config gate (test_dead_config): a schema field named
    only in a comment or docstring is documentation, not consumption — the
    reference check must not be satisfiable by prose.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)
