"""Decorator pattern examples for Aleph testing."""

import functools
import time


def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} took {elapsed:.4f}s")
        return result
    return wrapper


def retry(max_attempts=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if attempt == max_attempts - 1:
                        raise
        return wrapper
    return decorator


class Config:
    def __init__(self):
        self._values = {}

    @property
    def debug(self):
        return self._values.get("debug", False)

    @property
    def timeout(self):
        return self._values.get("timeout", 30)

    @staticmethod
    def from_dict(data):
        config = Config()
        config._values = dict(data)
        return config

    @classmethod
    def default(cls):
        return cls()


@timer
def slow_computation(n):
    total = 0
    for i in range(n):
        total += i * i
    return total


@retry(max_attempts=3)
def unreliable_fetch(url):
    import random
    if random.random() < 0.5:
        raise ConnectionError("Network error")
    return f"Data from {url}"
