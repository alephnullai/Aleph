"""P1-A: identifier-aware lexical search relevance + brief relevance floor."""

from __future__ import annotations

import pytest

from aleph.emit.file_components import FileComponentWriter
from aleph.mcp.handlers import AlephHandlers
from aleph.model.components import (
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectStructComponent,
    ProjectSalienceComponent, ProjectSalienceEntry,
)
from aleph.query.engine import QueryEngine, tokenize_identifier


# ── Tokenizer ──


class TestTokenizeIdentifier:
    def test_snake_case(self):
        assert tokenize_identifier("load_build_cache") == ["load", "build", "cache"]

    def test_camel_case(self):
        assert tokenize_identifier("parseHttpResponse") == ["parse", "http", "response"]

    def test_acronym_run(self):
        assert tokenize_identifier("parseHTTPResponse") == ["parse", "http", "response"]

    def test_trailing_acronym(self):
        assert tokenize_identifier("toJSON") == ["to", "json"]

    def test_acronym_then_word(self):
        assert tokenize_identifier("XMLHttpRequest") == ["xml", "http", "request"]

    def test_digit_boundaries(self):
        assert tokenize_identifier("utf8Decode") == ["utf", "8", "decode"]
        assert tokenize_identifier("sha256_hash") == ["sha", "256", "hash"]

    def test_path_components(self):
        assert tokenize_identifier("src/aleph/query/engine.py") == [
            "src", "aleph", "query", "engine", "py",
        ]

    def test_qualified_name_separators(self):
        assert tokenize_identifier("mod::Type.method") == ["mod", "type", "method"]

    def test_empty_and_punctuation(self):
        assert tokenize_identifier("") == []
        assert tokenize_identifier("--- !!") == []


# ── Ranking ──


def _sym(sid: str, name: str, qname: str = "", file: str = "src/main.py") -> ProjectSymbolEntry:
    return ProjectSymbolEntry(
        symbol_id=sid, name=name, qualified_name=qname or name,
        kind="f", scope="mod", file=file,
    )


def _write_project(tmp_path, symbols, salience=None):
    d = str(tmp_path)
    writer = FileComponentWriter(d)
    writer.write_project_dict(ProjectDictComponent(root=d, symbols=symbols))
    writer.write_project_struct(ProjectStructComponent(root=d, cross_refs=[], file_deps=[]))
    writer.write_project_salience(
        ProjectSalienceComponent(root=d, entries=salience or [])
    )
    return d


class TestSearchRanking:
    def test_exact_beats_prefix_beats_subtoken(self, tmp_path):
        d = _write_project(tmp_path, [
            _sym("f_aaa001", "parse"),                # exact
            _sym("f_aaa002", "parse_config"),         # prefix
            _sym("f_aaa003", "configFileParseHelper"),  # subtoken only
        ])
        engine = QueryEngine(d)
        results = engine.search("parse")
        by_id = {r.symbol_id: r for r in results}
        assert by_id["f_aaa001"].score > by_id["f_aaa002"].score
        assert by_id["f_aaa002"].score > by_id["f_aaa003"].score
        assert results[0].symbol_id == "f_aaa001"

    def test_rare_token_outranks_common(self, tmp_path):
        # "cache" appears in many symbols, "validator" in exactly one.
        symbols = [
            _sym(f"f_cc{i:04x}", f"cache_thing_{i}") for i in range(20)
        ]
        symbols.append(_sym("f_rare01", "license_validator"))
        symbols.append(_sym("f_comm01", "cache_warmup"))
        d = _write_project(tmp_path, symbols)
        engine = QueryEngine(d)
        results = engine.search("validator cache")
        by_id = {r.symbol_id: r for r in results}
        # The symbol matching the rare token must outrank one that only
        # matches the common token (which may be dropped as noise entirely).
        assert "f_rare01" in by_id
        if "f_comm01" in by_id:
            assert by_id["f_rare01"].score > by_id["f_comm01"].score
        assert results[0].symbol_id == "f_rare01"

    def test_path_components_match(self, tmp_path):
        d = _write_project(tmp_path, [
            _sym("f_eng001", "run", file="src/aleph/query/engine.py"),
            _sym("f_oth001", "run", file="src/aleph/emit/writer.py"),
        ])
        engine = QueryEngine(d)
        results = engine.search("query run")
        # Both match "run" exactly is impossible (dedup is by qname+kind);
        # use ids: engine.py symbol gets the path-token boost.
        scores = {r.symbol_id: r.score for r in results}
        assert "f_eng001" in scores

    def test_camel_snake_cross_match(self, tmp_path):
        d = _write_project(tmp_path, [
            _sym("f_abc001", "loadBuildCache"),
        ])
        engine = QueryEngine(d)
        results = engine.search("build cache")
        assert results and results[0].symbol_id == "f_abc001"
        assert results[0].match == "subtoken"

    def test_subtoken_noise_floor(self, tmp_path):
        """Matching one common token of a long query is not a result."""
        symbols = [_sym(f"f_aa{i:04x}", f"data_item_{i}") for i in range(30)]
        d = _write_project(tmp_path, symbols)
        engine = QueryEngine(d)
        # "data" is extremely common; the other three tokens match nothing
        results = engine.search("quantum flux capacitor data")
        assert all(r.score < 0.4 for r in results)

    def test_no_match_returns_empty(self, tmp_path):
        d = _write_project(tmp_path, [_sym("f_abc001", "hello")])
        engine = QueryEngine(d)
        assert engine.search("zzqqxx") == []

    def test_match_type_exposed(self, tmp_path):
        d = _write_project(tmp_path, [_sym("f_abc001", "hello_world")])
        engine = QueryEngine(d)
        assert engine.search("hello_world")[0].match == "exact"
        assert engine.search("hello_w")[0].match == "prefix"


