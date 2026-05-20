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
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            chunks.append(_make_function(child, source_bytes, file_path))
        elif child.type == "method_declaration":
            chunks.append(_make_method(child, source_bytes, file_path))
        elif child.type == "type_declaration":
            chunk = _make_type(child, source_bytes, file_path)
            if chunk:
                chunks.append(chunk)
    return chunks


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


def _extract_calls(node: Node, source: bytes) -> list[str]:
    """Collect called symbols within a node's subtree, qualified where possible."""
    from ..builtins import GO_BUILTINS
    calls: set[str] = set()
    _collect_calls(node, source, calls, GO_BUILTINS)
    return sorted(calls)


def _collect_calls(node: Node, source: bytes, calls: set[str], builtins: frozenset[str]) -> None:
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
                        calls.add(f"{obj_node.text.decode()}.{method}")
                    else:
                        calls.add(method)
    for child in node.children:
        _collect_calls(child, source, calls, builtins)


def _make_function(node: Node, source: bytes, file: str) -> Chunk:
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
    )


def _make_method(node: Node, source: bytes, file: str) -> Chunk:
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
    )


def _make_type(node: Node, source: bytes, file: str) -> Chunk | None:
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
            )
    return None
