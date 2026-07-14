"""Tests for intent inference from AST patterns."""

import os
import pytest

from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry
from aleph.inference.intent_inference import IntentInferrer

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def _run_inference(source_code, language, source_file="test_file"):
    parser = TreeSitterParser()
    tree, source, lang = parser.parse_string(source_code, language)
    source_bytes = source.encode("utf-8")
    extractor = SymbolExtractor()
    raw_symbols = extractor.extract(tree, source, lang, source_file=source_file)
    registry = SymbolRegistry()
    symbols = [registry.register(raw) for raw in raw_symbols]
    inferrer = IntentInferrer()
    return inferrer.infer(tree, source_bytes, lang, symbols), symbols


# ── Python Tests ──

def test_python_assert_precondition():
    code = '''
def validate(x):
    assert x > 0
    return x * 2
'''
    component, symbols = _run_inference(code, "python")
    preconditions = [e for e in component.entries if e.tag_type == "PRECONDITION"]
    assert len(preconditions) >= 1
    assert "assert" in preconditions[0].description


def test_python_try_except_intent():
    code = '''
def safe_op():
    try:
        return 1 / 0
    except ZeroDivisionError:
        return None
'''
    component, _ = _run_inference(code, "python")
    intents = [e for e in component.entries if e.tag_type == "INTENT" and "error-boundary" in e.description]
    assert len(intents) >= 1


def test_python_property_decorator():
    code = '''
class Foo:
    @property
    def value(self):
        return self._value
'''
    component, _ = _run_inference(code, "python")
    accessors = [e for e in component.entries if e.description == "accessor"]
    assert len(accessors) >= 1


def test_python_staticmethod_decorator():
    code = '''
class Foo:
    @staticmethod
    def create():
        return Foo()
'''
    component, _ = _run_inference(code, "python")
    statics = [e for e in component.entries if e.description == "static"]
    assert len(statics) >= 1


# ── C++ Tests ──

def test_cpp_assert_precondition():
    code = '''
int validate(int x) {
    assert(x >= 0);
    return x;
}
'''
    component, _ = _run_inference(code, "cpp")
    preconditions = [e for e in component.entries if e.tag_type == "PRECONDITION"]
    assert len(preconditions) >= 1


def test_cpp_throw_precondition():
    code = '''
void setAge(int age) {
    if (age < 0) {
        throw std::invalid_argument("negative age");
    }
}
'''
    component, _ = _run_inference(code, "cpp")
    preconditions = [e for e in component.entries if e.tag_type == "PRECONDITION" and "throw" in e.description]
    assert len(preconditions) >= 1


def test_cpp_try_catch_intent():
    code = '''
int safe_parse(const char* s) {
    try {
        return atoi(s);
    } catch (...) {
        return 0;
    }
}
'''
    component, _ = _run_inference(code, "cpp")
    boundaries = [e for e in component.entries if e.description == "error-boundary"]
    assert len(boundaries) >= 1


def test_cpp_const_invariant():
    code = '''
class Foo {
public:
    const int MAX_SIZE = 100;
    int getValue() const { return value_; }
private:
    int value_;
};
'''
    component, _ = _run_inference(code, "cpp")
    invariants = [e for e in component.entries if e.tag_type == "INVARIANT"]
    assert len(invariants) >= 1


# ── Rust Tests ──

def test_rust_assert_precondition():
    code = '''
fn validate(x: i32) -> i32 {
    assert!(x > 0);
    x * 2
}
'''
    component, _ = _run_inference(code, "rust")
    preconditions = [e for e in component.entries if e.tag_type == "PRECONDITION"]
    assert len(preconditions) >= 1
    assert any("assert" in p.description for p in preconditions)


def test_rust_unsafe_intent():
    code = '''
fn raw_access(ptr: *const i32) -> i32 {
    unsafe { *ptr }
}
'''
    component, _ = _run_inference(code, "rust")
    unsafe_intents = [e for e in component.entries if "unsafe" in e.description]
    assert len(unsafe_intents) >= 1


def test_rust_debug_assert():
    code = '''
fn process(n: usize) -> usize {
    debug_assert!(n < 1000);
    n + 1
}
'''
    component, _ = _run_inference(code, "rust")
    preconditions = [e for e in component.entries if e.tag_type == "PRECONDITION"]
    assert len(preconditions) >= 1


# ── Fixture-based Tests ──

def test_intents_fixture_cpp():
    path = os.path.join(FIXTURES_DIR, "cpp", "intents_sample.cpp")
    if not os.path.exists(path):
        pytest.skip("Fixture not available")
    with open(path) as f:
        code = f.read()
    component, _ = _run_inference(code, "cpp", path)
    assert len(component.entries) > 0
    tag_types = {e.tag_type for e in component.entries}
    assert "PRECONDITION" in tag_types


def test_symbol_intents_populated():
    code = '''
def validate(x):
    assert x > 0
    return x
'''
    component, symbols = _run_inference(code, "python")
    func_syms = [s for s in symbols if s.raw.name == "validate"]
    assert len(func_syms) == 1
    assert len(func_syms[0].intents) > 0


def test_empty_source():
    code = ""
    # Should not crash with empty source
    parser = TreeSitterParser()
    tree, source, lang = parser.parse_string(code, "python")
    source_bytes = source.encode("utf-8")
    inferrer = IntentInferrer()
    result = inferrer.infer(tree, source_bytes, lang, [])
    assert len(result.entries) == 0
