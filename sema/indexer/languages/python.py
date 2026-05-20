"""
Python chunk extraction using tree-sitter.

Extracts function definitions, async function definitions,
class definitions, and methods inside classes.
"""

import tree_sitter_python as tspy
from tree_sitter import Language, Parser, Node
from sema.store.schema import Chunk

PY_LANGUAGE = Language(tspy.language())


def extract_chunks(source: str, file_path: str) -> list[Chunk]:
    parser = Parser(PY_LANGUAGE)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    chunks: list[Chunk] = []
    file_imports = _extract_file_imports(tree.root_node, source_bytes)
    _walk(tree.root_node, source_bytes, file_path, chunks, parent_name=None, file_imports=file_imports)
    return chunks


def _extract_file_imports(root: Node, source: bytes) -> list[str]:
    """Collect module names from top-level import statements."""
    imports = []
    for node in root.children:
        if node.type == "import_statement":
            # import foo, bar
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append(child.text.decode())
        elif node.type == "import_from_statement":
            # from foo.bar import baz  →  record "foo.bar"
            for child in node.children:
                if child.type in ("dotted_name", "relative_import"):
                    imports.append(child.text.decode())
                    break
    return imports


def _walk(node: Node, source: bytes, file: str, chunks: list, parent_name: str | None, file_imports: list[str] | None = None):
    fi = file_imports or []
    if node.type in ("function_definition", "decorated_definition"):
        target = node
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "function_definition":
                    target = child
                    break
        chunks.append(_make_function(target, source, file, parent_name, fi))

    elif node.type == "class_definition":
        chunk = _make_class(node, source, file, fi)
        chunks.append(chunk)
        for child in node.children:
            if child.type == "block":
                for method in child.children:
                    actual = method
                    if method.type == "decorated_definition":
                        for c in method.children:
                            if c.type == "function_definition":
                                actual = c
                                break
                    if actual.type == "function_definition":
                        chunks.append(
                            _make_method(actual, source, file, parent_name=chunk.name, file_imports=fi)
                        )
    else:
        for child in node.children:
            _walk(child, source, file, chunks, parent_name, fi)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_name(node: Node) -> str:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode()
    return "unknown"


def _get_params(node: Node, source: bytes) -> str:
    for child in node.children:
        if child.type == "parameters":
            text = _node_text(child, source)
            return text[1:-1]  # strip outer parens
    return ""


def _get_return_annotation(node: Node, source: bytes) -> str | None:
    found_arrow = False
    for child in node.children:
        if child.type == "->":
            found_arrow = True
        elif found_arrow and child.type not in (":", "block"):
            return _node_text(child, source)
    return None


def _get_docstring(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for expr in stmt.children:
                        if expr.type == "string":
                            text = _node_text(expr, source)
                            return text.strip('"""').strip("'''").strip()
    return None


def _build_type_map(node: Node, source: bytes) -> dict[str, str]:
    """Build {varName: TypeName} by scanning for typed declarations in a node's subtree.

    Covers three patterns:
      svc: AuthService = ...      (annotated assignment)
      svc = AuthService(...)      (uppercase constructor call on RHS)
      def f(svc: AuthService)     (typed parameter)

    Only uppercase type names are recorded so built-ins like str/int are ignored.
    """
    type_map: dict[str, str] = {}
    _collect_type_hints(node, source, type_map)
    return type_map


def _collect_type_hints(node: Node, source: bytes, type_map: dict[str, str]) -> None:
    if node.type == "assignment":
        # Handles two sub-cases in one node type:
        #   svc: AuthService = get_service()  → "type" child present
        #   svc = AuthService(...)            → uppercase constructor on RHS
        var_name: str | None = None
        type_name: str | None = None
        for child in node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode()
            elif child.type == "type" and type_name is None:
                # Inline annotation: svc: AuthService = ...
                for c in child.children:
                    if c.type == "identifier":
                        candidate = c.text.decode()
                        if candidate[0].isupper():
                            type_name = candidate
                        break
            elif child.type == "call" and var_name is not None and type_name is None:
                # Bare constructor: svc = AuthService(...)
                fn = child.children[0] if child.children else None
                if fn and fn.type == "identifier":
                    fn_name = fn.text.decode()
                    if fn_name[0].isupper():
                        type_name = fn_name
        if var_name and type_name:
            type_map[var_name] = type_name

    elif node.type == "typed_parameter":
        # def f(svc: AuthService) — first identifier = param name, second = type
        var_name = None
        type_name = None
        for child in node.children:
            if child.type == "identifier" and var_name is None:
                var_name = child.text.decode()
            elif child.type == "identifier" and var_name is not None:
                candidate = child.text.decode()
                if candidate[0].isupper():
                    type_name = candidate
                    break
        if var_name and type_name:
            type_map[var_name] = type_name

    for child in node.children:
        _collect_type_hints(child, source, type_map)


def _extract_calls(node: Node, source: bytes) -> list[str]:
    """Collect called symbols within a node's subtree, qualified where possible."""
    from ..builtins import PY_BUILTINS
    calls: set[str] = set()
    type_map = _build_type_map(node, source)
    _collect_calls(node, source, calls, PY_BUILTINS, type_map)
    return sorted(calls)


def _collect_calls(
    node: Node,
    source: bytes,
    calls: set[str],
    builtins: frozenset[str],
    type_map: dict[str, str] | None = None,
) -> None:
    if node.type == "call":
        fn = node.children[0] if node.children else None
        if fn is not None:
            if fn.type == "identifier":
                name = fn.text.decode()
                if name not in builtins:
                    calls.add(name)
            elif fn.type == "attribute":
                last = fn.children[-1] if fn.children else None
                obj_node = fn.children[0] if fn.children else None
                if last and last.type == "identifier":
                    method = last.text.decode()
                    if method not in builtins:
                        if obj_node and obj_node.type == "identifier":
                            obj = obj_node.text.decode()
                            if obj in ("self", "cls"):
                                calls.add(method)
                            else:
                                resolved = type_map.get(obj) if type_map else None
                                calls.add(f"{resolved or obj}.{method}")
                        else:
                            calls.add(method)
    for child in node.children:
        _collect_calls(child, source, calls, builtins, type_map)


def _make_function(node: Node, source: bytes, file: str, parent: str | None, file_imports: list[str] | None = None) -> Chunk:
    name = _get_name(node)
    params = _get_params(node, source)
    return_type = _get_return_annotation(node, source)
    signature = f"def {name}({params}){' -> ' + return_type if return_type else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="python",
        chunk_type="function",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        docstring=_get_docstring(node, source),
        parent_name=parent,
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )


def _make_class(node: Node, source: bytes, file: str, file_imports: list[str] | None = None) -> Chunk:
    name = _get_name(node)
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="python",
        chunk_type="class",
        name=name,
        signature=f"class {name}",
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        docstring=_get_docstring(node, source),
        imports=file_imports or [],
    )


def _make_method(node: Node, source: bytes, file: str, parent_name: str, file_imports: list[str] | None = None) -> Chunk:
    name = _get_name(node)
    params = _get_params(node, source)
    return_type = _get_return_annotation(node, source)
    signature = f"def {name}({params}){' -> ' + return_type if return_type else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{parent_name}.{name}:{start_line}",
        file=file,
        language="python",
        chunk_type="method",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        docstring=_get_docstring(node, source),
        parent_name=parent_name,
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )
