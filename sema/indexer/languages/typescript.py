"""
TypeScript/JavaScript chunk extraction using tree-sitter.

Extracts function declarations, arrow functions, class declarations,
methods inside classes, and interface declarations.
"""

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Node
from sema.store.schema import Chunk

TS_LANGUAGE = Language(tsts.language_typescript())


def extract_chunks(source: str, file_path: str) -> list[Chunk]:
    parser = Parser(TS_LANGUAGE)
    tree = parser.parse(source.encode())
    chunks: list[Chunk] = []
    _walk(tree.root_node, source, file_path, chunks, parent_name=None)
    return chunks


def _walk(node: Node, source: str, file: str, chunks: list, parent_name: str | None):
    if node.type == "function_declaration":
        chunks.append(_make_function(node, source, file, parent_name))

    elif node.type == "class_declaration":
        chunk = _make_class(node, source, file)
        chunks.append(chunk)
        for child in node.children:
            if child.type == "class_body":
                for method in child.children:
                    if method.type == "method_definition":
                        chunks.append(
                            _make_method(method, source, file, parent_name=chunk.name)
                        )

    elif node.type == "interface_declaration":
        chunks.append(_make_interface(node, source, file))

    elif node.type == "lexical_declaration":
        arrow = _find_arrow_function(node)
        if arrow:
            chunks.append(_make_arrow_fn(node, arrow, source, file, parent_name))

    else:
        for child in node.children:
            _walk(child, source, file, chunks, parent_name)


def _node_text(node: Node, source: str) -> str:
    return source[node.start_byte:node.end_byte]


def _get_identifier(node: Node) -> str:
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return child.text.decode()
    return "unknown"


def _get_params(node: Node, source: str) -> str:
    for child in node.children:
        if child.type == "formal_parameters":
            text = _node_text(child, source)
            return text[1:-1]  # strip outer parens
    return ""


def _get_return_type(node: Node, source: str) -> str | None:
    for child in node.children:
        if child.type == "type_annotation":
            return _node_text(child, source).lstrip(": ")
    return None


def _get_jsdoc(node: Node, source: str) -> str | None:
    """Look for JSDoc comment immediately before this node."""
    start = node.start_byte
    preceding = source[max(0, start - 500):start].strip()
    if preceding.endswith("*/"):
        doc_start = preceding.rfind("/**")
        if doc_start != -1:
            return preceding[doc_start:].strip()
    return None


def _is_exported(node: Node) -> bool:
    parent = node.parent
    if parent and parent.type == "export_statement":
        return True
    return False


def _find_arrow_function(node: Node) -> Node | None:
    for child in node.children:
        if child.type == "variable_declarator":
            for grandchild in child.children:
                if grandchild.type in ("arrow_function", "function"):
                    return grandchild
    return None


def _make_function(node: Node, source: str, file: str, parent: str | None) -> Chunk:
    name = _get_identifier(node)
    params = _get_params(node, source)
    return_type = _get_return_type(node, source)
    signature = f"{name}({params}){': ' + return_type if return_type else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="typescript",
        chunk_type="function",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        docstring=_get_jsdoc(node, source),
        exports=_is_exported(node),
        parent_name=parent,
    )


def _make_class(node: Node, source: str, file: str) -> Chunk:
    name = _get_identifier(node)
    signature = f"class {name}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="typescript",
        chunk_type="class",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        docstring=_get_jsdoc(node, source),
        exports=_is_exported(node),
    )


def _make_method(node: Node, source: str, file: str, parent_name: str) -> Chunk:
    name = _get_identifier(node)
    params = _get_params(node, source)
    return_type = _get_return_type(node, source)
    signature = f"{name}({params}){': ' + return_type if return_type else ''}"
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{parent_name}.{name}:{start_line}",
        file=file,
        language="typescript",
        chunk_type="method",
        name=name,
        signature=signature,
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        parent_name=parent_name,
    )


def _make_interface(node: Node, source: str, file: str) -> Chunk:
    name = _get_identifier(node)
    start_line = node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="typescript",
        chunk_type="interface",
        name=name,
        signature=f"interface {name}",
        body=_node_text(node, source),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        exports=_is_exported(node),
    )


def _make_arrow_fn(
    decl_node: Node, fn_node: Node, source: str, file: str, parent: str | None
) -> Chunk:
    name = _get_identifier(decl_node)
    params = _get_params(fn_node, source)
    return_type = _get_return_type(fn_node, source)
    signature = f"{name}({params}){': ' + return_type if return_type else ''}"
    start_line = decl_node.start_point[0] + 1
    return Chunk(
        id=f"{file}::{name}:{start_line}",
        file=file,
        language="typescript",
        chunk_type="function",
        name=name,
        signature=signature,
        body=_node_text(decl_node, source),
        start_line=decl_node.start_point[0] + 1,
        end_line=decl_node.end_point[0] + 1,
        exports=_is_exported(decl_node),
        parent_name=parent,
    )
