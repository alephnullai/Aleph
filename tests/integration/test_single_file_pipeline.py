"""Integration test: full single-file pipeline."""

import pytest
from aleph.cli import run_pipeline


class TestSingleFilePipeline:
    def test_cpp_simple_produces_valid_output(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        assert result["symbols_extracted"] > 0
        assert result["struct_text"]
        assert result["bodies_text"]
        assert "[ALEPH:STRUCT:1.0]" in result["struct_text"]
        assert "[ALEPH:BODIES:1.0]" in result["bodies_text"]

    def test_cpp_complex_produces_valid_output(self, cpp_complex_path):
        result = run_pipeline(cpp_complex_path)
        assert result["symbols_extracted"] > 5
        assert result["semantic_hash"]

    def test_rust_simple_produces_valid_output(self, rust_simple_path):
        result = run_pipeline(rust_simple_path)
        assert result["symbols_extracted"] > 0
        assert result["language"] == "rust"

    def test_rust_complex_produces_valid_output(self, rust_complex_path):
        result = run_pipeline(rust_complex_path)
        assert result["symbols_extracted"] > 5

    def test_symbol_ids_consistent_across_components(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        struct_ids = set(result["struct_component"].symbols.keys())
        body_ids = {str(e.symbol_id) for e in result["bodies_component"].entries}
        # Struct is a navigation subset; every struct symbol must have a body entry.
        assert struct_ids.issubset(body_ids)

    def test_produces_semantic_hash(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        assert len(result["semantic_hash"]) == 12

    def test_call_edges_found(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        assert result["call_edges"] >= 3

    def test_cpp_simple_expected_symbols_exist(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        qnames = {s.raw.qualified_name for s in result["symbols"]}
        assert "main" in qnames
        assert "calculateDistance" in qnames
        assert "calculateArea" in qnames
        assert "printResult" in qnames

    def test_cpp_simple_main_call_edges(self, cpp_simple_path):
        result = run_pipeline(cpp_simple_path)
        by_qname = {s.raw.qualified_name: str(s.id) for s in result["symbols"]}
        main_id = by_qname["main"]
        expected_callees = {
            by_qname["calculateDistance"],
            by_qname["calculateArea"],
            by_qname["printResult"],
        }
        observed = {
            callee
            for caller, callee in result["struct_component"].call_edges
            if caller == main_id
        }
        assert expected_callees.issubset(observed)