# ── Brief relevance floor ──


@pytest.fixture
def brief_project(tmp_path):
    """Project with one well-connected symbol and one zero-fan-in symbol."""
    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()
    d = str(aleph_dir)
    writer = FileComponentWriter(d)
    writer.write_project_dict(ProjectDictComponent(root=d, symbols=[
        _sym("f_main01", "main_dispatch", file="src/main.py"),
        # Zero fan-in; only matchable via raw substring of its name
        _sym("f_leaf01", "xanaduqwertyhelper", file="src/leaf.py"),
    ]))
    writer.write_project_struct(ProjectStructComponent(root=d, cross_refs=[], file_deps=[]))
    writer.write_project_salience(ProjectSalienceComponent(root=d, entries=[
        ProjectSalienceEntry(
            symbol_id="f_main01", qualified_name="main_dispatch",
            file="src/main.py", score=0.9, local_fan_in=3,
            cross_file_fan_in=1, total_fan_in=4,
        ),
    ]))
    return str(tmp_path)


class TestBriefRelevanceFloor:
    def test_junk_query_says_no_confident_match(self, brief_project):
        h = AlephHandlers(project_dir=brief_project)
        result = h.handle_brief("investigate the qqzz flux capacitor anomaly")
        assert "No symbols matched" in result
        assert "enough confidence" in result
        # Must not pad with unrelated symbols
        assert "f_main01" not in result
        assert "f_leaf01" not in result
        # Suggests better strategies
        assert "aleph_search" in result or "aleph_map" in result

    def test_substring_only_zero_fanin_dropped(self, brief_project):
        h = AlephHandlers(project_dir=brief_project)
        # "aduqwert" only matches the middle of xanaduqwertyhelper (substring),
        # which nothing calls — should be dropped, not briefed.
        result = h.handle_brief("aduqwert")
        assert "No symbols matched" in result
        assert "f_leaf01" not in result

    def test_good_match_not_padded(self, brief_project):
        h = AlephHandlers(project_dir=brief_project)
        result = h.handle_brief("main_dispatch")
        assert "f_main01" in result
        # The irrelevant leaf symbol must not be padded in
        assert "f_leaf01" not in result
        assert "[RELEVANT SYMBOLS] (1 of 1" in result

    def test_next_steps_without_bodies_recommends_context(self, brief_project):
        h = AlephHandlers(project_dir=brief_project)
        result = h.handle_brief("main_dispatch")
        assert "[NEXT STEPS]" in result
        assert "ALEPH:EXPAND" not in result
        assert "ALEPH:CONTEXT f_main01" in result
        assert "ALEPH:IMPACT f_main01" in result

    def test_next_steps_with_bodies_recommends_expand(self, brief_project):
        # Write a bodies artifact for src/main.py containing the symbol
        from aleph.model.components import BodiesComponent, BodyEntry
        from aleph.model.enums import BodyLevel
        from aleph.model.symbol import SymbolID
        import os
        writer = FileComponentWriter(os.path.join(brief_project, ".aleph"))
        writer.write_bodies(BodiesComponent(
            source_file="src/main.py",
            entries=[BodyEntry(
                symbol_id=SymbolID("f", "main01"),
                level=BodyLevel.FULL,
                content="def main_dispatch():\n    pass",
                original_body="def main_dispatch():\n    pass",
            )],
            symbol_dict={"f_main01": "main_dispatch"},
        ), include_original_bodies=True)

        h = AlephHandlers(project_dir=brief_project)
        result = h.handle_brief("main_dispatch")
        assert "ALEPH:EXPAND f_main01" in result


# ── Inflection-aware subtoken matching ──


