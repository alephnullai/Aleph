"""Java extraction regression tests (alephnullai/Aleph issue #1).

Each test names the extraction path it guards. The three marked REGRESSION
were written against the shape the feature request proposed and observed to
fail there before the implementation landed.
"""

import os

import pytest

from aleph.ingest.languages import LanguageRegistry
from aleph.ingest.node_types import (
    clear_unknown_node_types,
    get_unknown_node_types,
)
from aleph.ingest.parser import TreeSitterParser
from aleph.model.enums import SymbolKind
from aleph.structure.callgraph import CallGraphBuilder
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "fixtures", "java", "extraction_patterns.java",
)


@pytest.fixture
def java_source():
    with open(FIXTURE) as handle:
        return handle.read()


@pytest.fixture
def symbols(java_source):
    parser = TreeSitterParser()
    tree = parser.parse(java_source, "java")
    return SymbolExtractor().extract(tree, java_source, "java", FIXTURE)


def by_name(symbols, name):
    return [s for s in symbols if s.name == name]


def one(symbols, name):
    matches = by_name(symbols, name)
    assert matches, f"no symbol named {name!r}"
    return matches[0]


class TestRegistration:
    def test_java_extension_maps(self):
        assert LanguageRegistry.language_for_extension(".java") == "java"

    def test_java_is_advertised_as_supported(self):
        assert "java" in LanguageRegistry.supported_languages()

    def test_grammar_loads(self):
        assert LanguageRegistry.get_language("java") is not None


class TestNameExtraction:
    def test_fields_are_extracted(self, symbols):
        """REGRESSION: the proposed first-identifier scan returns None here.

        A field_declaration is [modifiers, type, variable_declarator, ";"] —
        no direct child is an `identifier`, so every field was dropped.
        """
        assert one(symbols, "customer").kind is SymbolKind.VARIABLE
        assert one(symbols, "lines").kind is SymbolKind.VARIABLE

    def test_static_final_field_is_a_constant(self, symbols):
        """Node type alone cannot separate these — modifiers decide."""
        assert one(symbols, "MAX_LINES").kind is SymbolKind.CONSTANT
        assert one(symbols, "CURRENCY").kind is SymbolKind.CONSTANT
        assert one(symbols, "customer").kind is SymbolKind.VARIABLE

    def test_multi_declarator_field_indexes_the_first(self, symbols):
        """Documented limitation, pinned so a change is deliberate."""
        assert by_name(symbols, "subtotal")
        assert not by_name(symbols, "total")

    def test_method_name_is_not_its_return_type(self, symbols):
        assert one(symbols, "getCustomer").kind is SymbolKind.FUNCTION
        assert not by_name(symbols, "String")

    def test_generic_method_name_survives_type_parameters(self, symbols):
        assert one(symbols, "sorted").kind is SymbolKind.FUNCTION

    def test_overloaded_constructors_both_survive(self, symbols):
        ctors = [
            s for s in by_name(symbols, "Invoice")
            if s.kind is SymbolKind.FUNCTION
        ]
        assert len(ctors) == 2

    def test_enum_constants(self, symbols):
        for constant in ("DRAFT", "ISSUED", "PAID"):
            assert one(symbols, constant).kind is SymbolKind.CONSTANT

    def test_record_and_annotation_type_are_types(self, symbols):
        """REGRESSION: both node types were absent from the request."""
        assert one(symbols, "Line").kind is SymbolKind.TYPE
        assert one(symbols, "Reviewed").kind is SymbolKind.TYPE

    def test_interface_is_a_type_and_its_methods_are_functions(self, symbols):
        assert one(symbols, "Payable").kind is SymbolKind.TYPE
        assert one(symbols, "isFree").kind is SymbolKind.FUNCTION

    def test_imports_drop_keywords_and_semicolon(self, symbols):
        names = {s.name for s in symbols if s.kind is SymbolKind.DEPENDENCY}
        assert "java.util.List" in names
        assert "java.util.Collections.emptyList" in names   # static import
        assert "java.util.function.*" in names              # wildcard import
        assert not any(n.startswith("import") or n.endswith(";") for n in names)


