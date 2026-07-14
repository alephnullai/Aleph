"""Tests for the span-based, Python-scoped patch engine (P2-3).

Targets are located by the symbol's recorded span (file + line range in
the dictionary artifact), duplicate names are disambiguated by qualified
name, remaining ambiguity errors out listing candidates, and non-Python
targets get a clear unsupported-language error.
"""

from __future__ import annotations

import pytest

from aleph.patch.manager import PatchManager


@pytest.fixture
def span_project(tmp_path):
    """Project with duplicate method names in two classes + recorded spans."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "models.py").write_text(
        "class Alpha:\n"
        "    def run(self):\n"
        '        """Alpha run."""\n'
        "        return 1\n"
        "\n"
        "\n"
        "class Beta:\n"
        "    def run(self):\n"
        '        """Beta run."""\n'
        "        return 2\n"
    )
    (src_dir / "lib.rs").write_text(
        "fn do_thing() -> i32 {\n"
        "    42\n"
        "}\n"
    )

    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()
    (aleph_dir / "project.aleph.dict").write_text(
        "[ALEPH:DICT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[SYMBOLS]\n"
        "f_aaa111=Alpha::run file=src/models.py kind=m scope=Alpha sig=hashaaaa span=2-4 lang=python\n"
        "f_bbb222=Beta::run file=src/models.py kind=m scope=Beta sig=hashbbbb span=8-10 lang=python\n"
        "f_rust01=do_thing file=src/lib.rs kind=f scope=module sig=hashrust span=1-3 lang=rust\n"
        "[/SYMBOLS]\n"
    )
    (aleph_dir / "project.aleph.epistemic").write_text("{}")
    return tmp_path


class TestSpanLocatedApply:
    def test_duplicate_name_patches_correct_class_via_span(self, span_project):
        mgr = PatchManager(str(span_project))
        mgr.propose("f_bbb222", "guard against zero")
        result = mgr.apply("patch_1")
        assert result.success, result.message

        lines = (span_project / "src" / "models.py").read_text().splitlines()
        # The TODO must land inside Beta.run (after its docstring), not Alpha.run
        beta_doc = lines.index('        """Beta run."""')
        assert "TODO [patch_1]" in lines[beta_doc + 1]
        # Method-level indentation (8 spaces), not the hardcoded 4
        assert lines[beta_doc + 1].startswith("        # TODO")
        # Alpha.run untouched
        alpha_doc = lines.index('        """Alpha run."""')
        assert "TODO" not in lines[alpha_doc + 1]

    def test_first_class_patched_when_targeted(self, span_project):
        mgr = PatchManager(str(span_project))
        mgr.propose("f_aaa111", "alpha change")
        result = mgr.apply("patch_1")
        assert result.success

        text = (span_project / "src" / "models.py").read_text()
        alpha_part, beta_part = text.split("class Beta:")
        assert "TODO [patch_1]" in alpha_part
        assert "TODO" not in beta_part

    def test_qualified_name_disambiguates(self, span_project):
        """Proposing by qualified name resolves to exactly one symbol."""
        mgr = PatchManager(str(span_project))
        mgr.propose("Beta::run", "qualified target")
        result = mgr.apply("patch_1")
        assert result.success, result.message
        lines = (span_project / "src" / "models.py").read_text().splitlines()
        beta_doc = lines.index('        """Beta run."""')
        assert "TODO [patch_1]" in lines[beta_doc + 1]

    def test_ambiguous_bare_name_errors_with_candidates(self, span_project):
        mgr = PatchManager(str(span_project))
        mgr.propose("run", "which one?", file="src/models.py")
        result = mgr.apply("patch_1")
        assert not result.success
        assert "Ambiguous symbol 'run'" in result.message
        assert "f_aaa111" in result.message
        assert "f_bbb222" in result.message
        # File untouched
        assert "TODO" not in (span_project / "src" / "models.py").read_text()

    def test_stale_span_falls_back_to_unique_name_scan(self, span_project):
        """A stale (out-of-range) span still applies when the name is unique."""
        aleph_dir = span_project / ".aleph"
        dict_text = (aleph_dir / "project.aleph.dict").read_text()
        (aleph_dir / "project.aleph.dict").write_text(
            dict_text + ""  # keep as-is; add a uniquely-named symbol with bad span
        )
        (span_project / "src" / "solo.py").write_text(
            "def lonely():\n    return 'solo'\n"
        )
        (aleph_dir / "project.aleph.dict").write_text(
            "[ALEPH:DICT:1.0]\n"
            f"[ROOT:{span_project}]\n"
            "[SYMBOLS]\n"
            "f_solo01=lonely file=src/solo.py kind=f scope=module sig=hashsolo span=90-95 lang=python\n"
            "[/SYMBOLS]\n"
        )
        mgr = PatchManager(str(span_project))
        mgr.propose("f_solo01", "still findable")
        result = mgr.apply("patch_1")
        assert result.success, result.message
        assert "TODO [patch_1]" in (span_project / "src" / "solo.py").read_text()


class TestSpanPriorityOverFirstMatch:
    def test_span_beats_first_string_match(self, tmp_path):
        """Without spans the old engine patched the FIRST name match."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "svc.py").write_text(
            "def helper():\n"
            "    return 'module-level'\n"
            "\n"
            "\n"
            "class Service:\n"
            "    def helper(self):\n"
            "        return 'method'\n"
        )
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text(
            "[ALEPH:DICT:1.0]\n"
            f"[ROOT:{tmp_path}]\n"
            "[SYMBOLS]\n"
            "f_mod001=helper file=src/svc.py kind=f scope=module sig=hashmod1 span=1-2 lang=python\n"
            "f_svc001=Service::helper file=src/svc.py kind=m scope=Service sig=hashsvc1 span=6-7 lang=python\n"
            "[/SYMBOLS]\n"
        )
        mgr = PatchManager(str(tmp_path))
        mgr.propose("f_svc001", "patch the method")
        result = mgr.apply("patch_1")
        assert result.success, result.message

        lines = (tmp_path / "src" / "svc.py").read_text().splitlines()
        method_def = lines.index("    def helper(self):")
        assert "TODO [patch_1]" in lines[method_def + 1]
        assert lines[method_def + 1].startswith("        # TODO")
        # Module-level helper untouched
        assert "TODO" not in lines[1]


