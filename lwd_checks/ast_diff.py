"""AST primitives shared by the standards checks.

Functions and classes are matched between the base and head versions of
a file by qualified name (`Class.method`), so moving code inside a file
does not hide a rule violation.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class LwdFunctionInfo:
    qualname: str
    params: int
    start_line: int  # the `def` line; decorators are excluded
    end_line: int

    @property
    def length(self) -> int:
        return self.end_line - self.start_line + 1


def lwd_count_params(args: ast.arguments, *, is_method: bool) -> int:
    """Count parameters; a leading `self`/`cls` on methods is not counted.

    `*args` and `**kwargs` count as one each; positional-only and
    keyword-only parameters count individually.
    """
    positional = list(args.posonlyargs) + list(args.args)
    if is_method and positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    count = len(positional) + len(args.kwonlyargs)
    count += 1 if args.vararg else 0
    count += 1 if args.kwarg else 0
    return count


def _lwd_walk_defs(
    node: ast.AST, stack: list[str], in_class: bool
) -> Iterator[tuple[list[str], ast.AST, bool]]:
    """Yield (qualname parts, node, directly-inside-a-class) for defs."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            qualname = stack + [child.name]
            yield qualname, child, in_class
            yield from _lwd_walk_defs(child, stack + [child.name], True)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = stack + [child.name]
            yield qualname, child, in_class
            yield from _lwd_walk_defs(child, stack + [child.name], False)


def lwd_parse_functions(source: str) -> dict[str, LwdFunctionInfo]:
    """Parse a module into functions keyed by qualified name.

    Syntax errors raise SyntaxError; the caller decides what to do with
    them (pre-commit owns syntax).
    """
    functions: dict[str, LwdFunctionInfo] = {}
    for qualname_parts, node, in_class in _lwd_walk_defs(ast.parse(source), [], False):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        qualname = ".".join(qualname_parts)
        functions[qualname] = LwdFunctionInfo(
            qualname=qualname,
            params=lwd_count_params(node.args, is_method=in_class),
            start_line=node.lineno,
            end_line=node.end_lineno,
        )
    return functions


def lwd_parse_classes(source: str) -> set[str]:
    classes: set[str] = set()
    for qualname_parts, node, _in_class in _lwd_walk_defs(ast.parse(source), [], False):
        if isinstance(node, ast.ClassDef):
            classes.add(".".join(qualname_parts))
    return classes
