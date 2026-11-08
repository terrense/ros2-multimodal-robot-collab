#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> [1/8] Rewriting /etc/apt/sources.list with the Tsinghua mirror"
if [ ! -f /etc/apt/sources.list.bak ]; then
  sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak
fi
sudo tee /etc/apt/sources.list > /dev/null <<'EOF'
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-backports main restricted universe multiverse
deb http://security.ubuntu.com/ubuntu/ jammy-security main restricted universe multiverse
EOF

echo "==> [2/8] apt update against the Tsinghua mirror"
sudo apt update

echo "==> [3/8] Installing curl/gnupg2 and enabling universe"
sudo apt install -y curl gnupg2 lsb-release software-properties-common ca-certificates
sudo add-apt-repository -y universe

echo "==> [4/8] Adding the ROS2 apt source (Tsinghua mirror)"
sudo curl -sSL --retry 3 --max-time 20 https://mirrors.tuna.tsinghua.edu.cn/rosdistro/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu jammy main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt update

echo "==> [5/8] Installing ROS2 Humble desktop + Gazebo + Nav2 + slam_toolbox (the big download, be patient)"
sudo apt install -y \
  ros-humble-desktop \
  ros-humble-navigation2 \
  ros-humble-nav2-bringup \
  ros-humble-gazebo-ros-pkgs \
  ros-humble-slam-toolbox \
  ros-humble-robot-localization \
  python3-colcon-common-extensions \
  python3-rosdep \
  python3-vcstool \
  python3-pip

echo "==> [6/8] Persisting ROS2 environment sourcing in ~/.bashrc"
grep -qxF 'source /opt/ros/humble/setup.bash' ~/.bashrc || echo 'source /opt/ros/humble/setup.bash' >> ~/.bashrc
# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
set -u

echo "==> [7/8] rosdep init/update against the Tsinghua rosdistro mirror"
grep -qxF 'export ROSDISTRO_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml' ~/.bashrc || \
  echo 'export ROSDISTRO_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml' >> ~/.bashrc
export ROSDISTRO_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml
sudo mkdir -p /etc/ros/rosdep/sources.list.d
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  sudo curl -sSL --retry 3 --max-time 20 \
    https://mirrors.tuna.tsinghua.edu.cn/rosdistro/rosdep/sources.list.d/20-default.list \
    -o /etc/ros/rosdep/sources.list.d/20-default.list
fi
rosdep update

echo "==> [8/8] Installing workspace package dependencies (src only)"
# ament_python has no rosdep rule upstream (it's a build_type marker, not an
# installable package); skip it so the real dependencies still get resolved.
rosdep install --from-paths src -y --ignore-src --skip-keys=ament_python

echo "INSTALL_DONE_OK"
