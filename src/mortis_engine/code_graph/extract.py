"""Parse supplied bytes only. Never materialize or execute repository files."""

import importlib
import re
import resource
import sys
from pathlib import Path, PurePosixPath

from tree_sitter import Language, Parser

from mortis_engine.code_graph.contracts import (
    MAX_FACTS,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    CodeGraphFile,
    CodeGraphImport,
    CodeGraphRequest,
    CodeGraphResult,
    CodeGraphSymbol,
    source_lines,
)
from mortis_engine.code_graph.syntax import LANGUAGES, descendants, import_target


def location(path, node):
    return dict(file_path=path, start_byte=node.start_byte, end_byte=node.end_byte,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + (node.end_point.column > 0))


def syntax_tree(parser, source, language):
    root = parser.parse(source).root_node
    if not root.has_error or language not in {"javascript", "typescript", "tsx", "c"}:
        return root
    masked = bytearray(source)
    if language in {"typescript", "tsx"}:
        # shortcut: normalize import-type namespaces until tree-sitter-typescript#365 ships.
        calls = []
        for node in descendants(root):
            if node.type != "call_expression" or node.has_error or node.parent.type != "member_expression":
                continue
            function, arguments = node.child_by_field_name("function"), node.child_by_field_name("arguments")
            if (function is not None and function.type == "import" and arguments is not None
                and [child.type for child in arguments.children if not child.is_extra] == ["(", "string", ")"]):
                calls.append(node)
        for node in calls:
            mask(masked, node.start_byte, node.end_byte)
            masked[node.start_byte] = ord("_")
        typed = parser.parse(bytes(masked)).root_node if calls else root
        masked = bytearray(source)
        for node in calls:
            namespace = typed.named_descendant_for_byte_range(node.start_byte, node.start_byte + 1)
            if namespace.parent is not None and namespace.parent.type == "nested_type_identifier":
                mask(masked, node.start_byte, node.end_byte)
                masked[node.start_byte] = ord("_")
    if language == "c":
        # Retain both sides of optional boolean continuations and validate the absent branch too.
        starts = {node.start_byte for node in descendants(root) if node.type == "ERROR"}
        conditional = re.compile(rb"(?m)^[ \t]*#[ \t]*ifn?def[ \t]+[A-Za-z_][A-Za-z_0-9]*[ \t]*\r?\n"
                                 rb"(?P<body>[^#]*?)^[ \t]*#[ \t]*endif[ \t]*(?=\r?$)")
        blocks = [match for match in conditional.finditer(source)
                  if source.index(b"#", match.start(), match.start("body")) in starts
                  and match["body"].lstrip().startswith((b"||", b"&&"))]
        # shortcut: validate one optional expression block until branch-aware grammar support is available.
        if len(blocks) > 1:
            return root
        for block in blocks:
            mask(masked, block.start(), block.start("body"))
            mask(masked, block.end("body"), block.end())
        for block in blocks:
            absent = bytearray(masked)
            mask(absent, block.start("body"), block.end("body"))
            if parse_diagnostic(parser.parse(bytes(absent)).root_node, language) is not None:
                return root
    for node in descendants(root) if language in {"javascript", "tsx"} else ():
        if (node.type == "ERROR" and node.parent.type == "jsx_element" and b"&" in node.text
            and not any(char in node.text for char in (b"<", b">", b"{", b"}"))):
            # shortcut: recover only unambiguous JSX text until tree-sitter-typescript#371 ships.
            masked[node.start_byte:node.end_byte] = node.text.replace(b"&", b" ")
    # Equal-length ASCII replacements preserve original UTF-8 byte and line positions.
    recovered = parser.parse(bytes(masked)).root_node if masked != source else root
    if language in {"typescript", "tsx"}:
        for node in descendants(recovered):
            if node.type == "arguments":
                children = [child for child in node.children if not child.is_extra]
                if any(child.type == "," and children[index - 1].type in {"(", ","}
                       for index, child in enumerate(children)):
                    return root
    return recovered if parse_diagnostic(recovered, language) is None else root


def mask(source, start, end):
    source[start:end] = bytes(char if char in {10, 13} else 32 for char in source[start:end])


def module_initializer(root, language):
    if language != "c" or not root.has_error:
        return None
    for node in descendants(root):
        name = node.child_by_field_name("name")
        directive, argument = node.child_by_field_name("directive"), node.child_by_field_name("argument")
        if ((node.type in {"preproc_def", "preproc_function_def"} and name is not None
             and name.text == b"NAPI_MODULE_INIT")
            or (node.type == "preproc_call" and directive is not None
                and directive.text.lstrip(b"#").strip() == b"undef"
                and argument is not None and b"NAPI_MODULE_INIT" in argument.text)):
            return None
    header, initializer, count = False, None, 0
    for node in root.named_children:
        path = node.child_by_field_name("path")
        if node.type == "preproc_include" and path is not None and path.text in {b'"node_api.h"', b"<node_api.h>"}:
            header = True
        if node.type == "expression_statement" and node.text.strip() == b"NAPI_MODULE_INIT()":
            count += 1
            following = node.next_named_sibling
            if header and following is not None and following.type == "compound_statement":
                initializer = node
    # Node-API permits one initializer declaration, supplied by its header macro.
    return initializer if count == 1 else None


