"""Tests for semantic diff engine."""

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind
from aleph.model.components import StructComponent
from aleph.diff.semantic_diff import SemanticDiff, SemanticDiffReport


def _make_symbol(name, sid, body="", sig=""):
    raw = RawSymbol(
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        scope="",
        span=Span(0, 0, 5, 0),
        language="python",
        source_file="test.py",
        body_text=body,
        signature_text=sig,
    )
    return Symbol(id=SymbolID.from_string(sid), raw=raw)


def _make_result(symbols, call_edges=None, sem_hash="abc123"):
    struct = StructComponent(
        source_file="test.py",
        call_edges=call_edges or [],
        symbols={str(s.id): s for s in symbols},
    )
    return {
        "semantic_hash": sem_hash,
        "symbols": symbols,
        "struct_component": struct,
    }


def test_no_prior():
    diff = SemanticDiff()
    sym = _make_symbol("func", "f_aaa111")
    result = _make_result([sym])
    report = diff.diff(None, result)
    assert report.semantic_hash_changed is True
    assert "f_aaa111" in report.symbols_added
    assert report.previous_hash == ""


def test_unchanged():
    diff = SemanticDiff()
    sym = _make_symbol("func", "f_aaa111")
    result = _make_result([sym], sem_hash="abc123")
    old_entry = {
        "semantic_hash": "abc123",
        "symbols": [{"id": "f_aaa111", "name": "func"}],
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert report.semantic_hash_changed is False
    assert len(report.symbols_added) == 0
    assert len(report.symbols_removed) == 0


def test_symbol_added():
    diff = SemanticDiff()
    sym1 = _make_symbol("func1", "f_aaa111")
    sym2 = _make_symbol("func2", "f_bbb222")
    result = _make_result([sym1, sym2], sem_hash="new123")
    old_entry = {
        "semantic_hash": "old123",
        "symbols": [{"id": "f_aaa111", "name": "func1"}],
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert "f_bbb222" in report.symbols_added
    assert len(report.symbols_removed) == 0


def test_symbol_removed():
    diff = SemanticDiff()
    sym1 = _make_symbol("func1", "f_aaa111")
    result = _make_result([sym1], sem_hash="new123")
    old_entry = {
        "semantic_hash": "old123",
        "symbols": [
            {"id": "f_aaa111", "name": "func1"},
            {"id": "f_bbb222", "name": "func2"},
        ],
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert "f_bbb222" in report.symbols_removed
    assert len(report.symbols_added) == 0


def test_call_edges_changed():
    diff = SemanticDiff()
    sym1 = _make_symbol("a", "f_aaa111")
    sym2 = _make_symbol("b", "f_bbb222")
    result = _make_result([sym1, sym2], call_edges=[("f_aaa111", "f_bbb222")], sem_hash="new")
    old_entry = {
        "semantic_hash": "old",
        "symbols": [
            {"id": "f_aaa111", "name": "a"},
            {"id": "f_bbb222", "name": "b"},
        ],
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert ("f_aaa111", "f_bbb222") in report.calls_added
    assert len(report.calls_removed) == 0


def test_signature_changed():
    diff = SemanticDiff()
    sym = _make_symbol("func", "f_aaa111", sig="def func(x, y)")
    result = _make_result([sym], sem_hash="new")
    old_entry = {
        "semantic_hash": "old",
        "symbols": [{"id": "f_aaa111", "name": "func"}],
        "signature_hashes": {"f_aaa111": "different"},
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert "f_aaa111" in report.signatures_changed


def test_body_changed():
    diff = SemanticDiff()
    sym = _make_symbol("func", "f_aaa111", body="return 42")
    result = _make_result([sym], sem_hash="new")
    old_entry = {
        "semantic_hash": "old",
        "symbols": [{"id": "f_aaa111", "name": "func"}],
        "body_hashes": {"f_aaa111": "oldhash1"},
        "calls": [],
    }
    report = diff.diff(old_entry, result)
    assert "f_aaa111" in report.bodies_changed


def test_report_to_dict():
    report = SemanticDiffReport(
        semantic_hash_changed=True,
        previous_hash="old",
        current_hash="new",
        symbols_added=["f_aaa111"],
    )
    d = report.to_dict()
    assert d["semantic_hash_changed"] is True
    assert d["symbols_added"] == ["f_aaa111"]
    assert isinstance(d["calls_added"], list)
