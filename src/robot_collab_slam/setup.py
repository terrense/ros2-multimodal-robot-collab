from setuptools import setup

package_name = "robot_collab_slam"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="terrense",
    maintainer_email="terrense@example.com",
    description="ROS2 SLAM/VIO adapters for feeding VINS-Mono estimates into the mobile robot stack.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "vins_mono_bridge_node = robot_collab_slam.vins_mono_bridge_node:main",
        ],
    },
)

