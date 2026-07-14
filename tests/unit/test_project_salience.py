"""Tests for project-wide salience scoring and attention budget."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from aleph.link.project_salience import (
    compute_project_salience, compute_attention_budget,
    CRITICAL_THRESHOLD, IMPORTANT_THRESHOLD, PERIPHERAL_THRESHOLD,
    _is_vendor_file,
)
from aleph.model.components import (
    ProjectSalienceComponent, ProjectSalienceEntry,
    ProjectAttentionComponent,
    StructComponent,
)
from aleph.model.enums import AttentionLevel, SymbolKind
from aleph.model.symbol import SymbolID, RawSymbol, Symbol


def _sym(name, sid_hex, called_by=None, source_file="test.py"):
    raw = RawSymbol(
        name=name, qualified_name=name, kind=SymbolKind.FUNCTION,
        scope="", span=MagicMock(), language="python",
        source_file=source_file, body_text="", signature_text="",
    )
    s = Symbol(id=SymbolID(prefix="f", hex_hash=sid_hex), raw=raw)
    s.called_by = called_by or []
    return s


def _result(source_file, symbols, call_edges=None):
    struct = StructComponent(
        source_file=source_file,
        call_edges=call_edges or [],
        symbols={str(s.id): s for s in symbols},
    )
    return {
        "source_file": source_file,
        "symbols": symbols,
        "struct_component": struct,
    }


class TestComputeProjectSalience:
    def test_empty_project(self):
        result = compute_project_salience("/root", {})
        assert result.entries == []
        assert result.root == "/root"

    def test_single_file_fan_in(self, tmp_path):
        root = str(tmp_path)
        f = str(tmp_path / "a.py")
        hub = _sym("hub", "aaa111", called_by=["f_bbb222", "f_ccc333"], source_file=f)
        leaf = _sym("leaf", "bbb222", called_by=[], source_file=f)

        salience = compute_project_salience(root, {f: _result(f, [hub, leaf])})

        scores = {e.symbol_id: e.score for e in salience.entries}
        assert scores["f_aaa111"] > scores["f_bbb222"]

    def test_cross_file_fan_in_weighted_higher(self, tmp_path):
        root = str(tmp_path)
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")

        # local_only: 3 local callers, 0 cross-file
        local_only = _sym("local_only", "loc111", called_by=["x", "y", "z"], source_file=f1)
        # cross_target: 1 local caller, 1 cross-file caller
        cross_target = _sym("cross_target", "crs111", called_by=["w"], source_file=f2)
        caller = _sym("caller", "cal111", called_by=[], source_file=f1)

        file_results = {
            f1: _result(f1, [local_only, caller],
                        call_edges=[("f_cal111", "f_crs111")]),
            f2: _result(f2, [cross_target]),
        }

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}

        # cross_target: 1 local + 2*1 cross = 3 raw
        # local_only: 3 local + 2*0 cross = 3 raw
        # They should be equal
        assert by_id["f_crs111"].cross_file_fan_in == 1
        assert by_id["f_loc111"].cross_file_fan_in == 0

    def test_scores_normalized_0_1(self, tmp_path):
        root = str(tmp_path)
        f = str(tmp_path / "a.py")
        syms = [
            _sym("hub", "aaa111", called_by=["x", "y", "z", "w"], source_file=f),
            _sym("mid", "bbb222", called_by=["x"], source_file=f),
            _sym("leaf", "ccc333", called_by=[], source_file=f),
        ]
        salience = compute_project_salience(root, {f: _result(f, syms)})

        for e in salience.entries:
            assert 0.0 <= e.score <= 1.0

        # Highest fan-in should be 1.0
        scores = {e.symbol_id: e.score for e in salience.entries}
        assert scores["f_aaa111"] == 1.0
        assert scores["f_ccc333"] == 0.0

    def test_entries_sorted_by_score_descending(self, tmp_path):
        root = str(tmp_path)
        f = str(tmp_path / "a.py")
        syms = [
            _sym("low", "aaa111", called_by=[], source_file=f),
            _sym("high", "bbb222", called_by=["x", "y", "z"], source_file=f),
            _sym("mid", "ccc333", called_by=["x"], source_file=f),
        ]
        salience = compute_project_salience(root, {f: _result(f, syms)})

        scores = [e.score for e in salience.entries]
        assert scores == sorted(scores, reverse=True)


class TestComputeAttentionBudget:
    def _make_salience(self, scores):
        from aleph.model.components import ProjectSalienceEntry
        entries = [
            ProjectSalienceEntry(
                symbol_id=f"f_{i:06x}", qualified_name=f"sym_{i}",
                file="test.py", score=s, local_fan_in=0,
                cross_file_fan_in=0, total_fan_in=0,
            )
            for i, s in enumerate(scores)
        ]
        return ProjectSalienceComponent(root="/root", entries=entries)

    def test_critical_threshold(self):
        salience = self._make_salience([0.9, 0.7, 0.69])
        attn = compute_attention_budget(salience)

        levels = [e.level for e in attn.entries]
        assert levels[0] == AttentionLevel.CRITICAL
        assert levels[1] == AttentionLevel.CRITICAL
        assert levels[2] != AttentionLevel.CRITICAL

    def test_important_threshold(self):
        salience = self._make_salience([0.5, 0.3, 0.29])
        attn = compute_attention_budget(salience)

        levels = [e.level for e in attn.entries]
        assert levels[0] == AttentionLevel.IMPORTANT
        assert levels[1] == AttentionLevel.IMPORTANT
        assert levels[2] != AttentionLevel.IMPORTANT

    def test_peripheral_threshold(self):
        salience = self._make_salience([0.1, 0.05, 0.04])
        attn = compute_attention_budget(salience)

        levels = [e.level for e in attn.entries]
        assert levels[0] == AttentionLevel.PERIPHERAL
        assert levels[1] == AttentionLevel.PERIPHERAL
        assert levels[2] == AttentionLevel.SKIP

    def test_budget_counts(self):
        salience = self._make_salience([0.9, 0.8, 0.5, 0.3, 0.1, 0.02, 0.0])
        attn = compute_attention_budget(salience)

        assert attn.budget["critical"] == 2
        assert attn.budget["important"] == 2
        assert attn.budget["peripheral"] == 1
        assert attn.budget["skip"] == 2

    def test_empty(self):
        salience = self._make_salience([])
        attn = compute_attention_budget(salience)
        assert attn.entries == []
        assert sum(attn.budget.values()) == 0

    def test_all_zero(self):
        salience = self._make_salience([0.0, 0.0, 0.0])
        attn = compute_attention_budget(salience)
        assert all(e.level == AttentionLevel.SKIP for e in attn.entries)


class TestSalienceTestFileDemotion:
    """Phase 2.8: test-file symbols should score below src symbols."""

    def test_test_file_symbol_scores_below_src(self, tmp_path):
        """Symbol in test file with high fan-in scores below symbol in src with moderate fan-in."""
        root = str(tmp_path)
        src_file = str(tmp_path / "src" / "core.py")
        test_file = str(tmp_path / "tests" / "test_core.py")

        # Test helper called by many (high local fan-in: 10)
        test_sym = _sym("_make_symbol", "tst111",
                        called_by=[f"x{i}" for i in range(10)],
                        source_file=test_file)
        # Core function with moderate fan-in (3)
        src_sym = _sym("run_pipeline", "src111",
                       called_by=["a", "b", "c"],
                       source_file=src_file)

        file_results = {
            src_file: _result(src_file, [src_sym]),
            test_file: _result(test_file, [test_sym]),
        }

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}
        # src symbol should rank higher despite lower raw fan-in
        assert by_id["f_src111"].score > by_id["f_tst111"].score

    def test_file_diversity_bonus(self, tmp_path):
        """Symbol called from 3 files scores above symbol called 3x from same file."""
        root = str(tmp_path)
        f1 = str(tmp_path / "a.py")
        f2 = str(tmp_path / "b.py")
        f3 = str(tmp_path / "c.py")
        f4 = str(tmp_path / "d.py")

        # diverse_target: called from 3 different files (1 local call each)
        diverse = _sym("diverse", "div111", called_by=["x"], source_file=f1)
        # local_target: called 3x from same file
        local = _sym("local_heavy", "loc111", called_by=["a", "b", "c"], source_file=f2)

        caller_b = _sym("caller_b", "cb_111", source_file=f2)
        caller_c = _sym("caller_c", "cc_111", source_file=f3)
        caller_d = _sym("caller_d", "cd_111", source_file=f4)

        file_results = {
            f1: _result(f1, [diverse]),
            f2: _result(f2, [local, caller_b],
                        call_edges=[("f_cb_111", "f_div111")]),
            f3: _result(f3, [caller_c],
                        call_edges=[("f_cc_111", "f_div111")]),
            f4: _result(f4, [caller_d],
                        call_edges=[("f_cd_111", "f_div111")]),
        }

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}
        # diverse_target gets cross-file fan-in + file diversity bonus
        assert by_id["f_div111"].score > by_id["f_loc111"].score

    def test_private_function_demoted(self, tmp_path):
        """Private _helper with same fan-in scores below public process."""
        root = str(tmp_path)
        f = str(tmp_path / "src" / "core.py")

        public = _sym("process", "pub111", called_by=["a", "b", "c"], source_file=f)
        private = _sym("_helper", "prv111", called_by=["x", "y", "z"], source_file=f)

        file_results = {f: _result(f, [public, private])}

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}
        # Same fan-in, but private gets 0.5x penalty
        assert by_id["f_pub111"].score > by_id["f_prv111"].score

    def test_dunder_not_penalized(self, tmp_path):
        """__init__ (dunder) should NOT be penalized as private."""
        root = str(tmp_path)
        f = str(tmp_path / "src" / "core.py")

        dunder = _sym("__init__", "dun111", called_by=["a", "b"], source_file=f)
        private = _sym("_helper", "prv111", called_by=["a", "b"], source_file=f)

        file_results = {f: _result(f, [dunder, private])}

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}
        assert by_id["f_dun111"].score > by_id["f_prv111"].score


class TestVendorDemotion:
    def test_is_vendor_file_detection(self):
        assert _is_vendor_file("vendor/taffy/src/lib.rs")
        assert _is_vendor_file("fastrender/vendor/ecma-rs/vm-js/src/heap.rs")
        assert _is_vendor_file("third_party/openssl/lib.rs")
        assert _is_vendor_file("external/deps/foo.py")
        assert not _is_vendor_file("src/core.rs")
        assert not _is_vendor_file("crates/hiwave-app/src/main.rs")
        assert not _is_vendor_file("tests/test_vendor_mock.py")

    def test_vendor_symbol_demoted(self, tmp_path):
        """Vendor symbol with same fan-in scores below src symbol."""
        root = str(tmp_path)
        src_file = str(tmp_path / "src" / "core.py")
        vendor_file = str(tmp_path / "vendor" / "lib" / "util.py")

        src_sym = _sym("process", "src111", called_by=["a", "b", "c"], source_file=src_file)
        vendor_sym = _sym("process", "vnd111", called_by=["a", "b", "c"], source_file=vendor_file)

        file_results = {
            src_file: _result(src_file, [src_sym]),
            vendor_file: _result(vendor_file, [vendor_sym]),
        }

        salience = compute_project_salience(root, file_results)
        by_id = {e.symbol_id: e for e in salience.entries}
        assert by_id["f_src111"].score > by_id["f_vnd111"].score


class TestAdaptiveThresholds:
    def test_large_project_gets_more_critical(self):
        """Projects > 1000 symbols should have more than 10 critical entries."""
        entries = []
        for i in range(2000):
            entries.append(ProjectSalienceEntry(
                symbol_id=f"f_{i:06x}",
                qualified_name=f"func_{i}",
                file=f"src/mod_{i // 50}.py",
                score=round(1.0 - (i / 2000), 4),
                local_fan_in=0,
                cross_file_fan_in=0,
                total_fan_in=0,
            ))
        salience = ProjectSalienceComponent(root="/root", entries=entries)
        attention = compute_attention_budget(salience)
        critical_count = attention.budget[AttentionLevel.CRITICAL.value]
        important_count = attention.budget[AttentionLevel.IMPORTANT.value]
        assert critical_count >= 10, f"Expected >= 10 critical, got {critical_count}"
        assert important_count >= 40, f"Expected >= 40 important, got {important_count}"

    def test_small_project_uses_fixed_thresholds(self):
        """Projects <= 1000 symbols use original fixed thresholds."""
        entries = [
            ProjectSalienceEntry(
                symbol_id="f_000001", qualified_name="high", file="src/a.py",
                score=0.8, local_fan_in=0, cross_file_fan_in=0, total_fan_in=0,
            ),
            ProjectSalienceEntry(
                symbol_id="f_000002", qualified_name="mid", file="src/b.py",
                score=0.4, local_fan_in=0, cross_file_fan_in=0, total_fan_in=0,
            ),
            ProjectSalienceEntry(
                symbol_id="f_000003", qualified_name="low", file="src/c.py",
                score=0.06, local_fan_in=0, cross_file_fan_in=0, total_fan_in=0,
            ),
        ]
        salience = ProjectSalienceComponent(root="/root", entries=entries)
        attention = compute_attention_budget(salience)
        levels = {e.symbol_id: e.level for e in attention.entries}
        assert levels["f_000001"] == AttentionLevel.CRITICAL
        assert levels["f_000002"] == AttentionLevel.IMPORTANT
        assert levels["f_000003"] == AttentionLevel.PERIPHERAL
