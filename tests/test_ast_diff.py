from lwd_checks.ast_diff import (
    lwd_count_params,
    lwd_parse_classes,
    lwd_parse_functions,
)


def _parse(source: str):
    import ast

    return ast.parse(source)


def test_count_params_counts_vararg_and_kwarg_once_each():
    tree = _parse("class A:\n    def m(self, a, b=1, *args, c, **kw):\n        pass\n")
    method = tree.body[0].body[0]
    assert lwd_count_params(method.args, is_method=True) == 5


def test_count_params_does_not_strip_module_level_self():
    tree = _parse("def f(self, a):\n    pass\n")
    assert lwd_count_params(tree.body[0].args, is_method=False) == 2


def test_count_params_includes_posonly_and_kwonly():
    tree = _parse("def f(a, /, b, *, c):\n    pass\n")
    assert lwd_count_params(tree.body[0].args, is_method=False) == 3


def test_parse_functions_builds_nested_qualified_names():
    functions = lwd_parse_functions(
        "class A:\n"
        "    class B:\n"
        "        def method(self):\n"
        "            pass\n"
        "    def outer(self):\n"
        "        pass\n"
        "def top():\n"
        "    pass\n"
    )
    assert set(functions) == {"A.B.method", "A.outer", "top"}


def test_parse_functions_span_excludes_decorators():
    functions = lwd_parse_functions(
        "@decorator\n"  # line 1
        "def f():\n"  # line 2
        "    x = 1\n"  # line 3
        "    return x\n"  # line 4
    )
    info = functions["f"]
    assert (info.start_line, info.end_line) == (2, 4)


def test_parse_classes_returns_nested_qualified_names():
    classes = lwd_parse_classes("class A:\n    class B:\n        pass\n")
    assert classes == {"A", "A.B"}
