"""Integration test: 50+ file roundtrip corpus validation."""

import os
import pytest
from pathlib import Path

from aleph.cli import run_pipeline
from aleph.emit.serializer import AlephSerializer
from aleph.emit.loader import AlephLoader


FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
ALEPH_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "src", "aleph")
TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)))


def _collect_corpus():
    """Build the 50+ file corpus."""
    files = []

    # 1. Test fixtures
    for lang_dir in ["cpp", "rust", "python"]:
        d = os.path.join(FIXTURES_DIR, lang_dir)
        if os.path.isdir(d):
            for f in os.listdir(d):
                full = os.path.join(d, f)
                if os.path.isfile(full):
                    files.append(full)

    # 2. Aleph's own source
    for root, dirs, filenames in os.walk(ALEPH_SRC):
        for f in filenames:
            if f.endswith(".py"):
                files.append(os.path.join(root, f))

    # 3. Aleph's own test files (Python)
    for root, dirs, filenames in os.walk(TESTS_DIR):
        for f in filenames:
            if f.endswith(".py") and f.startswith("test_"):
                files.append(os.path.join(root, f))

    return sorted(set(files))


CORPUS = _collect_corpus()


def test_corpus_size():
    """Verify we have 50+ files in the corpus."""
    assert len(CORPUS) >= 50, f"Corpus has only {len(CORPUS)} files, need 50+"


@pytest.mark.parametrize("source_path", CORPUS[:60], ids=lambda p: os.path.relpath(p))
def test_pipeline_roundtrip(source_path):
    """Run pipeline and verify basic roundtrip properties."""
    result = run_pipeline(source_path)

    # Pipeline should not crash
    assert result["symbols_extracted"] >= 0
    assert result["semantic_hash"]

    # Struct and bodies should serialize
    assert len(result["struct_text"]) > 0
    assert len(result["bodies_text"]) > 0


@pytest.mark.parametrize("source_path", CORPUS[:60], ids=lambda p: os.path.relpath(p))
def test_bodies_roundtrip(source_path):
    """Bodies: serialize → deserialize preserves entries."""
    result = run_pipeline(source_path)
    serializer = AlephSerializer()
    loader = AlephLoader()

    # Serialize with original bodies
    bodies_text = serializer.serialize_bodies(
        result["bodies_component"], include_original_bodies=True
    )

    # Deserialize
    roundtripped = loader.deserialize_bodies(bodies_text)
    assert len(roundtripped.entries) == len(result["bodies_component"].entries)

    # Expand and compare
    original_expanded = loader.expand_bodies(result["bodies_component"])
    roundtripped_expanded = loader.expand_bodies(roundtripped)
    for sid, original_body in original_expanded.items():
        if original_body:  # Only check non-empty bodies
            assert sid in roundtripped_expanded, f"Missing {sid} in roundtrip"
            assert roundtripped_expanded[sid] == original_body, (
                f"Body mismatch for {sid}"
            )


@pytest.mark.parametrize("source_path", CORPUS[:60], ids=lambda p: os.path.relpath(p))
def test_component_roundtrip(source_path):
    """All component types: serialize → deserialize preserves data."""
    result = run_pipeline(source_path)
    serializer = AlephSerializer()
    loader = AlephLoader()

    # Temporal
    temporal = result["temporal_component"]
    temporal_text = serializer.serialize_temporal(temporal)
    temporal_rt = loader.deserialize_temporal(temporal_text)
    assert len(temporal_rt.entries) == len(temporal.entries)
    assert temporal_rt.source_file == temporal.source_file

    # Intents
    intents = result["intents_component"]
    intents_text = serializer.serialize_intents(intents)
    intents_rt = loader.deserialize_intents(intents_text)
    assert len(intents_rt.entries) == len(intents.entries)

    # Errors
    errors = result["errors_component"]
    errors_text = serializer.serialize_errors(errors)
    errors_rt = loader.deserialize_errors(errors_text)
    assert len(errors_rt.sources) == len(errors.sources)
    assert len(errors_rt.boundaries) == len(errors.boundaries)
    assert len(errors_rt.unhandled) == len(errors.unhandled)

    # Tests
    tests = result["tests_component"]
    tests_text = serializer.serialize_tests(tests)
    tests_rt = loader.deserialize_tests(tests_text)
    assert len(tests_rt.coverage) == len(tests.coverage)
    assert len(tests_rt.test_details) == len(tests.test_details)
