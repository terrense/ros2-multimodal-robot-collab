from setuptools import setup

package_name = "robot_collab_hri"

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
    description="Human-robot interaction gateway for ASR, TTS, gestures, and mission events.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "voice_gateway_node = robot_collab_hri.voice_gateway_node:main",
        ],
    },
)

