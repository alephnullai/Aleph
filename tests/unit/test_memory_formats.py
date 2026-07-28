"""Tests for aleph.memory.formats — format constants."""

from aleph.memory.formats import (
    MEMORY_HEADER,
    DECISIONS_TAG,
    DECISIONS_END,
    CONCLUSIONS_TAG,
    CONCLUSIONS_END,
    OPEN_QUESTIONS_TAG,
    OPEN_QUESTIONS_END,
    CODE_CHANGES_TAG,
    CODE_CHANGES_END,
    ERRORS_TAG,
    ERRORS_END,
    CONTEXT_TAG,
    CONTEXT_END,
    DICT_TAG,
    DICT_END,
    STATS_TAG,
    STATS_END,
)


class TestFormatConstants:
    def test_memory_header_format(self):
        assert MEMORY_HEADER == "[ALEPH:MEMORY:1.0]"

    def test_tags_are_paired(self):
        pairs = [
            (DECISIONS_TAG, DECISIONS_END),
            (CONCLUSIONS_TAG, CONCLUSIONS_END),
            (OPEN_QUESTIONS_TAG, OPEN_QUESTIONS_END),
            (CODE_CHANGES_TAG, CODE_CHANGES_END),
            (ERRORS_TAG, ERRORS_END),
            (CONTEXT_TAG, CONTEXT_END),
            (DICT_TAG, DICT_END),
            (STATS_TAG, STATS_END),
        ]
        for open_tag, close_tag in pairs:
            assert open_tag.startswith("[")
            assert close_tag.startswith("[/")
            # Close tag should contain the name from open tag
            name = open_tag[1:-1]
            assert name in close_tag

    def test_tags_are_unique(self):
        all_tags = [
            DECISIONS_TAG, DECISIONS_END,
            CONCLUSIONS_TAG, CONCLUSIONS_END,
            OPEN_QUESTIONS_TAG, OPEN_QUESTIONS_END,
            CODE_CHANGES_TAG, CODE_CHANGES_END,
            ERRORS_TAG, ERRORS_END,
            CONTEXT_TAG, CONTEXT_END,
            DICT_TAG, DICT_END,
            STATS_TAG, STATS_END,
        ]
        assert len(all_tags) == len(set(all_tags))
