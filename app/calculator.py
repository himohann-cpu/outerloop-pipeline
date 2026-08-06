"""A tiny example module that the CI pipeline builds and tests.

Intentionally simple — swap this out for your real application code. It
exists so the build/test/deploy stages (and the failure-analysis agents)
have something real to run against.
"""


def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
