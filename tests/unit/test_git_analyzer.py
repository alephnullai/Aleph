"""Tests for project-level temporal git analyzer."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind
from aleph.model.components import (
    TemporalComponent,
    TemporalEntry,
    ProjectTemporalComponent,
)
from aleph.temporal.git_analyzer import compute_project_temporal, _churn_label
from aleph.emit.serializer import AlephSerializer


def _make_symbol(name, sid, source_file="test.py"):
    raw = RawSymbol(
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        scope="",
        span=Span(0, 0, 5, 0),
        language="python",
        source_file=source_file,
    )
    return Symbol(id=SymbolID.from_string(sid), raw=raw)


def _make_temporal_component(source_file, entries):
    return TemporalComponent(
        source_file=source_file,
        computed_date="2026-03-17",
        entries=entries,
    )


def _make_temporal_entry(sid, age=100, last=10, churn=0, stability="stable"):
    return TemporalEntry(
        symbol_id=SymbolID.from_string(sid),
        age_days=age,
        last_modified_days=last,
        churn_count=churn,
        stability=stability,
    )


class TestChurnLabel:
    def test_low(self):
        assert _churn_label(0) == "low"

    def test_medium(self):
        assert _churn_label(1) == "medium"
        assert _churn_label(2) == "medium"

    def test_high(self):
        assert _churn_label(3) == "high"
        assert _churn_label(10) == "high"


class TestComputeProjectTemporal:
    def test_empty_project(self, tmp_path):
        result = compute_project_temporal(str(tmp_path), {})
        assert isinstance(result, ProjectTemporalComponent)
        assert len(result.entries) == 0
        assert result.computed_date  # non-empty

    def test_single_file_single_symbol(self, tmp_path):
        sym = _make_symbol("foo", "f_aaa111", str(tmp_path / "a.py"))
        temporal = _make_temporal_component(
            str(tmp_path / "a.py"),
            [_make_temporal_entry("f_aaa111", age=200, last=5, churn=0, stability="stable")],
        )
        file_results = {
            str(tmp_path / "a.py"): {
                "temporal_component": temporal,
                "symbols": [sym],
            }
        }
        result = compute_project_temporal(str(tmp_path), file_results)
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.symbol_id == "f_aaa111"
        assert e.qualified_name == "foo"
        assert e.file == "a.py"
        assert e.age_days == 200
        assert e.last_modified_days == 5
        assert e.churn_count == 0
        assert e.churn_label == "low"
        assert e.stability == "stable"

    def test_multi_file_aggregation(self, tmp_path):
        sym_a = _make_symbol("func_a", "f_aaa111", str(tmp_path / "a.py"))
        sym_b = _make_symbol("func_b", "f_bbb222", str(tmp_path / "b.py"))
        temporal_a = _make_temporal_component(
            str(tmp_path / "a.py"),
            [_make_temporal_entry("f_aaa111", age=500, last=120, churn=0, stability="stable")],
        )
        temporal_b = _make_temporal_component(
            str(tmp_path / "b.py"),
            [_make_temporal_entry("f_bbb222", age=10, last=1, churn=5, stability="volatile")],
        )
        file_results = {
            str(tmp_path / "a.py"): {"temporal_component": temporal_a, "symbols": [sym_a]},
            str(tmp_path / "b.py"): {"temporal_component": temporal_b, "symbols": [sym_b]},
        }
        result = compute_project_temporal(str(tmp_path), file_results)
        assert len(result.entries) == 2
        # Volatile symbols should be sorted first
        assert result.entries[0].stability == "volatile"
        assert result.entries[0].symbol_id == "f_bbb222"
        assert result.entries[1].stability == "stable"

    def test_sorting_volatile_first(self, tmp_path):
        syms = [
            _make_symbol("stable_fn", "f_111111", str(tmp_path / "a.py")),
            _make_symbol("active_fn", "f_222222", str(tmp_path / "a.py")),
            _make_symbol("volatile_fn", "f_333333", str(tmp_path / "a.py")),
        ]
        temporal = _make_temporal_component(
            str(tmp_path / "a.py"),
            [
                _make_temporal_entry("f_111111", churn=0, stability="stable"),
                _make_temporal_entry("f_222222", churn=2, stability="active"),
                _make_temporal_entry("f_333333", churn=5, stability="volatile"),
            ],
        )
        file_results = {
            str(tmp_path / "a.py"): {"temporal_component": temporal, "symbols": syms},
        }
        result = compute_project_temporal(str(tmp_path), file_results)
        stabilities = [e.stability for e in result.entries]
        assert stabilities == ["volatile", "active", "stable"]

    def test_missing_temporal_component(self, tmp_path):
        """Files without temporal data are gracefully skipped."""
        sym = _make_symbol("foo", "f_aaa111", str(tmp_path / "a.py"))
        file_results = {
            str(tmp_path / "a.py"): {"symbols": [sym]},
        }
        result = compute_project_temporal(str(tmp_path), file_results)
        assert len(result.entries) == 0

    def test_reference_date_used(self, tmp_path):
        ref = datetime(2026, 6, 15)
        result = compute_project_temporal(str(tmp_path), {}, reference_date=ref)
        assert result.computed_date == "2026-06-15"

    def test_root_stored(self, tmp_path):
        result = compute_project_temporal(str(tmp_path), {})
        assert result.root == str(tmp_path)


class TestProjectTemporalSerialization:
    def test_serialization_format(self, tmp_path):
        sym = _make_symbol("process", "f_abc123", str(tmp_path / "main.py"))
        temporal = _make_temporal_component(
            str(tmp_path / "main.py"),
            [_make_temporal_entry("f_abc123", age=547, last=120, churn=0, stability="stable")],
        )
        file_results = {
            str(tmp_path / "main.py"): {"temporal_component": temporal, "symbols": [sym]},
        }
        component = compute_project_temporal(str(tmp_path), file_results)

        serializer = AlephSerializer()
        text = serializer.serialize_project_temporal(component)

        assert "[ALEPH:TEMPORAL:PROJECT:1.0]" in text
        assert f"[PROJECT:{tmp_path}]" in text
        assert "[COMPUTED:" in text
        assert "[SYMBOLS]" in text
        assert "[/SYMBOLS]" in text
        assert "f_abc123" in text
        assert "age=547d" in text
        assert "last=120d" in text
        assert "churn=low" in text
        assert "stability=stable" in text

    def test_volatile_symbol_in_output(self, tmp_path):
        sym = _make_symbol("hot_fn", "f_hot001", str(tmp_path / "hot.py"))
        temporal = _make_temporal_component(
            str(tmp_path / "hot.py"),
            [_make_temporal_entry("f_hot001", age=12, last=1, churn=8, stability="volatile")],
        )
        file_results = {
            str(tmp_path / "hot.py"): {"temporal_component": temporal, "symbols": [sym]},
        }
        component = compute_project_temporal(str(tmp_path), file_results)

        serializer = AlephSerializer()
        text = serializer.serialize_project_temporal(component)

        assert "stability=volatile" in text
        assert "churn=high" in text
        assert "age=12d" in text
        assert "last=1d" in text
