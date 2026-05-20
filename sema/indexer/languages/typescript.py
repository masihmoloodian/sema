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
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    chunks: list[Chunk] = []
    file_imports = _extract_file_imports(tree.root_node, source_bytes)
    _walk(tree.root_node, source_bytes, file_path, chunks, parent_name=None, file_imports=file_imports)
    return chunks


def _extract_file_imports(root: Node, source: bytes) -> list[str]:
    """Collect module specifiers from top-level import statements."""
    imports = []
    for node in root.children:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    # strip surrounding quotes
                    val = child.text.decode().strip("'\"`")
                    if val:
                        imports.append(val)
    return imports


def _walk(node: Node, source: bytes, file: str, chunks: list, parent_name: str | None, file_imports: list[str] | None = None):
    fi = file_imports or []
    if node.type == "function_declaration":
        chunks.append(_make_function(node, source, file, parent_name, fi))

    elif node.type == "class_declaration":
        chunk = _make_class(node, source, file, fi)
        chunks.append(chunk)
        for child in node.children:
            if child.type == "class_body":
                for method in child.children:
                    if method.type == "method_definition":
                        chunks.append(
                            _make_method(method, source, file, parent_name=chunk.name, file_imports=fi)
                        )

    elif node.type == "interface_declaration":
        chunks.append(_make_interface(node, source, file, fi))

    elif node.type == "lexical_declaration":
        arrow = _find_arrow_function(node)
        if arrow:
            chunks.append(_make_arrow_fn(node, arrow, source, file, parent_name, fi))

    else:
        for child in node.children:
            _walk(child, source, file, chunks, parent_name, fi)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _get_identifier(node: Node) -> str:
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier"):
            return child.text.decode()
    return "unknown"


def _get_params(node: Node, source: bytes) -> str:
    for child in node.children:
        if child.type == "formal_parameters":
            text = _node_text(child, source)
            return text[1:-1]  # strip outer parens
    return ""


def _get_return_type(node: Node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "type_annotation":
            return _node_text(child, source).lstrip(": ")
    return None


def _get_jsdoc(node: Node, source: bytes) -> str | None:
    """Look for JSDoc comment immediately before this node."""
    start = node.start_byte
    preceding = source[max(0, start - 500):start].decode("utf-8", errors="replace").strip()
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


def _make_function(node: Node, source: str, file: str, parent: str | None, file_imports: list[str] | None = None) -> Chunk:
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
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )


def _make_class(node: Node, source: str, file: str, file_imports: list[str] | None = None) -> Chunk:
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
        imports=file_imports or [],
    )


def _make_method(node: Node, source: str, file: str, parent_name: str, file_imports: list[str] | None = None) -> Chunk:
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
        calls=_extract_calls(node, source),
        imports=file_imports or [],
    )


def _make_interface(node: Node, source: str, file: str, file_imports: list[str] | None = None) -> Chunk:
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
        imports=file_imports or [],
    )


def _get_arrow_name(decl_node: Node) -> str:
    """Extract variable name from a const/let/var declaration node."""
    for child in decl_node.children:
        if child.type == "variable_declarator":
            for grandchild in child.children:
                if grandchild.type == "identifier":
                    return grandchild.text.decode()
    return "unknown"


def _extract_calls(node: Node, source: bytes) -> list[str]:
    """Collect called symbols within a node's subtree, qualified where possible."""
    from ..builtins import TS_BUILTINS
    calls: set[str] = set()
    _collect_calls(node, source, calls, TS_BUILTINS)
    return sorted(calls)


def _collect_calls(node: Node, source: bytes, calls: set[str], builtins: frozenset[str]) -> None:
    if node.type == "call_expression":
        fn = node.children[0] if node.children else None
        if fn is not None:
            if fn.type == "identifier":
                name = fn.text.decode()
                if name not in builtins:
                    calls.add(name)
            elif fn.type == "member_expression":
                obj_node = fn.children[0] if fn.children else None
                prop = None
                for child in fn.children:
                    if child.type == "property_identifier":
                        prop = child.text.decode()
                        break
                if prop and prop not in builtins:
                    # Qualify as "obj.method" when object is a plain identifier (not `this`)
                    if obj_node and obj_node.type == "identifier":
                        obj = obj_node.text.decode()
                        if obj == "this":
                            calls.add(prop)
                        else:
                            calls.add(f"{obj}.{prop}")
                    else:
                        calls.add(prop)
    elif node.type == "new_expression":
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                name = child.text.decode()
                if name not in builtins:
                    calls.add(name)
                break
    for child in node.children:
        _collect_calls(child, source, calls, builtins)


def _make_arrow_fn(
    decl_node: Node, fn_node: Node, source: str, file: str, parent: str | None, file_imports: list[str] | None = None
) -> Chunk:
    name = _get_arrow_name(decl_node)
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
        calls=_extract_calls(fn_node, source),
        imports=file_imports or [],
    )
