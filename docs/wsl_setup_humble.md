# WSL + ROS2 Humble 安装步骤（Windows 10 / WSL2）

一次性把 `Ubuntu-22.04` 配成能 build 和跑这个项目的环境。预计 30–50 分钟、下载若干 GB。
每一大步后面都有一个 **检查点**，过了再往下走。

> 这台机器有多个 WSL 发行版，本项目固定用 **Ubuntu-22.04**。

---

## 0. 打开 WSL

Windows PowerShell：

```powershell
wsl -d Ubuntu-22.04
```

进去后确认是 Jammy：

```bash
cat /etc/os-release | grep VERSION_CODENAME   # 应为 jammy
```

---

## 1. 添加 ROS2 apt 源

```bash
sudo apt update
sudo apt install -y software-properties-common curl gnupg lsb-release
sudo add-apt-repository -y universe

sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | \
sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt update
```

**检查点**：`apt-cache policy ros-humble-desktop` 能列出候选版本（不是 None）。

---

## 2. 安装 ROS2 Humble + Gazebo + Nav2 + 工具链

```bash
sudo apt install -y \
  ros-humble-desktop \
  ros-humble-xacro \
  ros-humble-navigation2 \
  ros-humble-nav2-bringup \
  ros-humble-gazebo-ros-pkgs \
  ros-humble-slam-toolbox \
  ros-humble-robot-localization \
  python3-colcon-common-extensions \
  python3-rosdep \
  python3-vcstool \
  python3-pip
```

这是最大的一步（几 GB）。让它跑完。

**检查点**：

```bash
source /opt/ros/humble/setup.bash
ros2 --version && gazebo --version
```

---

## 3. 初始化 rosdep 并配好 shell

```bash
sudo rosdep init || true
rosdep update

echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

---

## 4. 安装感知 Python 依赖（YOLOv8）

```bash
pip3 install --upgrade pip
pip3 install ultralytics opencv-python numpy
```

> 注意：`ultralytics` 会拉 `torch`，是这步里最大的下载（可能 1–2 GB）。耐心等。
> 如果之后 `import cv2` 和 ROS 的 cv_bridge 冲突，改装 `pip3 install opencv-python-headless` 即可。

**检查点**：

```bash
python3 -c "import ultralytics, cv2, numpy; print('perception deps OK')"
```

---

## 5. 构建工作区

只构建 `src/`，不要让 colcon 去编 `third_party/`（VINS-Mono/OpenPose 源码快照）。

```bash
cd /mnt/f/Downloads/ros2-multimodal-robot-collab-main

rosdep install --from-paths src -y --ignore-src
colcon build --symlink-install --base-paths src
source install/setup.bash
```

> 在 `/mnt/f` 上构建可用但偏慢。要更快可 rsync 到 WSL 原生 ext4：
> ```bash
> mkdir -p ~/work
> rsync -a --exclude build --exclude install --exclude log \
>   /mnt/f/Downloads/ros2-multimodal-robot-collab-main/ \
>   ~/work/ros2-multimodal-robot-collab-main/
> cd ~/work/ros2-multimodal-robot-collab-main
> ```
> 但注意：原生副本和 Windows 里的 git 仓库是两份，提交记得在同一份里做。

**检查点**：

```bash
bash scripts/check_ros2_env.sh
```
应大部分 `[OK]`。

---

## 6. 拉取 YOLOv8 权重和样例图

```bash
bash scripts/fetch_models.sh
```

生成 `models/yolov8n.pt` 和 `models/coco_sample.jpg`。

---

## 7. 跑一次完整可见 demo

终端 A（起 Gazebo + SLAM + Nav2 + 整套栈）：

```bash
cd /mnt/f/Downloads/ros2-multimodal-robot-collab-main
source /opt/ros/humble/setup.bash
source install/setup.bash
FULL_NAV=1 bash scripts/run_gazebo_visible_demo.sh
```

终端 B（发一个完整递送任务）：

```bash
cd /mnt/f/Downloads/ros2-multimodal-robot-collab-main
source /opt/ros/humble/setup.bash
source install/setup.bash
bash scripts/run_demo_mission.sh
```

---

## 关于 Gazebo 图形界面（Windows 10 重点）

WSLg（WSL 自带图形）官方面向 Windows 11。Windows 10 上 Gazebo 窗口**可能开不出来**。判断与对策：

1. 先直接试第 7 步。如果 Gazebo 窗口正常弹出 —— 你的 WSL 已支持 GUI，无需额外操作。
2. 渲染花屏 / 报 GL 错：
   ```bash
   export LIBGL_ALWAYS_SOFTWARE=1
   # 或用脚本开关：
   SOFTWARE_RENDERING=1 FULL_NAV=1 bash scripts/run_gazebo_visible_demo.sh
   ```
3. 完全没有窗口（无 WSLg）：装一个 Windows X server（如 VcXsrv），然后在 WSL 里：
   ```bash
   export DISPLAY=$(ip route list default | awk '{print $3}'):0
   export LIBGL_ALWAYS_INDIRECT=0
   ```
   再重跑第 7 步。
4. 实在不想要 GUI：导航/任务链路本身不依赖窗口。可以无头跑，用 `ros2 topic echo /mission/events`、`ros2 topic echo /odom` 观察机器人是否在动。

---

## 装完之后

回来告诉我“装好了”，我就能直接在 WSL 里替你：
- `colcon build` 验证编译
- 起图、发急停手势验证任务被打断
- 发导航目标验证 Nav2 真的驱动小车

这些之前因为没装 ROS 我做不了，装完就能做。
