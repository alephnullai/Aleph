"""Canonical mapping from tree-sitter node types to SymbolKind."""

from aleph.model.enums import SymbolKind

# Shared TypeScript/JavaScript function and class node types
_TS_JS_COMMON: dict[str, SymbolKind] = {
    "function_declaration": SymbolKind.FUNCTION,
    "method_definition": SymbolKind.FUNCTION,
    "arrow_function": SymbolKind.FUNCTION,
    "generator_function_declaration": SymbolKind.FUNCTION,
    "class_declaration": SymbolKind.TYPE,
    "import_statement": SymbolKind.DEPENDENCY,
    "export_statement": SymbolKind.DEPENDENCY,
}

# tree-sitter node type -> SymbolKind
NODE_TYPE_MAP: dict[str, dict[str, SymbolKind]] = {
    "cpp": {
        "function_definition": SymbolKind.FUNCTION,
        "class_specifier": SymbolKind.TYPE,
        "struct_specifier": SymbolKind.TYPE,
        "enum_specifier": SymbolKind.TYPE,
        "namespace_definition": SymbolKind.MODULE,
        "field_declaration": SymbolKind.VARIABLE,
        "type_definition": SymbolKind.TYPE,
        "template_declaration": SymbolKind.TYPE,
        "preproc_def": SymbolKind.CONSTANT,
        "enumerator": SymbolKind.CONSTANT,
        "preproc_include": SymbolKind.DEPENDENCY,
        "using_declaration": SymbolKind.DEPENDENCY,
    },
    "rust": {
        "function_item": SymbolKind.FUNCTION,
        "impl_item": SymbolKind.TYPE,
        "struct_item": SymbolKind.TYPE,
        "enum_item": SymbolKind.TYPE,
        "trait_item": SymbolKind.TYPE,
        "type_item": SymbolKind.TYPE,
        "mod_item": SymbolKind.MODULE,
        "static_item": SymbolKind.VARIABLE,
        "const_item": SymbolKind.CONSTANT,
        "use_declaration": SymbolKind.DEPENDENCY,
    },
    "python": {
        "function_definition": SymbolKind.FUNCTION,
        "class_definition": SymbolKind.TYPE,
        "import_statement": SymbolKind.DEPENDENCY,
        "import_from_statement": SymbolKind.DEPENDENCY,
    },
    "typescript": {
        **_TS_JS_COMMON,
        "interface_declaration": SymbolKind.TYPE,
        "type_alias_declaration": SymbolKind.TYPE,
        "enum_declaration": SymbolKind.TYPE,
        "module": SymbolKind.MODULE,
    },
    "tsx": {
        **_TS_JS_COMMON,
        "interface_declaration": SymbolKind.TYPE,
        "type_alias_declaration": SymbolKind.TYPE,
        "enum_declaration": SymbolKind.TYPE,
        "module": SymbolKind.MODULE,
    },
    "javascript": {
        **_TS_JS_COMMON,
    },
    "go": {
        "function_declaration": SymbolKind.FUNCTION,
        "method_declaration": SymbolKind.FUNCTION,
        "type_declaration": SymbolKind.TYPE,
        "const_declaration": SymbolKind.CONSTANT,
        "var_declaration": SymbolKind.VARIABLE,
        "package_clause": SymbolKind.MODULE,
        "import_declaration": SymbolKind.DEPENDENCY,
    },
    "java": {
        "class_declaration": SymbolKind.TYPE,
        "interface_declaration": SymbolKind.TYPE,
        "enum_declaration": SymbolKind.TYPE,
        # Java 14+ records and `@interface` declarations are types too. The
        # first Java issue's spec omitted both; without them a modern
        # codebase indexes with holes exactly where its data model lives.
        "record_declaration": SymbolKind.TYPE,
        "annotation_type_declaration": SymbolKind.TYPE,
        "method_declaration": SymbolKind.FUNCTION,
        "constructor_declaration": SymbolKind.FUNCTION,
        # `String by();` inside an @interface — an annotation's elements are
        # its public surface, and they carry a `name` field like any method.
        "annotation_type_element_declaration": SymbolKind.FUNCTION,
        # Refined to CONSTANT for `static final` fields — see
        # SymbolExtractor._walk.
        "field_declaration": SymbolKind.VARIABLE,
        "enum_constant": SymbolKind.CONSTANT,
        "import_declaration": SymbolKind.DEPENDENCY,
        "package_declaration": SymbolKind.MODULE,
    },
}

# Node types that contain callable references (for call graph extraction)
CALL_NODE_TYPES: dict[str, list[str]] = {
    "cpp": ["call_expression"],
    "rust": ["call_expression", "macro_invocation"],
    "python": ["call"],
    "typescript": ["call_expression", "new_expression"],
    "tsx": ["call_expression", "new_expression"],
    "javascript": ["call_expression", "new_expression"],
    "go": ["call_expression"],
    # `new Foo()` is a call to Foo's constructor, and constructors are
    # extracted symbols — leaving object_creation_expression out would make
    # every Java constructor look uncalled. TypeScript maps new_expression
    # for the same reason. `this(...)`/`super(...)`
    # (explicit_constructor_invocation) is deliberately NOT mapped: its
    # callee text is the literal keyword, so it resolves to nothing and only
    # inflates the unresolved-edge metadata.
    "java": ["method_invocation", "object_creation_expression"],
}


