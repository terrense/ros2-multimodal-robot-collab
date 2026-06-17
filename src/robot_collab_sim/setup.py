from glob import glob
from setuptools import setup

package_name = "robot_collab_sim"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/urdf", glob("urdf/*")),
        (f"share/{package_name}/worlds", glob("worlds/*")),
        (f"share/{package_name}/config", glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="terrense",
    maintainer_email="terrense@example.com",
    description="Gazebo simulation assets for the ROS2 robot collaboration demo.",
    license="MIT",
)
