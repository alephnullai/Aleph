"""Tests for project indexer helpers."""

from aleph.project.indexer import build_index, query_symbols


def _fake_runner(path: str) -> dict:
    class _Raw:
        def __init__(self, name):
            self.name = name
            self.qualified_name = name
            self.kind = type("Kind", (), {"value": "f"})
            self.scope = ""
            self.signature_text = ""
            self.body_text = ""

    class _Sym:
        def __init__(self, sid, name):
            self.id = sid
            self.raw = _Raw(name)

    class _Struct:
        call_edges = [("f_111111", "f_222222")]

    return {
        "language": "python",
        "semantic_hash": "abc123def456",
        "symbols": [_Sym("f_111111", "alpha"), _Sym("f_222222", "beta")],
        "struct_component": _Struct(),
    }


def test_build_index_and_query(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    p = src / "sample.py"
    p.write_text("def alpha():\n    return 1\n")

    index, stats = build_index(str(src), _fake_runner, previous={})
    assert stats.indexed_files == 1
    assert index["files"]

    matches = query_symbols(index, "alpha")
    assert len(matches) == 1
    assert matches[0]["qualified_name"] == "alpha"
