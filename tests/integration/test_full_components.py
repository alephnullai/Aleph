"""Integration test: all 5 components produced for every fixture."""

import os
import pytest
from pathlib import Path

from aleph.cli import run_pipeline

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")


def _all_fixtures():
    """Collect all fixture files."""
    fixtures = []
    for ext_dir in ["cpp", "rust", "python"]:
        d = os.path.join(FIXTURES_DIR, ext_dir)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            full = os.path.join(d, f)
            if os.path.isfile(full):
                fixtures.append(full)
    return fixtures


@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: os.path.basename(p))
def test_all_components_produced(fixture_path):
    """Every fixture should produce all 5 component types."""
    result = run_pipeline(fixture_path)

    # Core components (always present)
    assert result["struct_component"] is not None
    assert result["bodies_component"] is not None

    # Phase 1 components
    assert result["temporal_component"] is not None
    assert result["intents_component"] is not None
    assert result["errors_component"] is not None
    assert result["tests_component"] is not None

    # Struct must have symbols
    assert result["symbols_extracted"] > 0

    # Temporal must have entries for each symbol
    sym_count = len(result["symbols"])
    temporal_count = len(result["temporal_component"].entries)
    assert temporal_count == sym_count, (
        f"Temporal entries ({temporal_count}) != symbols ({sym_count})"
    )

    # Tests component: coverage entries for non-test symbols
    tests_comp = result["tests_component"]
    assert tests_comp.source_file == fixture_path


@pytest.mark.parametrize("fixture_path", _all_fixtures(), ids=lambda p: os.path.basename(p))
def test_components_write_to_disk(fixture_path, tmp_path):
    """All component files should be written when output dir specified."""
    result = run_pipeline(fixture_path, output_dir=str(tmp_path))
    base = os.path.basename(fixture_path)

    expected_suffixes = [".aleph.struct", ".aleph.bodies", ".aleph.temporal",
                         ".aleph.intents", ".aleph.errors", ".aleph.tests"]
    for suffix in expected_suffixes:
        path = tmp_path / (base + suffix)
        assert path.exists(), f"Missing {suffix} for {base}"
        content = path.read_text()
        assert len(content) > 0, f"Empty {suffix} for {base}"
