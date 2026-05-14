from setuptools import setup

package_name = "robot_collab_perception"

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
    description="Perception adapters for tool detection, face authorization, and gesture recognition.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "tool_detector_node = robot_collab_perception.tool_detector_node:main",
            "face_auth_node = robot_collab_perception.face_auth_node:main",
            "gesture_recognizer_node = robot_collab_perception.gesture_recognizer_node:main",
        ],
    },
)