class TestLanguageGate:
    def test_non_python_target_rejected_with_clear_error(self, span_project):
        mgr = PatchManager(str(span_project))
        record = mgr.propose("f_rust01", "rust change")
        # Propose still records non-Python targets
        assert record.patch_id == "patch_1"
        assert record.file == "src/lib.rs"

        result = mgr.apply("patch_1")
        assert not result.success
        assert "supports Python only" in result.message
        assert "rust" in result.message
        # Patch stays pending, file untouched
        assert mgr.get_patch("patch_1").status == "pending"
        assert "TODO" not in (span_project / "src" / "lib.rs").read_text()

    def test_unknown_extension_rejected(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text("")
        (tmp_path / "config.toml").write_text("[x]\ny = 1\n")
        mgr = PatchManager(str(tmp_path))
        mgr.propose("f_cfg", "edit config", file="config.toml")
        result = mgr.apply("patch_1")
        assert not result.success
        assert "supports Python only" in result.message

    def test_python_extension_inferred_without_lang_attr(self, tmp_path):
        """Old artifacts without lang= still apply to .py files."""
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text(
            "[ALEPH:DICT:1.0]\n"
            f"[ROOT:{tmp_path}]\n"
            "[SYMBOLS]\n"
            "f_old001=legacy file=old.py kind=f scope=module sig=hashold\n"
            "[/SYMBOLS]\n"
        )
        (tmp_path / "old.py").write_text("def legacy():\n    return 0\n")
        mgr = PatchManager(str(tmp_path))
        mgr.propose("f_old001", "works without span/lang")
        result = mgr.apply("patch_1")
        assert result.success, result.message
        assert "TODO [patch_1]" in (tmp_path / "old.py").read_text()
