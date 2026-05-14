from setuptools import setup

package_name = "robot_collab_navigation"

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
    description="Navigation skill server for station-level robot movement.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "nav_skill_server = robot_collab_navigation.nav_skill_server:main",
        ],
    },
)

