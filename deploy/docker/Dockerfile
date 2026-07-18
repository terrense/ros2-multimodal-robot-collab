# CPU cloud image for headless simulation & L2 data collection.
# Base: ROS2 Humble on Ubuntu 22.04 — the exact stack the repo was verified on
# (WSL Ubuntu-22.04 + Humble, full delivery mission COMPLETED, 2026-07-17).
FROM ros:humble-ros-base-jammy

# Domestic mirror toggle for cloud instances in China (AutoDL etc. often have
# their own accelerated mirrors preconfigured; then build with USE_TUNA=0).
ARG USE_TUNA=1
RUN if [ "$USE_TUNA" = "1" ]; then \
      sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g; s|http://ports.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list && \
      sed -i 's|http://packages.ros.org/ros2/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu|g' /etc/apt/sources.list.d/ros2-latest.list || true; \
    fi

# Verified dependency set:
# - nav2-msgs is an import-time dependency of nav_skill_server even in simulated mode
# - gazebo-ros-pkgs provides gzserver (headless) + ros plugins for the L2 world
# - navigation2/nav2-bringup + slam-toolbox for the nav2 backend of nav_skill_server
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ros-humble-nav2-msgs \
      ros-humble-gazebo-ros-pkgs \
      ros-humble-navigation2 \
      ros-humble-nav2-bringup \
      ros-humble-slam-toolbox \
      python3-colcon-common-extensions \
      python3-pip \
      git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ws
COPY src/ /ws/src/
COPY skills/ /ws/skills/
COPY agent_harness/ /ws/agent_harness/
COPY agent_env/ /ws/agent_env/
COPY scripts/ /ws/scripts/

RUN bash -c "source /opt/ros/humble/setup.bash && colcon build --symlink-install --base-paths src"

COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Isolation knobs for running many sims on one host (override per container).
ENV ROS_DOMAIN_ID=0 \
    GAZEBO_MASTER_URI=http://127.0.0.1:11345 \
    ROS_LOCALHOST_ONLY=1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
