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
