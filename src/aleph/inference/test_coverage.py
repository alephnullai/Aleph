"""Static test coverage mapping: symbol ↔ test bidirectional mapping."""

from __future__ import annotations

import os

from aleph.model.symbol import Symbol
from aleph.model.components import TestsComponent, CoverageEntry, TestDetail


class TestCoverageMapper:
    """Map symbols to tests and tests to symbols using static analysis."""

    def map(
        self,
        symbols: list[Symbol],
        call_edges: list[tuple[str, str]],
        language: str,
        source_file: str,
    ) -> TestsComponent:
        """Build bidirectional test coverage mapping."""
        by_id = {str(s.id): s for s in symbols}

        # Identify test functions
        test_syms = self._identify_tests(symbols, language, source_file)
        test_ids = {str(s.id) for s in test_syms}

        # Build call graph reachability from each test
        # adjacency: caller → [callees]
        adjacency: dict[str, list[str]] = {}
        for caller, callee in call_edges:
            adjacency.setdefault(caller, []).append(callee)

        # For each test, find all reachable symbols
        test_details: list[TestDetail] = []
        covered_by: dict[str, list[str]] = {}  # symbol_id → [test_ids]

        for test_sym in test_syms:
            tid = str(test_sym.id)
            reachable = self._reachable(tid, adjacency, test_ids)
            behaviors = self._infer_behaviors(test_sym.raw.name)

            test_details.append(TestDetail(
                test_id=test_sym.id,
                covers=sorted(reachable),
                behaviors=behaviors,
            ))

            for sym_id in reachable:
                covered_by.setdefault(sym_id, []).append(tid)

        # Build coverage entries for non-test symbols
        coverage: list[CoverageEntry] = []
        for sym in symbols:
            sid = str(sym.id)
            if sid in test_ids:
                continue
            tests_covering = covered_by.get(sid, [])
            if tests_covering:
                # Check for partial coverage (type with some methods uncovered)
                uncovered = self._find_uncovered_children(sym, covered_by, by_id)
                status = "partial" if uncovered else "covered"
                sym.coverage = status
                coverage.append(CoverageEntry(
                    symbol_id=sym.id,
                    status=status,
                    test_ids=sorted(tests_covering),
                    uncovered=uncovered,
                ))
            else:
                sym.coverage = "none"
                coverage.append(CoverageEntry(
                    symbol_id=sym.id,
                    status="none",
                    test_ids=[],
                    uncovered=[],
                ))

        return TestsComponent(
            source_file=source_file,
            coverage=coverage,
            test_details=test_details,
        )

    def _identify_tests(
        self, symbols: list[Symbol], language: str, source_file: str
    ) -> list[Symbol]:
        """Identify test functions by naming convention."""
        tests: list[Symbol] = []
        basename = os.path.basename(source_file)

        for sym in symbols:
            if sym.raw.kind.value != "f":
                continue
            name = sym.raw.name

            if language == "python":
                if name.startswith("test_") or name.startswith("test"):
                    tests.append(sym)
            elif language == "rust":
                # Functions with #[test] attribute or in a tests module
                if name.startswith("test_") or "tests::" in sym.raw.qualified_name:
                    tests.append(sym)
            elif language == "cpp":
                # Google Test: TEST, TEST_F, TEST_P macros create functions
                if name.startswith("TEST") or name.startswith("test_"):
                    tests.append(sym)

        return tests

    def _reachable(
        self, start: str, adjacency: dict[str, list[str]], test_ids: set[str]
    ) -> list[str]:
        """BFS from a test function to find all reachable non-test symbols."""
        visited: set[str] = set()
        queue = [start]
        reachable: list[str] = []

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current != start and current not in test_ids:
                reachable.append(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return reachable

    @staticmethod
    def _infer_behaviors(test_name: str) -> list[str]:
        """Infer test behaviors from the test function name."""
        # Strip test_ prefix
        name = test_name
        for prefix in ("test_", "test"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        # Split on underscores, filter empty
        parts = [p for p in name.split("_") if p]
        return parts if parts else [test_name]

    def _find_uncovered_children(
        self,
        sym: Symbol,
        covered_by: dict[str, list[str]],
        by_id: dict[str, Symbol],
    ) -> list[str]:
        """For a type symbol, find uncovered child methods."""
        if sym.raw.kind.value != "t":
            return []
        uncovered: list[str] = []
        for child_id in sym.children:
            cid = str(child_id)
            child_sym = by_id.get(cid)
            if child_sym and child_sym.raw.kind.value == "f" and cid not in covered_by:
                uncovered.append(child_sym.raw.name)
        return uncovered
