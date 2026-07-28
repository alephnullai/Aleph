"""Integration tests for the semantic index with the REAL embedding model.

Skipped entirely unless the optional fastembed extra is installed
(pip install 'aleph-compiler[semantic]'). First run downloads the
BAAI/bge-small-en-v1.5 ONNX model (~130 MB) if not already cached.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

fastembed = pytest.importorskip("fastembed")

from aleph.pipeline import auto_build
from aleph.query.engine import QueryEngine
from aleph.query.semantic import is_available

pytestmark = pytest.mark.skipif(
    not is_available(), reason="fastembed not installed"
)


_AUTH_SRC = '''\
def authenticate_user(password):
    """Check the supplied secret against the saved hash."""
    return bool(password)
'''

_CHART_SRC = '''\
def render_chart(data):
    """Draw a bar graph from the numbers."""
    return list(data)
'''

_CONFIG_SRC = '''\
def parse_settings(text):
    """Read the ini file into a dict."""
    return dict(line.split("=") for line in text.splitlines() if "=" in line)
'''


@pytest.fixture(scope="module")
def semantic_project(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("semproj"))
    with open(os.path.join(root, "auth.py"), "w") as f:
        f.write(_AUTH_SRC)
    with open(os.path.join(root, "charts.py"), "w") as f:
        f.write(_CHART_SRC)
    with open(os.path.join(root, "config.py"), "w") as f:
        f.write(_CONFIG_SRC)
    auto_build(root, semantic=True)
    return root


class TestRealSemanticIndex:
    def test_embeddings_written_with_real_model(self, semantic_project):
        db = os.path.join(semantic_project, ".aleph", "aleph.db")
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT model, dim, length(vector) FROM embeddings"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 3
        for model, dim, nbytes in rows:
            assert model == "BAAI/bge-small-en-v1.5"
            assert dim == 384
            assert nbytes == 384 * 4

    def test_nl_query_finds_symbol_lexical_misses(self, semantic_project):
        """The acceptance fixture: a natural-language query with ZERO
        identifier-subtoken overlap finds the right symbol semantically."""
        query = "how are login credentials verified"

        # Lexical alone: no token of the query appears in any symbol
        # name or file path component -> no results.
        lexical_engine = QueryEngine(semantic_project)
        lexical_engine._semantic_loaded = True  # force lexical-only
        assert lexical_engine.search(query) == []

        engine = QueryEngine(semantic_project)
        results = engine.search(query)
        assert results, "semantic index should surface a match"
        names = [r.qualified_name for r in results]
        assert "authenticate_user" in names
        # ...and ranked above the unrelated distractors
        target_rank = names.index("authenticate_user")
        for distractor in ("render_chart", "parse_settings"):
            if distractor in names:
                assert target_rank < names.index(distractor)
        assert results[target_rank].match in ("semantic", "hybrid")

    def test_identifier_query_still_pure_lexical(self, semantic_project):
        engine = QueryEngine(semantic_project)
        results = engine.search("authenticate_user")
        assert results[0].qualified_name == "authenticate_user"
        assert results[0].match == "exact"

    def test_semantic_status_ok(self, semantic_project):
        engine = QueryEngine(semantic_project)
        assert engine.semantic_status() == "ok"