class TestPackageScoping:
    def test_package_is_a_module_symbol_named_after_itself_only(self, symbols):
        package = one(symbols, "com.example.billing")
        assert package.kind is SymbolKind.MODULE
        assert package.qualified_name == "com.example.billing"

    def test_declarations_are_qualified_by_package(self, symbols):
        """REGRESSION: mapping package_declaration to MODULE alone does not
        do this — the package is a sibling statement, not a container, so
        _CONTAINER_TYPES has nothing to walk into."""
        assert one(symbols, "Invoice").qualified_name == (
            "com.example.billing::Invoice"
        )
        assert one(symbols, "getCustomer").qualified_name == (
            "com.example.billing::Invoice::getCustomer"
        )


class TestSignatures:
    def test_signature_stops_at_the_body(self, symbols):
        assert one(symbols, "getCustomer").signature_text == (
            "public String getCustomer()"
        )

    def test_abstract_method_signature_has_no_trailing_semicolon(self, symbols):
        interface_method = [
            s for s in by_name(symbols, "getCustomer")
            if s.scope.endswith("Payable")
        ]
        assert interface_method
        assert interface_method[0].signature_text == "String getCustomer()"

    def test_type_signature_stops_at_the_class_body(self, symbols):
        assert one(symbols, "Invoice").signature_text == (
            "public class Invoice implements Payable"
        )


class TestCallGraph:
    @pytest.fixture
    def graph(self, java_source):
        parser = TreeSitterParser()
        tree = parser.parse(java_source, "java")
        raws = SymbolExtractor().extract(tree, java_source, "java")
        registry = SymbolRegistry()
        syms = [registry.register(r) for r in raws]
        builder = CallGraphBuilder()
        edges, meta = builder.build_with_metadata(
            tree, java_source.encode("utf-8"), "java", syms
        )
        names = {str(s.id): s.raw.qualified_name for s in syms}
        return {(names[a], names[b]) for a, b in edges}, meta

    def test_method_call_resolves_to_the_method_not_the_receiver(self, graph):
        """REGRESSION: `lines.size()` is [object, ".", name, arguments].

        The positional scan returns `lines` — which IS a symbol here (the
        field), so the bug produces a confidently wrong edge rather than a
        missing one. Verified red with only the callee fix reverted; it needs
        field extraction working to bite, so the two fixes are chained.
        """
        edges, _ = graph
        assert not any(
            callee.endswith("::lines") for _, callee in edges
        ), "a method call resolved to its receiver field"

    def test_constructor_call_creates_an_edge(self, graph):
        edges, _ = graph
        assert any(
            caller.endswith("::addLine") and callee.endswith("Line")
            for caller, callee in edges
        )

    def test_unresolved_edges_are_only_jdk_calls(self, graph):
        """Unresolved is correct for JDK targets — they aren't in this file.

        What must NOT appear is `this`/`super`: mapping
        explicit_constructor_invocation would resolve the callee to a bare
        keyword and inflate this list with edges that can never resolve.
        """
        _, meta = graph
        unresolved = {
            m["callee_name"] for m in meta if m["status"] == "unresolved"
        }
        assert unresolved <= {"ArrayList", "emptyList", "size", "add"}
        assert not unresolved & {"this", "super"}


class TestUnknownNodeTypes:
    def test_java_reports_no_extraction_gaps(self, java_source):
        """The unknown accumulator is how real gaps get found. If Java floods
        it with keywords and punctuation, it stops being a usable signal."""
        clear_unknown_node_types()
        parser = TreeSitterParser()
        tree = parser.parse(java_source, "java")
        SymbolExtractor().extract(tree, java_source, "java")
        assert get_unknown_node_types().get("java", set()) == set()
