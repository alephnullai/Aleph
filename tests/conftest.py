"""Shared test fixtures."""

import os
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def cpp_simple_path():
    return os.path.join(FIXTURES_DIR, "cpp", "sample_simple.cpp")


@pytest.fixture
def cpp_complex_path():
    return os.path.join(FIXTURES_DIR, "cpp", "sample_complex.cpp")


@pytest.fixture
def rust_simple_path():
    return os.path.join(FIXTURES_DIR, "rust", "sample_simple.rs")


@pytest.fixture
def rust_complex_path():
    return os.path.join(FIXTURES_DIR, "rust", "sample_complex.rs")


@pytest.fixture
def cpp_simple_source(cpp_simple_path):
    with open(cpp_simple_path) as f:
        return f.read()


@pytest.fixture
def rust_simple_source(rust_simple_path):
    with open(rust_simple_path) as f:
        return f.read()


@pytest.fixture
def cpp_realistic_path():
    return os.path.join(FIXTURES_DIR, "cpp", "sample_realistic.cpp")


@pytest.fixture
def cpp_intents_path():
    return os.path.join(FIXTURES_DIR, "cpp", "intents_sample.cpp")


@pytest.fixture
def python_error_handling_path():
    return os.path.join(FIXTURES_DIR, "python", "error_handling.py")


@pytest.fixture
def python_test_sample_path():
    return os.path.join(FIXTURES_DIR, "python", "test_sample.py")
