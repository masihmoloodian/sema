"""
Go chunk extraction using tree-sitter.

Extracts function declarations, method declarations,
struct type declarations, and interface type declarations.
"""

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node
from sema.store.schema import Chunk

GO_LANGUAGE = Language(tsgo.language())


def extract_chunks(source: str, file_path: str) -> list[Chunk]:
    parser = Parser(GO_LANGUAGE)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    chunks: list[Chunk] = []
    file_imports = _extract_file_imports(tree.root_node, source_bytes)
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            chunks.append(_make_function(child, source_bytes, file_path, file_imports))
        elif child.type == "method_declaration":
            chunks.append(_make_method(child, source_bytes, file_path, file_imports))
        elif child.type == "type_declaration":
            chunk = _make_type(child, source_bytes, file_path, file_imports)
            if chunk:
                chunks.append(chunk)
    return chunks


def _extract_file_imports(root: Node, source: bytes) -> list[str]:
    """Collect import paths from import declarations."""
    imports = []
    for node in root.children:
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            for s in spec.children:
                                if s.type == "interpreted_string_literal":
                                    val = s.text.decode().strip('"')
                                    if val:
                                        imports.append(val)
                elif child.type == "import_spec":
                    for s in child.children:
                        if s.type == "interpreted_string_literal":
                            val = s.text.decode().strip('"')
                            if val:
                                imports.append(val)
    return imports


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_name(node: Node) -> str:
    for child in node.children:
        if child.type in ("field_identifier", "identifier"):
            return child.text.decode()
    return "unknown"


def _get_params(node: Node, source: bytes) -> str:
    for child in node.children:
        if child.type == "parameter_list":
            return _node_text(child, source)
    return "()"


def _get_result(node: Node, source: bytes) -> str | None:
    params_seen = False
    for child in node.children:
        if child.type == "parameter_list":
            if params_seen:
                return _node_text(child, source)
            params_seen = True
        elif child.type in ("type_identifier", "pointer_type", "qualified_type"):
            if params_seen:
                return _node_text(child, source)
    return None


def _build_type_map(node: Node, source: bytes) -> dict[str, str]:
    """Build {varName: TypeName} from typed Go declarations in a node's subtree.

    Covers two patterns:
      var svc *AuthService = ...      (var declaration with explicit type)
      svc := NewAuthService(...)      (short declaration with NewXxx constructor)
      svc := AuthService{...}         (composite literal constructor)

    The NewXxx → Xxx stripping is the Go constructor naming convention.
    """
    type_map: dict[str, str] = {}
    _collect_type_hints(node, source, type_map)
    return type_map


def _collect_type_hints(node: Node, source: bytes, type_map: dict[str, str]) -> None:
    if node.type == "var_declaration":
        for child in node.children:
            if child.type == "var_spec":
                var_name: str | None = None
                type_name: str | None = None
                for c in child.children:
                    if c.type == "identifier" and var_name is None:
                        var_name = c.text.decode()
                    elif c.type in ("type_identifier", "pointer_type") and type_name is None:
                        raw = _node_text(c, source).lstrip("*")
                        if raw and raw[0].isupper():
                            type_name = raw
                if var_name and type_name:
                    type_map[var_name] = type_name

    elif node.type == "short_var_declaration":
        # Both sides are expression_list in Go's grammar.
        # svc := NewService()   →  left=expression_list("svc"), right=expression_list(call)
        # svc := Service{}      →  left=expression_list("svc"), right=expression_list(literal)
        expr_lists: list[Node] = [c for c in node.children if c.type == "expression_list"]
        if len(expr_lists) >= 2:
            left_list, right_list = expr_lists[0], expr_lists[1]
            identifiers = [c.text.decode() for c in left_list.children if c.type == "identifier"]
            if identifiers:
                for c in right_list.children:
                    if c.type == "call_expression":
                        fn = c.children[0] if c.children else None
                        if fn and fn.type == "identifier":
                            fn_name = fn.text.decode()
                            # NewAuthService → AuthService (Go constructor convention)
                            if fn_name.startswith("New") and len(fn_name) > 3:
                                type_map[identifiers[0]] = fn_name[3:]
                        break
                    elif c.type == "composite_literal":
                        type_node = next(
                            (ch for ch in c.children if ch.type == "type_identifier"), None
                        )
                        if type_node:
                            raw = type_node.text.decode()
                            if raw and raw[0].isupper():
                                type_map[identifiers[0]] = raw
                        break

    for child in node.children:
        _collect_type_hints(child, source, type_map)


def _extract_calls(node: Node, source: bytes) -> list[str]:
    """Collect called symbols within a node's subtree, qualified where possible."""
    from ..builtins import GO_BUILTINS
    calls: set[str] = set()
    type_map = _build_type_map(node, source)
    _collect_calls(node, source, calls, GO_BUILTINS, type_map)
    return sorted(calls)


def _collect_calls(
    node: Node,
    source: bytes,
    calls: set[str],
    builtins: frozenset[str],
    type_map: dict[str, str] | None = None,
) -> None:
    if node.type == "call_expression":
        fn = node.children[0] if node.children else None
        if fn is not None:
            if fn.type == "identifier":
                name = fn.text.decode()
                if name not in builtins:
                    calls.add(name)
            elif fn.type == "selector_expression":
                obj_node = fn.children[0] if fn.children else None
                method = None
                for child in fn.children:
                    if child.type == "field_identifier":
                        method = child.text.decode()
                        break
                if method and method not in builtins:
                    if obj_node and obj_node.type == "identifier":
                        obj = obj_node.text.decode()
                        resolved = type_map.get(obj) if type_map else None
                        calls.add(f"{resolved or obj}.{method}")
                    else:
                        calls.add(method)
    for child in node.children:
        _collect_calls(child, source, calls, builtins, type_map)


def _make_function(node: Node, source: bytes, file: str, file_imports: list[str] | None = None) -> Chunk:
    name = _get_name(node)
    params = _get_params(node, source)
    result = _get_result(node, source)
    signature = f"func {name}{params}{' ' + result if result else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="go",
        chunk_type="function",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        exports=name[0].isupper() if name else False,
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )


def _make_method(node: Node, source: bytes, file: str, file_imports: list[str] | None = None) -> Chunk:
    name = _get_name(node)
    receiver = None
    params = ""
    result = None
    param_count = 0
    for child in node.children:
        if child.type == "parameter_list":
            if param_count == 0:
                receiver = _node_text(child, source)
                param_count += 1
            elif param_count == 1:
                params = _node_text(child, source)
                param_count += 1
            else:
                result = _node_text(child, source)
    signature = f"func {receiver} {name}{params}{' ' + result if result else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="go",
        chunk_type="method",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        exports=name[0].isupper() if name else False,
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )


def _make_type(node: Node, source: bytes, file: str, file_imports: list[str] | None = None) -> Chunk | None:
    for child in node.children:
        if child.type == "type_spec":
            name_node = child.child_by_field_name("name")
            type_node = child.child_by_field_name("type")
            if not name_node or not type_node:
                continue
            name = name_node.text.decode()
            type_kind = type_node.type  # "struct_type" or "interface_type"
            chunk_type = "struct" if "struct" in type_kind else "interface"
            signature = f"type {name} {chunk_type}"
            return Chunk(
                id=f"{file}::{name}:{node.start_point[0] + 1}",
                file=file,
                language="go",
                chunk_type=chunk_type,
                name=name,
                signature=signature,
                body=_node_text(node, source),
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                exports=name[0].isupper() if name else False,
                imports=file_imports or [],
            )
    return None
