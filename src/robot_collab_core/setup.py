from setuptools import setup

package_name = "robot_collab_core"

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
    description="Mission state machine for ROS2 multimodal robot collaboration.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "mission_state_machine = robot_collab_core.mission_state_machine:main",
        ],
    },
)

