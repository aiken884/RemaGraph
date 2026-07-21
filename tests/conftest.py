"""Shared pytest fixtures and markers for RemaGraph tests."""


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that require real model2vec model download")
