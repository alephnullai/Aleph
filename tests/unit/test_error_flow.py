"""Tests for error flow analysis."""

import os
import pytest

from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry
from aleph.inference.error_flow import ErrorFlowAnalyzer

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def _run_error_flow(source_code, language, source_file="test_file"):
    parser = TreeSitterParser()
    tree, source, lang = parser.parse_string(source_code, language)
    source_bytes = source.encode("utf-8")
    extractor = SymbolExtractor()
    raw_symbols = extractor.extract(tree, source, lang, source_file=source_file)
    registry = SymbolRegistry()
    symbols = [registry.register(raw) for raw in raw_symbols]
    analyzer = ErrorFlowAnalyzer()
    return analyzer.analyze(tree, source_bytes, lang, symbols)


# ── Python Tests ──

def test_python_raise_source():
    code = '''
def validate(x):
    if x < 0:
        raise ValueError("negative value")
    return x
'''
    component = _run_error_flow(code, "python")
    assert len(component.sources) >= 1
    assert component.sources[0].error_type == "ValueError"
    assert component.sources[0].propagation == "throws"


def test_python_try_except_boundary():
    code = '''
def safe_op():
    try:
        return 1 / 0
    except ZeroDivisionError:
        return None
'''
    component = _run_error_flow(code, "python")
    assert len(component.boundaries) >= 1
    assert "ZeroDivisionError" in component.boundaries[0].catches


def test_python_unhandled_error():
    code = '''
def risky():
    raise RuntimeError("boom")
'''
    component = _run_error_flow(code, "python")
    assert len(component.sources) >= 1
    assert len(component.unhandled) >= 1
    assert component.unhandled[0].error_type == "RuntimeError"


def test_python_handled_error():
    code = '''
def safe():
    try:
        raise ValueError("oops")
    except ValueError:
        pass
'''
    component = _run_error_flow(code, "python")
    assert len(component.sources) >= 1
    assert len(component.boundaries) >= 1
    # The error is handled in the same function
    assert len(component.unhandled) == 0


# ── C++ Tests ──

def test_cpp_throw_source():
    code = '''
void fail() {
    throw std::runtime_error("failed");
}
'''
    component = _run_error_flow(code, "cpp")
    assert len(component.sources) >= 1
    assert "runtime_error" in component.sources[0].error_type


def test_cpp_try_catch_boundary():
    code = '''
int safe() {
    try {
        return riskyCall();
    } catch (const std::exception& e) {
        return -1;
    }
}
'''
    component = _run_error_flow(code, "cpp")
    assert len(component.boundaries) >= 1
    assert "exception" in component.boundaries[0].catches


def test_cpp_unhandled():
    code = '''
void danger() {
    throw std::invalid_argument("bad input");
}
'''
    component = _run_error_flow(code, "cpp")
    assert len(component.unhandled) >= 1


# ── Rust Tests ──

def test_rust_err_return():
    code = '''
fn validate(x: i32) -> Result<i32, String> {
    if x < 0 {
        return Err("negative".to_string());
    }
    Ok(x)
}
'''
    component = _run_error_flow(code, "rust")
    err_sources = [s for s in component.sources if "Err" in s.propagation]
    assert len(err_sources) >= 1


# ── Fixture Tests ──

def test_error_handling_fixture_cpp():
    path = os.path.join(FIXTURES_DIR, "cpp", "error_handling.cpp")
    if not os.path.exists(path):
        pytest.skip("Fixture not available")
    with open(path) as f:
        code = f.read()
    component = _run_error_flow(code, "cpp", path)
    assert len(component.sources) > 0
    assert len(component.boundaries) > 0


def test_error_flow_fixture_rust():
    path = os.path.join(FIXTURES_DIR, "rust", "error_flow_sample.rs")
    if not os.path.exists(path):
        pytest.skip("Fixture not available")
    with open(path) as f:
        code = f.read()
    component = _run_error_flow(code, "rust", path)
    # Should find Err() returns
    err_sources = [s for s in component.sources if "Err" in s.propagation]
    assert len(err_sources) >= 1


def test_error_handling_fixture_python():
    path = os.path.join(FIXTURES_DIR, "python", "error_handling.py")
    if not os.path.exists(path):
        pytest.skip("Fixture not available")
    with open(path) as f:
        code = f.read()
    component = _run_error_flow(code, "python", path)
    assert len(component.sources) > 0


def test_empty_source():
    parser = TreeSitterParser()
    tree, source, lang = parser.parse_string("", "python")
    analyzer = ErrorFlowAnalyzer()
    result = analyzer.analyze(tree, source.encode("utf-8"), lang, [])
    assert len(result.sources) == 0
    assert len(result.boundaries) == 0
    assert len(result.unhandled) == 0
