# syntax=docker/dockerfile:1

# Three stages, because the three concerns have different lifetimes:
#
#   deps     rosdep resolution -- changes only when a package.xml changes
#   builder  colcon build      -- changes on every source edit
#   runtime  what ships        -- base image + resolved deps + compiled overlay
#
# Putting deps in its own stage means an edit to a .py file re-runs only the
# build, not the apt/rosdep install, which is the slow part.
#
# third_party/ (203MB of vendored Ultralytics / VINS-Mono / OpenPose snapshots)
# is deliberately NOT in the image. Those are heavy, optional perception
# backends; mount them or use a dedicated image. The nodes degrade with a clear
# error when they are absent.

FROM ros:humble-ros-base AS deps
SHELL ["/bin/bash", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-rosdep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ws
COPY src ./src

RUN [ -f /etc/ros/rosdep/sources.list.d/20-default.list ] || rosdep init \
    && rosdep update --rosdistro humble \
    && rosdep install --from-paths src --ignore-src -y --rosdistro humble


FROM deps AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*

RUN source /opt/ros/humble/setup.bash \
    && colcon build --base-paths src --merge-install \
                    --cmake-args -DCMAKE_BUILD_TYPE=Release


FROM deps AS runtime

COPY --from=builder /ws/install /ws/install

# ros_entrypoint.sh comes from the base image and sources /opt/ros/humble.
# Source the overlay for interactive shells; scripts should source it
# explicitly, the same way they would on a robot.
RUN echo "source /ws/install/setup.bash" >> /root/.bashrc
ENV ROS_OVERLAY=/ws/install

WORKDIR /ws
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
