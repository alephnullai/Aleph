"""Tests for static test coverage mapping."""

import os
import pytest

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind
from aleph.inference.test_coverage import TestCoverageMapper


def _make_symbol(name, kind=SymbolKind.FUNCTION, scope="", sid=None):
    raw = RawSymbol(
        name=name,
        qualified_name=f"{scope}::{name}" if scope else name,
        kind=kind,
        scope=scope,
        span=Span(0, 0, 10, 0),
        language="python",
        source_file="test_file.py",
    )
    symbol_id = SymbolID.from_string(sid) if sid else SymbolID(prefix=kind.value, hex_hash=name[:6].ljust(6, "0"))
    return Symbol(id=symbol_id, raw=raw)


def test_identify_python_tests():
    mapper = TestCoverageMapper()
    symbols = [
        _make_symbol("add", sid="f_aaa000"),
        _make_symbol("test_add", sid="f_bbb000"),
        _make_symbol("test_multiply", sid="f_ccc000"),
    ]
    tests = mapper._identify_tests(symbols, "python", "test_sample.py")
    assert len(tests) == 2
    assert all(s.raw.name.startswith("test_") for s in tests)


def test_covered_symbol():
    mapper = TestCoverageMapper()
    func = _make_symbol("add", sid="f_aaa000")
    test = _make_symbol("test_add", sid="f_bbb000")
    symbols = [func, test]
    edges = [("f_bbb000", "f_aaa000")]  # test_add calls add

    result = mapper.map(symbols, edges, "python", "test_sample.py")
    coverage = {str(c.symbol_id): c for c in result.coverage}
    assert "f_aaa000" in coverage
    assert coverage["f_aaa000"].status == "covered"
    assert "f_bbb000" in coverage["f_aaa000"].test_ids


def test_uncovered_symbol():
    mapper = TestCoverageMapper()
    func1 = _make_symbol("add", sid="f_aaa000")
    func2 = _make_symbol("multiply", sid="f_ccc000")
    test = _make_symbol("test_add", sid="f_bbb000")
    symbols = [func1, func2, test]
    edges = [("f_bbb000", "f_aaa000")]  # test_add calls add, not multiply

    result = mapper.map(symbols, edges, "python", "test_sample.py")
    coverage = {str(c.symbol_id): c for c in result.coverage}
    assert coverage["f_ccc000"].status == "none"


def test_transitive_coverage():
    mapper = TestCoverageMapper()
    func_a = _make_symbol("a", sid="f_aaa000")
    func_b = _make_symbol("b", sid="f_bbb000")
    test = _make_symbol("test_a", sid="f_ttt000")
    symbols = [func_a, func_b, test]
    edges = [("f_ttt000", "f_aaa000"), ("f_aaa000", "f_bbb000")]

    result = mapper.map(symbols, edges, "python", "test_sample.py")
    coverage = {str(c.symbol_id): c for c in result.coverage}
    assert coverage["f_bbb000"].status == "covered"


def test_infer_behaviors():
    mapper = TestCoverageMapper()
    assert mapper._infer_behaviors("test_add_positive") == ["add", "positive"]
    assert mapper._infer_behaviors("test_empty") == ["empty"]


def test_test_details():
    mapper = TestCoverageMapper()
    func = _make_symbol("add", sid="f_aaa000")
    test = _make_symbol("test_add_works", sid="f_bbb000")
    symbols = [func, test]
    edges = [("f_bbb000", "f_aaa000")]

    result = mapper.map(symbols, edges, "python", "test_sample.py")
    assert len(result.test_details) == 1
    detail = result.test_details[0]
    assert "f_aaa000" in detail.covers
    assert "add" in detail.behaviors


def test_no_tests():
    mapper = TestCoverageMapper()
    func = _make_symbol("add", sid="f_aaa000")
    symbols = [func]

    result = mapper.map(symbols, [], "python", "main.py")
    assert len(result.test_details) == 0
    assert all(c.status == "none" for c in result.coverage)


def test_symbol_coverage_field_populated():
    mapper = TestCoverageMapper()
    func = _make_symbol("add", sid="f_aaa000")
    test = _make_symbol("test_add", sid="f_bbb000")
    assert func.coverage is None

    mapper.map([func, test], [("f_bbb000", "f_aaa000")], "python", "test.py")
    assert func.coverage == "covered"


def test_rust_test_detection():
    mapper = TestCoverageMapper()
    symbols = [
        _make_symbol("process", sid="f_aaa000"),
        _make_symbol("test_process", sid="f_bbb000"),
    ]
    tests = mapper._identify_tests(symbols, "rust", "lib.rs")
    assert len(tests) == 1
