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
    _walk(tree.root_node, source_bytes, file_path, chunks, parent_name=None)
    return chunks


def _walk(node: Node, source: bytes, file: str, chunks: list, parent_name: str | None):
    if node.type in ("function_definition", "decorated_definition"):
        target = node
        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "function_definition":
                    target = child
                    break
        chunks.append(_make_function(target, source, file, parent_name))

    elif node.type == "class_definition":
        chunk = _make_class(node, source, file)
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
                            _make_method(actual, source, file, parent_name=chunk.name)
                        )
    else:
        for child in node.children:
            _walk(child, source, file, chunks, parent_name)


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


def _make_function(node: Node, source: bytes, file: str, parent: str | None) -> Chunk:
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
    )


def _make_class(node: Node, source: bytes, file: str) -> Chunk:
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
    )


def _make_method(node: Node, source: bytes, file: str, parent_name: str) -> Chunk:
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
    )
