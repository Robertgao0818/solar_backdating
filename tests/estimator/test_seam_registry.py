"""Tests for the estimator registry surface (seam.py)."""
from __future__ import annotations

import pytest

from solar_backdating.estimators import (
    available_estimators,
    get_estimator,
    register,
)


def test_available_contains_builtins():
    names = available_estimators()
    assert names == sorted(names)  # sorted
    assert {"fpd", "sustained", "pava"} <= set(names)


def test_register_and_duplicate():
    @register("unit_test_dummy_estimator")
    def _dummy(observations, clamp, config):  # pragma: no cover - never called
        raise NotImplementedError

    assert "unit_test_dummy_estimator" in available_estimators()

    with pytest.raises(ValueError):

        @register("unit_test_dummy_estimator")
        def _dummy2(observations, clamp, config):  # pragma: no cover
            raise NotImplementedError


def test_get_estimator_miss_raises_keyerror():
    with pytest.raises(KeyError):
        get_estimator("no_such_estimator_name")


def test_get_estimator_returns_callable():
    fn = get_estimator("fpd")
    assert callable(fn)
