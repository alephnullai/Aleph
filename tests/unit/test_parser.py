"""Tests for tree-sitter parser."""

import pytest
from aleph.ingest.parser import TreeSitterParser


@pytest.fixture
def parser():
    return TreeSitterParser()


class TestTreeSitterParser:
    def test_parse_cpp(self, parser, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        assert tree is not None
        assert tree.root_node.type == "translation_unit"
        assert not tree.root_node.has_error

    def test_parse_rust(self, parser, rust_simple_source):
        tree = parser.parse(rust_simple_source, "rust")
        assert tree is not None
        assert tree.root_node.type == "source_file"
        assert not tree.root_node.has_error

    def test_parse_file(self, parser, cpp_simple_path):
        tree, source, language = parser.parse_file(cpp_simple_path)
        assert tree is not None
        assert language == "cpp"
        assert len(source) > 0

    def test_parse_rust_file(self, parser, rust_simple_path):
        tree, source, language = parser.parse_file(rust_simple_path)
        assert tree is not None
        assert language == "rust"

    def test_unsupported_extension(self, parser, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("content")
        with pytest.raises(ValueError, match="Unsupported"):
            parser.parse_file(str(f))

    def test_identifies_function_nodes(self, parser, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        func_nodes = []
        self._find_nodes(tree.root_node, "function_definition", func_nodes)
        assert len(func_nodes) >= 3  # calculateDistance, calculateArea, printResult, main

    def _find_nodes(self, node, target_type, results):
        if node.type == target_type:
            results.append(node)
        for child in node.children:
            self._find_nodes(child, target_type, results)
