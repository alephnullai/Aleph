"""Tests for Aleph artifact loader/expansion."""

from aleph.emit.loader import AlephLoader
from aleph.emit.serializer import AlephSerializer
from aleph.model.components import BodiesComponent, BodyEntry
from aleph.model.enums import BodyLevel
from aleph.model.symbol import SymbolID


def test_loader_roundtrip_with_original_bodies():
    sid = SymbolID(prefix="f", hex_hash="abc123")
    component = BodiesComponent(
        source_file="x.cpp",
        symbol_dict={"f_abc123": "my_func"},
        entries=[
            BodyEntry(
                symbol_id=sid,
                level=BodyLevel.FULL,
                content="f_abc123();",
                original_body="my_func();",
            )
        ],
    )
    text = AlephSerializer().serialize_bodies(component, include_original_bodies=True)
    loaded = AlephLoader().deserialize_bodies(text)
    expanded = AlephLoader().expand_bodies(loaded)
    assert expanded["f_abc123"] == "my_func();"


def test_loader_expands_without_original_via_symbol_dict():
    entry = BodyEntry(
        symbol_id=SymbolID(prefix="f", hex_hash="abc123"),
        level=BodyLevel.FULL,
        content="f_def456();",
    )
    expanded = AlephLoader().expand_entry(entry, {"f_def456": "target"})
    assert expanded == "target();"