def parse_diagnostic(root, language=None):
    initializer = module_initializer(root, language)
    pending = [root]
    while pending:
        node = pending.pop()
        if node.is_error or node.is_missing:
            if node.is_missing and node.type == ";" and node.parent == initializer:
                continue
            parent = node.parent
            while parent is not None and not parent.is_named:
                parent = parent.parent
            context = parent.type if parent else "program"
            if len(context) > 100 or not context.isascii() or not context.isidentifier():
                context = "syntax"
            token = node.type if node.is_missing else node.descendant_for_byte_range(
                node.start_byte, min(node.start_byte + 1, node.end_byte)).type
            if not token or len(token) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in token):
                token = "syntax"
            return dict(start_byte=node.start_byte, line=node.start_point.row + 1,
                        column=node.start_point.column + 1, context=context,
                        token=token, missing_token=token if node.is_missing else None)
        if node.has_error:
            pending.extend(reversed(node.children))
    return None


def extract(request: CodeGraphRequest) -> CodeGraphResult:
    parsers, files, symbols, imports = {}, [], [], []
    for item in request.files:
        metadata = dict(path=item.path, blob_sha=item.blob_sha, size_bytes=item.size_bytes)
        if item.exclusion:
            files.append(CodeGraphFile(**metadata, status="excluded", reason=item.exclusion))
            continue
        config = LANGUAGES.get(PurePosixPath(item.path).suffix)
        if config is None:
            files.append(CodeGraphFile(**metadata, status="unsupported", reason="unsupported_language"))
            continue
        language, definitions, calls, import_kinds = config
        source = item.source()
        try:
            source.decode("utf-8")
        except UnicodeDecodeError:
            files.append(CodeGraphFile(**metadata, language=language, status="failed", reason="invalid_encoding"))
            continue
        if language not in parsers:
            package = "typescript" if language == "tsx" else language
            module = importlib.import_module("tree_sitter_" + package)
            factory = "language_" + language if language in {"typescript", "tsx", "php"} else "language"
            parsers[language] = Parser(Language(getattr(module, factory)()))
        root = syntax_tree(parsers[language], source, language)
        diagnostic = parse_diagnostic(root, language) if root.has_error else None
        initializer = module_initializer(root, language)
        count = 0
        for node in descendants(root):
            if node.has_error:
                continue
            value = node.child_by_field_name("value") if node.type == "variable_declarator" else None
            if node.type in definitions.split() or (value is not None and value.type in {"arrow_function", "function_expression"}):
                identifier = node.child_by_field_name("name")
                if identifier is None and node.type == "function_definition":
                    declarator = node.child_by_field_name("declarator")
                    if declarator is not None:
                        identifier = next((n for n in descendants(declarator) if n.type in {"identifier", "field_identifier"}), None)
                if identifier is not None:
                    if len(symbols) >= MAX_FACTS:
                        raise ValueError("Symbol count exceeds the result limit")
                    symbols.append(CodeGraphSymbol(**location(item.path, node), name=identifier.text.decode(), kind=node.type))
            count += node.type in calls.split() and node.parent != initializer
            if node.type not in import_kinds.split():
                continue
            if language == "python":
                if node.type == "import_statement":
                    # Each imported name has its own source range, including multi-import statements.
                    sites = [(n, n.child_by_field_name("name") if n.type == "aliased_import" else n)
                             for n in node.children_by_field_name("name")]
                else:
                    sites = [(node, node.child_by_field_name("module_name"))]
            elif language == "go":
                sites = [(n, n.child_by_field_name("path")) for n in descendants(node) if n.type == "import_spec"]
            else:
                specifier = node.child_by_field_name("source")
                if node.type == "export_statement" and specifier is None:
                    continue
                sites = [(node, specifier)]
            for site, specifier in sites:
                text = (specifier or site).text.decode()
                if language in {"javascript", "typescript", "tsx", "go"} and specifier is not None:
                    # Keep the literal as unresolved evidence when its module name is empty.
                    text = text[1:-1] or text
                if len(imports) >= MAX_FACTS:
                    raise ValueError("Import count exceeds the result limit")
                imports.append(CodeGraphImport(**location(item.path, site), specifier=text, provenance="unresolved"))
        files.append(CodeGraphFile(**metadata, language=language,
                                   status="partial" if diagnostic else "parsed",
                                   reason="syntax_error" if diagnostic else None,
                                   diagnostic=diagnostic,
                                   line_count=source_lines(source), unresolved_calls=count))
    available = {item.path for item in files if item.status not in {"excluded", "failed"}}
    for item in imports:
        language = LANGUAGES[PurePosixPath(item.file_path).suffix][0]
        target = import_target(item.file_path, item.specifier, language, available)
        if target is not None:
            item.target_path, item.provenance = target, "relative_path"
    return CodeGraphResult(**request.model_dump(exclude={"files"}), files=files, symbols=symbols, imports=imports)


def main():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        if request_path.stat().st_size > MAX_REQUEST_BYTES:
            raise ValueError("Oversize request")
        request = CodeGraphRequest.model_validate_json(request_path.read_bytes())
        if not {"schema_version", "extractor_version"} <= request.model_fields_set:
            raise ValueError("Explicit protocol and extractor versions are required")
    except ValueError:
        return 2
    try:
        result_path.write_text(extract(request).model_dump_json(), encoding="utf-8")
    except OSError as exc:
        return 4 if exc.errno in {28, 122} else 3
    except (ValueError, MemoryError):
        return 3
    return 0

if __name__ == "__main__":
    sys.exit(main())
