from setuptools import setup

package_name = "robot_collab_agent"

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
    description="Agent gateway that routes LLM/VLM skill calls into ROS2 actions and services.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "agent_gateway_node = robot_collab_agent.agent_gateway_node:main",
        ],
    },
)

