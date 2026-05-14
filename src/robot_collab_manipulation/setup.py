from setuptools import setup

package_name = "robot_collab_manipulation"

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
    description="Manipulation skill server for pick, place, and handover workflows.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "arm_skill_server = robot_collab_manipulation.arm_skill_server:main",
        ],
    },
)

