"""Transform math for the SLAM adapter (no ROS imports, unit-testable).

A backend gives us ``map -> base``. The robot's wheel odometry gives
``odom -> base``. Nav2 needs ``map -> odom``, which is:

    T_map_odom = T_map_base * T_odom_base^-1

Keeping this here rather than inside the node means the composition can be
tested without a ROS graph.
"""

from __future__ import annotations

from typing import NamedTuple


class Quat(NamedTuple):
    x: float
    y: float
    z: float
    w: float


class Transform(NamedTuple):
    translation: tuple[float, float, float]
    rotation: Quat


IDENTITY = Transform((0.0, 0.0, 0.0), Quat(0.0, 0.0, 0.0, 1.0))


def quat_multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return Quat(
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_conjugate(q: Quat) -> Quat:
    return Quat(-q.x, -q.y, -q.z, q.w)


def quat_normalize(q: Quat) -> Quat:
    norm = (q.x**2 + q.y**2 + q.z**2 + q.w**2) ** 0.5
    if norm == 0:
        raise ValueError("cannot normalise a zero quaternion")
    return Quat(q.x / norm, q.y / norm, q.z / norm, q.w / norm)


def rotate(rotation: Quat, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate a vector by a unit quaternion (q * v * q^-1, expanded)."""
    q = quat_normalize(rotation)
    vx, vy, vz = vector
    # t = 2 * cross(q.xyz, v)
    tx = 2 * (q.y * vz - q.z * vy)
    ty = 2 * (q.z * vx - q.x * vz)
    tz = 2 * (q.x * vy - q.y * vx)
    # v' = v + q.w * t + cross(q.xyz, t)
    return (
        vx + q.w * tx + (q.y * tz - q.z * ty),
        vy + q.w * ty + (q.z * tx - q.x * tz),
        vz + q.w * tz + (q.x * ty - q.y * tx),
    )


def compose(parent_child: Transform, child_leaf: Transform) -> Transform:
    """Return parent->leaf = parent->child * child->leaf."""
    rotated = rotate(parent_child.rotation, child_leaf.translation)
    translation = (
        parent_child.translation[0] + rotated[0],
        parent_child.translation[1] + rotated[1],
        parent_child.translation[2] + rotated[2],
    )
    rotation = quat_normalize(quat_multiply(parent_child.rotation, child_leaf.rotation))
    return Transform(translation, rotation)


def inverse(transform: Transform) -> Transform:
    rotation = quat_normalize(quat_conjugate(transform.rotation))
    rotated = rotate(rotation, transform.translation)
    return Transform((-rotated[0], -rotated[1], -rotated[2]), rotation)


def map_to_odom(map_base: Transform, odom_base: Transform) -> Transform:
    """The transform Nav2 asks for, from any backend's map->base estimate."""
    return compose(map_base, inverse(odom_base))
