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
    tree = parser.parse(source.encode())
    chunks: list[Chunk] = []
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            chunks.append(_make_function(child, source, file_path))
        elif child.type == "method_declaration":
            chunks.append(_make_method(child, source, file_path))
        elif child.type == "type_declaration":
            chunk = _make_type(child, source, file_path)
            if chunk:
                chunks.append(chunk)
    return chunks


def _node_text(node: Node, source: str) -> str:
    return source[node.start_byte:node.end_byte]


def _get_name(node: Node) -> str:
    for child in node.children:
        if child.type in ("field_identifier", "identifier"):
            return child.text.decode()
    return "unknown"


def _get_params(node: Node, source: str) -> str:
    for child in node.children:
        if child.type == "parameter_list":
            return _node_text(child, source)
    return "()"


def _get_result(node: Node, source: str) -> str | None:
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


def _make_function(node: Node, source: str, file: str) -> Chunk:
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
    )


def _make_method(node: Node, source: str, file: str) -> Chunk:
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
    )


def _make_type(node: Node, source: str, file: str) -> Chunk | None:
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
