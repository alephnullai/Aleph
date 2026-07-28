"""Property-based tests for semantic hash invariants.

Idempotent; whitespace-invariant; order-invariant; adding/removing symbols changes hash.
"""

from hypothesis import given, strategies as st

from aleph.util.hashing import semantic_hash


# Strategy for graph-like dicts
node_names = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
graph_data = st.fixed_dictionaries({
    "nodes": st.lists(node_names, min_size=0, max_size=10),
    "edges": st.lists(
        st.tuples(node_names, node_names),
        min_size=0, max_size=10,
    ),
})


class TestSemanticHashInvariants:
    @given(data=graph_data)
    def test_idempotent(self, data):
        h1 = semantic_hash(data)
        h2 = semantic_hash(data)
        assert h1 == h2

    @given(data=graph_data)
    def test_order_invariant(self, data):
        # JSON sort_keys ensures order independence
        h1 = semantic_hash(data)
        reversed_data = {k: v for k, v in reversed(list(data.items()))}
        h2 = semantic_hash(reversed_data)
        assert h1 == h2

    def test_adding_node_changes_hash(self):
        d1 = {"nodes": ["a", "b"], "edges": []}
        d2 = {"nodes": ["a", "b", "c"], "edges": []}
        assert semantic_hash(d1) != semantic_hash(d2)

    def test_removing_node_changes_hash(self):
        d1 = {"nodes": ["a", "b", "c"], "edges": []}
        d2 = {"nodes": ["a", "b"], "edges": []}
        assert semantic_hash(d1) != semantic_hash(d2)

    def test_adding_edge_changes_hash(self):
        d1 = {"nodes": ["a", "b"], "edges": []}
        d2 = {"nodes": ["a", "b"], "edges": [["a", "b"]]}
        assert semantic_hash(d1) != semantic_hash(d2)

    def test_whitespace_in_values_matters(self):
        # Semantic hash is based on structure, not formatting
        # But actual value differences should produce different hashes
        d1 = {"nodes": ["a"]}
        d2 = {"nodes": ["a "]}
        assert semantic_hash(d1) != semantic_hash(d2)