# Known non-symbol node types (don't log these as "unknown")
_SKIP_NODE_TYPES = frozenset({
    "comment", "line_comment", "block_comment", "doc_comment",
    "string", "string_literal", "raw_string_literal", "template_string",
    "number", "integer_literal", "float_literal", "true", "false", "null", "nil", "none",
    "identifier", "type_identifier", "field_identifier", "property_identifier",
    "(", ")", "{", "}", "[", "]", ",", ";", ":", ".", "=", "==", "!=",
    "+", "-", "*", "/", "<", ">", "&&", "||", "!", "&", "|",
    "program", "source_file", "module", "translation_unit",
    "expression_statement", "return_statement", "if_statement", "for_statement",
    "while_statement", "block", "compound_statement", "statement_block",
    "binary_expression", "unary_expression", "assignment_expression",
    "parenthesized_expression", "subscript_expression", "member_expression",
    "call_expression", "new_expression", "macro_invocation",
    "argument_list", "parameter_list", "formal_parameters", "parameters",
    "pair", "object", "array", "spread_element",
    # Java structural noise. Without these every Java file floods the
    # unknown-node-type accumulator (capped at 200/language), which is the
    # signal used to find real extraction gaps — so leaving them out would
    # bury genuine gaps under keywords and punctuation.
    "modifiers", "marker_annotation", "annotation", "annotation_argument_list",
    "element_value_pair", "class_body", "interface_body", "enum_body",
    "enum_body_declarations", "constructor_body", "annotation_type_body",
    "local_variable_declaration", "variable_declarator", "formal_parameter",
    "spread_parameter", "receiver_parameter", "type_parameters", "type_parameter",
    "type_arguments", "generic_type", "array_type", "dimensions", "scoped_type_identifier",
    "integral_type", "floating_point_type", "boolean_type", "void_type",
    "scoped_identifier", "method_invocation", "object_creation_expression",
    "explicit_constructor_invocation", "field_access", "array_access",
    "lambda_expression", "method_reference", "switch_expression", "switch_block",
    "switch_block_statement_group", "switch_rule", "switch_label",
    "ternary_expression", "instanceof_expression", "cast_expression",
    "update_expression", "assignment_expression",
    "try_statement", "try_with_resources_statement", "resource_specification",
    "resource", "catch_clause", "catch_formal_parameter", "catch_type",
    "finally_clause", "throw_statement", "throws", "synchronized_statement",
    "assert_statement", "labeled_statement", "enhanced_for_statement",
    "do_statement", "break_statement", "continue_statement", "yield_statement",
    "local_variable_declaration_statement", "static_initializer",
    "decimal_integer_literal", "decimal_floating_point_literal",
    "hex_integer_literal", "character_literal", "class_literal",
    "super", "this", "null_literal",
    # Java keyword leaf tokens. tree-sitter-java exposes each keyword as its
    # own node, so `public`/`static`/`class` reach get_symbol_kind and would
    # otherwise be reported as unmapped extraction gaps.
    "package", "import", "class", "interface", "enum", "record", "@interface",
    "public", "private", "protected", "static", "final", "abstract",
    "synchronized", "native", "transient", "volatile", "strictfp", "default",
    "extends", "implements", "permits", "sealed", "non-sealed",
    "super_interfaces", "superclass", "extends_interfaces", "type_list",
    "asterisk", "permits_clause",
    "int", "long", "short", "byte", "char", "float", "double", "boolean", "void",
    "new", "return", "throw", "throws", "try", "catch", "finally", "instanceof",
    "ERROR",
})

_unknown_node_types: dict[str, set[str]] = {}
_UNKNOWN_MAX_PER_LANG = 200  # Cap to prevent unbounded growth in long-running servers


def get_symbol_kind(language: str, node_type: str) -> SymbolKind | None:
    """Map a tree-sitter node type to a SymbolKind, or None if unmapped."""
    lang_map = NODE_TYPE_MAP.get(language, {})
    result = lang_map.get(node_type)
    if result is None and node_type not in _SKIP_NODE_TYPES:
        lang_set = _unknown_node_types.setdefault(language, set())
        if len(lang_set) < _UNKNOWN_MAX_PER_LANG:
            lang_set.add(node_type)
    return result


def get_unknown_node_types() -> dict[str, set[str]]:
    """Return node types encountered but not mapped (potential extraction gaps)."""
    return dict(_unknown_node_types)


def clear_unknown_node_types() -> None:
    """Reset tracked unknown types. Call between builds to prevent accumulation."""
    _unknown_node_types.clear()


def merge_unknown_node_types(other: dict[str, list[str] | set[str]]) -> None:
    """Merge another process's accumulator into this one (parallel builds).

    Worker payloads carry each worker's full accumulator snapshot; the
    union merge is idempotent, and the per-language cap is enforced the
    same way as :func:`get_symbol_kind` does locally.
    """
    for language, node_types in other.items():
        lang_set = _unknown_node_types.setdefault(language, set())
        for node_type in node_types:
            if len(lang_set) >= _UNKNOWN_MAX_PER_LANG:
                break
            lang_set.add(node_type)
