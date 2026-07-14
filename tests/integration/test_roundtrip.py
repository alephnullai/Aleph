"""Integration test: compress -> expand -> diff (MUST be 100% lossless).

Roundtrip correctness is the most critical test in the entire suite.
Principle II (Lossless Fidelity) and V (Bidirectionality).
"""

import pytest
from aleph.cli import run_pipeline
from aleph.emit.loader import AlephLoader
from aleph.emit.serializer import AlephSerializer
from aleph.model.enums import BodyLevel


def expand_bodies_from_serialized(bodies_component) -> dict[str, str]:
    """Deserialize emitted bodies and expand by symbol id."""
    serializer = AlephSerializer()
    loader = AlephLoader()
    text = serializer.serialize_bodies(bodies_component, include_original_bodies=True)
    parsed = loader.deserialize_bodies(text)
    return loader.expand_bodies(parsed)


class TestRoundtrip:
    def test_roundtrip_cpp_simple(self, cpp_simple_path):
        """expand(serialize(compress(file))) preserves all original bodies."""
        result = run_pipeline(cpp_simple_path)
        bodies = result["bodies_component"]
        expanded = expand_bodies_from_serialized(bodies)

        for entry in bodies.entries:
            if entry.original_body:
                assert expanded[str(entry.symbol_id)] == entry.original_body

    def test_roundtrip_rust_simple(self, rust_simple_path):
        result = run_pipeline(rust_simple_path)
        bodies = result["bodies_component"]
        expanded = expand_bodies_from_serialized(bodies)

        for entry in bodies.entries:
            if entry.original_body:
                assert expanded[str(entry.symbol_id)] == entry.original_body

    def test_roundtrip_cpp_complex(self, cpp_complex_path):
        result = run_pipeline(cpp_complex_path)
        bodies = result["bodies_component"]
        expanded = expand_bodies_from_serialized(bodies)

        for entry in bodies.entries:
            if entry.original_body:
                assert expanded[str(entry.symbol_id)] == entry.original_body

    def test_roundtrip_rust_complex(self, rust_complex_path):
        result = run_pipeline(rust_complex_path)
        bodies = result["bodies_component"]
        expanded = expand_bodies_from_serialized(bodies)

        for entry in bodies.entries:
            if entry.original_body:
                assert expanded[str(entry.symbol_id)] == entry.original_body

    def test_original_bodies_match_source(self, cpp_simple_path):
        """Verify that stored original bodies actually match the source file."""
        with open(cpp_simple_path) as f:
            source = f.read()

        result = run_pipeline(cpp_simple_path)
        bodies = result["bodies_component"]

        for entry in bodies.entries:
            if entry.original_body:
                # The original body text should be a substring of the source
                assert entry.original_body in source, (
                    f"Original body of {entry.symbol_id} not found in source"
                )

    def test_symbol_dict_enables_expansion(self, cpp_simple_path):
        """The symbol dict in bodies component enables reversing symbol substitution."""
        result = run_pipeline(cpp_simple_path)
        bodies = result["bodies_component"]
        expanded = expand_bodies_from_serialized(bodies)

        # Every symbol ID referenced in entries should be in the dict
        for entry in bodies.entries:
            id_str = str(entry.symbol_id)
            assert id_str in bodies.symbol_dict or entry.level == BodyLevel.OMIT
            if entry.level == BodyLevel.FULL and entry.original_body:
                assert expanded[id_str] == entry.original_body