class TestInflectionMatching:
    """Natural-language inflections must match identifier subtokens.

    Regression source: brief("detect racy mtimes ...") found nothing for
    _is_racy_mtime even though search("racy mtime") ranked it first —
    plural task words never matched singular identifier subtokens.
    """

    def test_plural_query_matches_singular_identifier(self, tmp_path):
        d = _write_project(tmp_path, [_sym("f_racy01", "is_racy_mtime")])
        engine = QueryEngine(d)
        results = engine.search("racy mtimes")
        assert results and results[0].symbol_id == "f_racy01"

    def test_singular_query_matches_plural_identifier(self, tmp_path):
        d = _write_project(tmp_path, [_sym("f_path01", "resolve_paths")])
        engine = QueryEngine(d)
        results = engine.search("resolve path")
        assert results and results[0].symbol_id == "f_path01"

    def test_ies_plural_matches_y_identifier(self, tmp_path):
        d = _write_project(tmp_path, [_sym("f_qry001", "run_query_batch")])
        engine = QueryEngine(d)
        results = engine.search("run queries batch")
        assert results and results[0].symbol_id == "f_qry001"

    def test_inflected_token_weighted_by_indexed_variant(self, tmp_path):
        """An inflected query word must not be priced as out-of-vocabulary.

        "paths" unseen + "path" common: the token's IDF must come from the
        indexed variant, otherwise one inflected word gets the unseen-token
        maximum weight and pushes every candidate below the noise floor.
        """
        symbols = [_sym(f"f_pp{i:04x}", f"path_helper_{i}") for i in range(10)]
        symbols.append(_sym("f_norm01", "normalize_path_case"))
        d = _write_project(tmp_path, symbols)
        engine = QueryEngine(d)
        engine.search("x")  # force index build
        assert engine._variant_idf(engine._token_variants("paths")) == (
            engine._idf("path")
        )

    def test_oov_words_still_suppress_common_matches(self, tmp_path):
        """Inflection handling must not weaken the OOV noise floor."""
        symbols = [_sym(f"f_aa{i:04x}", f"data_item_{i}") for i in range(30)]
        d = _write_project(tmp_path, symbols)
        engine = QueryEngine(d)
        results = engine.search("quantum flux capacitor data")
        assert all(r.score < 0.4 for r in results)


# ── Brief excludes import directives ──


def _dsym(sid: str, name: str, file: str = "src/main.py") -> ProjectSymbolEntry:
    return ProjectSymbolEntry(
        symbol_id=sid, name=name, qualified_name=name,
        kind="d", scope="mod", file=file,
    )


class TestBriefExcludesDirectives:
    def test_import_directive_not_briefed(self, tmp_path):
        """Import directives token-match almost anything; never brief them.

        Regression source: brief("detect racy mtimes in the file stamp
        cache") returned `from aleph.project.cache import FileStamp, ...`
        (kind=d) above every real symbol — and multi-line import names
        corrupt the line-oriented brief output.
        """
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        d = str(aleph_dir)
        writer = FileComponentWriter(d)
        writer.write_project_dict(ProjectDictComponent(root=d, symbols=[
            _sym("f_imp001", "import_config", file="src/config.py"),
            _dsym("d_dir001", "from config import import_config, load_config"),
        ]))
        writer.write_project_struct(
            ProjectStructComponent(root=d, cross_refs=[], file_deps=[])
        )
        writer.write_project_salience(
            ProjectSalienceComponent(root=d, entries=[])
        )
        h = AlephHandlers(project_dir=str(tmp_path))
        result = h.handle_brief("import config")
        assert "f_imp001" in result
        assert "d_dir001" not in result


# ── Brief blends salience over the full confident pool ──


class TestBriefBlendPool:
    def test_high_salience_symbol_beyond_lexical_cut_is_briefed(self, tmp_path):
        """The salience blend must see every confident match.

        Regression source: handle_brief sliced candidates to
        max_symbols*3 in pure lexical order before blending, so a
        high-salience implementation symbol behind a wall of equal-score
        zero-salience matches could never be briefed.
        """
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        d = str(aleph_dir)
        # 16 zero-salience decoys that tie lexically and sort first,
        # then the high-salience target (rank 17 > 5*3 lexical cut).
        decoys = [
            _sym(f"f_dec{i:03x}", f"build_cache_helper_{i:02d}")
            for i in range(16)
        ]
        target = _sym("f_core01", "zzz_build_cache_core")
        writer = FileComponentWriter(d)
        writer.write_project_dict(
            ProjectDictComponent(root=d, symbols=decoys + [target])
        )
        writer.write_project_struct(
            ProjectStructComponent(root=d, cross_refs=[], file_deps=[])
        )
        writer.write_project_salience(ProjectSalienceComponent(root=d, entries=[
            ProjectSalienceEntry(
                symbol_id="f_core01", qualified_name="zzz_build_cache_core",
                file="src/main.py", score=0.9, local_fan_in=3,
                cross_file_fan_in=1, total_fan_in=4,
            ),
        ]))
        h = AlephHandlers(project_dir=str(tmp_path))
        result = h.handle_brief("build cache", max_symbols=5)
        assert "f_core01" in result
