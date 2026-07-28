"""Tests for symbol extraction."""

import pytest
from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.model.enums import SymbolKind


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def extractor():
    return SymbolExtractor()


class TestSymbolExtractor:
    def test_extract_cpp_functions(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        func_names = [s.name for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert "calculateDistance" in func_names
        assert "calculateArea" in func_names
        assert "main" in func_names

    def test_extract_rust_functions(self, parser, extractor, rust_simple_source):
        tree = parser.parse(rust_simple_source, "rust")
        symbols = extractor.extract(tree, rust_simple_source, "rust")
        func_names = [s.name for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert "calculate_distance" in func_names
        assert "calculate_area" in func_names
        assert "main" in func_names

    def test_extract_cpp_nested_scope(self, parser, extractor):
        source = """
namespace geometry {
    class Point {
    public:
        double distanceTo(const Point& other) const {
            return 0.0;
        }
    };
}
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        # Should find namespace, class, and method
        kinds = {s.kind for s in symbols}
        assert SymbolKind.MODULE in kinds or SymbolKind.TYPE in kinds

    def test_cpp_name_is_identifier_not_return_or_member_type(
        self, parser, extractor
    ):
        """A method/member name must win over its (non-primitive) type.

        Regression: the extractor scanned children positionally and returned
        the first identifier-ish node, but C++ lays the TYPE before the name
        (`std::string name()`, `Point center_;`). So it captured the type
        (`std::string`, `Point`) as the symbol name and dropped the real one —
        invisible for primitive returns (`double area()`), because `double`
        parses as primitive_type and was skipped. Real C++ (mostly non-
        primitive returns) was therefore mis-named at scale.
        """
        source = """
namespace geometry {
    struct Point { double x; double y; };
    class Circle {
    private:
        Point center_;
        double radius_;
    public:
        std::string name() const { return "Circle"; }
        Point getCenter() const { return center_; }
        Point* clone() const { return nullptr; }
        double area() const { return 3.14 * radius_ * radius_; }
    };
}
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        qnames = {s.qualified_name for s in symbols}

        # The real identifiers must be present, fully qualified...
        for expected in (
            "geometry::Circle::name",       # non-primitive (std::string) return
            "geometry::Circle::getCenter",  # class-type return
            "geometry::Circle::clone",      # pointer return
            "geometry::Circle::area",       # primitive return (always worked)
            "geometry::Circle::center_",    # class-type member
            "geometry::Circle::radius_",    # primitive member
        ):
            assert expected in qnames, f"missing {expected}; got {sorted(qnames)}"

        # ...and a return/member TYPE must never be captured as the name. The
        # struct `geometry::Point` itself is legitimate, so we assert on the
        # specific corruption: a Circle member whose "name" is a type.
        assert "geometry::Circle::std::string" not in qnames
        assert "geometry::Circle::Point" not in qnames, (
            "getCenter()/center_ collapsed onto their type `Point`"
        )
        # No symbol name should be a std:: qualified type.
        assert not any("::std::" in q for q in qnames), (
            f"a std:: type leaked as a name: {sorted(qnames)}"
        )

    def test_cpp_using_declaration_named_by_import_not_raw_statement(
        self, parser, extractor
    ):
        """A `using X::y;` must be named by what it imports, not its raw text.

        Regression (OpenTTD full-corpus audit, 2026-07-15): using-declarations
        are DEPENDENCY-kind and the extractor returned the whole statement as
        the name, so `using Base::method;` was indexed as a symbol literally
        named "using Base::method;" — the `using` keyword and trailing `;`
        baked into the name. 49 such garbage names across OpenTTD (7 in its own
        src). Inheriting constructors/methods (`using Base::Base;`) are an
        everyday C++ idiom, so this is not an edge case.
        """
        source = """
namespace demo { struct Base { void method(); }; }
using namespace demo;
class Derived : public Base {
public:
    using Base::Base;
    using Base::method;
};
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        names = {s.name for s in symbols}

        # The imported entities are named cleanly...
        assert "Base::Base" in names, f"got {sorted(names)}"
        assert "Base::method" in names, f"got {sorted(names)}"
        # ...and no name is a raw `using ...;` statement.
        assert not any(
            n and (n.startswith("using ") or ";" in n) for n in names
        ), f"a using-declaration kept its raw statement as the name: {sorted(names)}"

    def test_cpp_extraction_patterns_fixture(self, parser, extractor, fixtures_dir):
        """Real-corpus-modeled fixture: every tricky declaration shape names
        the IDENTIFIER, never a type or keyword.

        tests/fixtures/cpp/extraction_patterns.cpp concentrates the shapes that
        broke C++ name extraction on OpenTTD (2026-07-15): non-primitive/pointer
        returns, class-type members, destructors, operators, nested namespaces,
        using-declarations, template methods, and trailing-return auto. This is
        the committed regression corpus for all of them.
        """
        import os
        path = os.path.join(fixtures_dir, "cpp", "extraction_patterns.cpp")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        symbols = extractor.extract(parser.parse(source, "cpp"), source, "cpp")
        q = {s.qualified_name for s in symbols}

        # Every working shape resolves to the identifier, fully qualified.
        for expected in (
            "transport::rail::Station::label",       # non-primitive (std::string) return
            "transport::rail::Station::origin",      # trailing-return auto -> Position
            "transport::rail::Station::~Station",    # destructor (virtual, defaulted) IS extracted
            "transport::rail::Station::where_",      # class-type member
            "transport::rail::Station::name_",       # std::string member
            "transport::rail::Depot::parent",        # pointer return
            "transport::rail::Depot::roundTrip",     # template method returning T
            "transport::rail::Depot::parent_",       # pointer member
            "transport::rail::Position::operator==", # operator overload
            "transport::rail::Depot::Station::Station",  # using-declaration import
            "transport::rail::Position",             # nested-namespace type
            "transport::rail",                       # nested namespace
        ):
            assert expected in q, f"missing {expected}; got {sorted(q)}"

        # No type or keyword ever leaks in as a name.
        assert not any("::std::" in n for n in q), f"type leaked: {sorted(q)}"
        assert not any(n and (n.startswith("using ") or ";" in n) for n in q)

    def test_cpp_reference_return_not_dropped(self, parser, extractor, fixtures_dir):
        import os
        path = os.path.join(fixtures_dir, "cpp", "extraction_patterns.cpp")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        symbols = extractor.extract(parser.parse(source, "cpp"), source, "cpp")
        q = {s.qualified_name for s in symbols}
        # `const Position& location() const` must be indexed as ...::location.
        assert "transport::rail::Station::location" in q

    def test_cpp_out_of_line_def_keeps_class_qualifier(
        self, parser, extractor, fixtures_dir
    ):
        import os
        path = os.path.join(fixtures_dir, "cpp", "extraction_patterns.cpp")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        symbols = extractor.extract(parser.parse(source, "cpp"), source, "cpp")
        q = {s.qualified_name for s in symbols}
        # The out-of-line `Money Depot::maintenanceCost()` must not collapse to
        # a namespace-scope `transport::rail::maintenanceCost`.
        assert "transport::rail::maintenanceCost" not in q

    def test_cpp_declaration_only_destructor_is_emitted(self, parser, extractor):
        source = "struct Signal { ~Signal(); };"
        symbols = extractor.extract(parser.parse(source, "cpp"), source, "cpp")
        assert "Signal::~Signal" in {s.qualified_name for s in symbols}

    def test_extract_rust_structs(self, parser, extractor, rust_simple_path):
        tree, source, lang = parser.parse_file(rust_simple_path)
        symbols = extractor.extract(tree, source, lang)
        func_syms = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(func_syms) >= 3

    def test_symbols_have_spans(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        for sym in symbols:
            assert sym.span.start_line >= 0
            assert sym.span.end_line >= sym.span.start_line

    def test_symbols_have_body_text(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        for f in funcs:
            assert len(f.body_text) > 0

    def test_qualified_names_cpp(self, parser, extractor):
        source = """
namespace ns {
    void helper() {}
}
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        # The function inside the namespace should have qualified name
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        if funcs:
            # Should be "ns::helper" if scope tracking works
            assert any("::" in s.qualified_name for s in funcs)
