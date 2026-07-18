"""Sample test file for coverage mapping analysis."""


def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def is_positive(n):
    return n > 0


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0
    assert multiply(-1, -1) == 1


def test_divide():
    assert divide(6, 2) == 3.0
    assert divide(1, 3) == 1 / 3


def test_divide_by_zero():
    try:
        divide(1, 0)
        assert False, "Should have raised"
    except ValueError:
        pass


def test_is_positive():
    assert is_positive(1) is True
    assert is_positive(-1) is False
    assert is_positive(0) is False
