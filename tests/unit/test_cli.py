"""Tests for CLI command surface."""

from __future__ import annotations

import json

from aleph import cli


def test_cli_compress_json(monkeypatch, capsys, cpp_simple_path):
    monkeypatch.setattr(
        "sys.argv",
        ["aleph", "compress", cpp_simple_path, "--json"],
    )
    cli.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["source_file"] == cpp_simple_path
    assert "bundle" in payload


def test_cli_index_and_query_json(monkeypatch, capsys, tmp_path):
    source = tmp_path / "example.py"
    source.write_text("def demo(x):\n    return x + 1\n")

    # Build project (produces dict, struct, index, etc.)
    monkeypatch.setattr(
        "sys.argv",
        ["aleph", "build", str(tmp_path), "-o", str(tmp_path), "--json"],
    )
    cli.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["total_files"] >= 1

    # Query using the new Phase 2.4 query interface
    monkeypatch.setattr(
        "sys.argv",
        ["aleph", "query", "SEARCH", "demo", "-d", str(tmp_path), "--json"],
    )
    cli.main()
    query_payload = json.loads(capsys.readouterr().out)
    assert len(query_payload["results"]) >= 1


def _build_demo_project(monkeypatch, capsys, tmp_path):
    source = tmp_path / "example.py"
    source.write_text(
        "def demo(x):\n"
        "    return helper(x) + 1\n"
        "\n"
        "def helper(x):\n"
        "    return x * 2\n"
    )
    monkeypatch.setattr("sys.argv", ["aleph", "build", str(tmp_path), "--json"])
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_cli_index_is_deprecated_alias_for_build(monkeypatch, capsys, tmp_path):
    (tmp_path / "example.py").write_text("def demo(x):\n    return x + 1\n")
    monkeypatch.setattr("sys.argv", ["aleph", "index", str(tmp_path), "--json"])
    cli.main()
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    payload = json.loads(captured.out)
    # Build output shape, not the legacy index output
    assert payload["total_files"] >= 1
    assert (tmp_path / ".aleph" / "project.aleph.dict").is_file()
    assert (tmp_path / ".aleph" / ".aleph.index.json").is_file()


def test_cli_resolve_uses_query_engine(monkeypatch, capsys, tmp_path):
    _build_demo_project(monkeypatch, capsys, tmp_path)

    monkeypatch.setattr(
        "sys.argv", ["aleph", "resolve", "demo", "-d", str(tmp_path), "--json"]
    )
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"] == "demo"
    assert len(payload["matches"]) == 1
    match = payload["matches"][0]
    assert match["qualified_name"] == "demo"
    assert match["file"] == "example.py"
    # Spans are recorded in the dictionary now (P2-3)
    assert match["start_line"] >= 1
    assert match["language"] == "python"


def test_cli_resolve_missing_artifacts_exits(monkeypatch, capsys, tmp_path):
    import pytest
    monkeypatch.setattr(
        "sys.argv", ["aleph", "resolve", "demo", "-d", str(tmp_path)]
    )
    with pytest.raises(SystemExit):
        cli.main()
    assert "aleph build" in capsys.readouterr().err


def test_cli_neighbors_uses_query_engine(monkeypatch, capsys, tmp_path):
    _build_demo_project(monkeypatch, capsys, tmp_path)

    monkeypatch.setattr(
        "sys.argv", ["aleph", "neighbors", "helper", "-d", str(tmp_path), "--json"]
    )
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    directions = {(n["direction"]) for n in payload["neighbors"]}
    # demo calls helper → helper has an inbound neighbor
    assert "in" in directions
