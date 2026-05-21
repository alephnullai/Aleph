"""Tests for salience scoring."""

from aleph.link.salience import SalienceScorer
from aleph.model.symbol import Symbol, RawSymbol, Span, SymbolID
from aleph.model.enums import SymbolKind


def make_symbol(name, hex_hash, called_by_count=0):
    raw = RawSymbol(
        name=name, qualified_name=name, kind=SymbolKind.FUNCTION, scope="",
        span=Span(0, 0, 10, 0), language="cpp",
    )
    sym = Symbol(id=SymbolID(prefix="f", hex_hash=hex_hash), raw=raw)
    # Simulate called_by
    for i in range(called_by_count):
        sym.called_by.append(SymbolID(prefix="f", hex_hash=f"caller{i}"))
    return sym


class TestSalienceScorer:
    def test_more_called_higher_score(self):
        scorer = SalienceScorer()
        hub = make_symbol("hub", "aaa111", called_by_count=10)
        leaf = make_symbol("leaf", "bbb222", called_by_count=1)
        scores = scorer.score([hub, leaf])
        assert scores[str(hub.id)] > scores[str(leaf.id)]

    def test_scores_in_range(self):
        scorer = SalienceScorer()
        syms = [
            make_symbol("a", "aaa111", called_by_count=5),
            make_symbol("b", "bbb222", called_by_count=2),
            make_symbol("c", "ccc333", called_by_count=0),
        ]
        scores = scorer.score(syms)
        for score in scores.values():
            assert 0.0 <= score <= 1.0

    def test_hub_is_top(self):
        scorer = SalienceScorer()
        hub = make_symbol("hub", "aaa111", called_by_count=20)
        others = [make_symbol(f"f{i}", f"xxx{i:03d}", called_by_count=i) for i in range(5)]
        all_syms = [hub] + others
        scores = scorer.score(all_syms)
        assert scores[str(hub.id)] == 1.0

    def test_empty_symbols(self):
        scorer = SalienceScorer()
        assert scorer.score([]) == {}

    def test_all_zero_fan_in(self):
        scorer = SalienceScorer()
        syms = [make_symbol("a", "aaa111"), make_symbol("b", "bbb222")]
        scores = scorer.score(syms)
        assert all(s == 0.0 for s in scores.values())
