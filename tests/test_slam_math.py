"""Transform math used by the SLAM adapter (no ROS required)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "robot_collab_slam"))
from robot_collab_slam.slam_math import (  # noqa: E402
    IDENTITY,
    Quat,
    Transform,
    compose,
    inverse,
    map_to_odom,
)


def yaw(angle: float) -> Quat:
    return Quat(0.0, 0.0, math.sin(angle / 2), math.cos(angle / 2))


def assert_transform(actual: Transform, expected: Transform, tolerance: float = 1e-9) -> None:
    for a, e in zip(actual.translation, expected.translation, strict=True):
        assert a == pytest.approx(e, abs=tolerance)
    for a, e in zip(actual.rotation, expected.rotation, strict=True):
        assert a == pytest.approx(e, abs=tolerance)


def test_identity_composition() -> None:
    assert compose(IDENTITY, IDENTITY) == IDENTITY


def test_pure_translations_compose() -> None:
    a = Transform((1.0, 0.0, 0.0), Quat(0, 0, 0, 1))
    b = Transform((0.5, 0.0, 0.0), Quat(0, 0, 0, 1))
    assert compose(a, b).translation == pytest.approx((1.5, 0.0, 0.0))


def test_inverse_round_trip() -> None:
    transform = Transform((1.0, 2.0, 0.0), yaw(0.7))
    assert_transform(compose(transform, inverse(transform)), IDENTITY, 1e-9)


def test_map_to_odom_is_identity_when_map_equals_odom() -> None:
    odom_base = Transform((3.0, 0.5, 0.0), yaw(1.1))
    assert_transform(map_to_odom(odom_base, odom_base), IDENTITY, 1e-9)


def test_map_to_odom_recovers_a_known_correction() -> None:
    # The backend says the robot is at (1, 0); wheel odometry says (2, 0).
    # The map correction must be -1 m in x so that map->base lands at (1, 0).
    map_base = Transform((1.0, 0.0, 0.0), Quat(0, 0, 0, 1))
    odom_base = Transform((2.0, 0.0, 0.0), Quat(0, 0, 0, 1))
    correction = map_to_odom(map_base, odom_base)
    assert correction.translation == pytest.approx((-1.0, 0.0, 0.0))

    # map->odom * odom->base == map->base
    assert_transform(compose(correction, odom_base), map_base, 1e-9)


def test_map_to_odom_with_rotation() -> None:
    map_base = Transform((1.0, 1.0, 0.0), yaw(math.pi / 2))
    odom_base = Transform((0.5, 0.0, 0.0), yaw(0.0))
    correction = map_to_odom(map_base, odom_base)
    assert_transform(compose(correction, odom_base), map_base, 1e-9)


def test_zero_quaternion_is_rejected() -> None:
    with pytest.raises(ValueError, match="zero quaternion"):
        compose(Transform((0, 0, 0), Quat(0, 0, 0, 0)), IDENTITY)
