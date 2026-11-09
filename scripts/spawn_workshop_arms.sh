#!/bin/bash
# Spawn the official robot arms into the running workshop.
#
# FANUC comes from the apt package moveit_resources_fanuc_description.
# KUKA and Dobot come from third_party/robot_descriptions (see
# scripts/fetch_robot_descriptions.sh).
#
# Their xacro trees reference sibling packages through $(find …) inside
# included files, so the tree is copied to /tmp and every reference is rewritten
# to an absolute path before xacro runs. package:// is rewritten to file:// for
# Gazebo's URDF loader.
set -o pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESC="$REPO/third_party/robot_descriptions"
source /opt/ros/humble/setup.bash
source "$REPO/install/setup.bash"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
GAZEBO_MODELS="$(bash "$REPO/scripts/setup_gazebo_models.sh")"
export GAZEBO_MODEL_PATH="$GAZEBO_MODELS:${GAZEBO_MODEL_PATH:-}"

TREE=/tmp/xacro_tree
rm -rf "$TREE"
mkdir -p "$TREE"
[ -d "$DESC/kuka_experimental" ] && cp -r "$DESC/kuka_experimental" "$TREE/"
[ -d "$DESC/dobot_6axis" ] && cp -r "$DESC/dobot_6axis" "$TREE/"

find "$TREE" \( -name '*.xacro' -o -name '*.urdf' \) -print0 2>/dev/null |
while IFS= read -r -d '' f; do
  for p in $(grep -o '\$(find [a-zA-Z0-9_]*' "$f" | sed 's/\$(find //' | sort -u); do
    path=$(find "$TREE" -maxdepth 3 -type d -name "$p" | head -1)
    [ -n "$path" ] && sed -i "s|\$(find $p)|$path|g" "$f"
  done
done

render() {
  src="$1"; out="$2"
  xacro "$src" > "$out" || { echo "xacro failed: $src"; return 1; }
  for p in $(grep -o 'package://[a-zA-Z0-9_]*' "$out" | sed 's|package://||' | sort -u); do
    path=$(find "$TREE" -maxdepth 3 -type d -name "$p" | head -1)
    [ -n "$path" ] && sed -i "s|package://$p/|file://$path/|g" "$out"
  done
  python3 "$REPO/scripts/ensure_urdf_inertials.py" "$out"
  echo "rendered $out"
}

spawn() {
  name="$1"; file="$2"; x="$3"; y="$4"; z="$5"
  # Planning URDFs fall over when spawned dynamically, so convert to a static
  # SDF first. spawn_entity.py also depends on ros2cli discovery, which is
  # unreliable in this WSL + CycloneDDS setup; call the service directly.
  sdf="/tmp/${name}.sdf"
  python3 "$REPO/scripts/make_static_sdf.py" "$file" "$sdf" >/dev/null || return 1
  python3 "$REPO/scripts/spawn_entity_direct.py" spawn "$name" "$sdf" "$x" "$y" "$z"
}

# FANUC: plain URDF from the apt description package. Its meshes use
# package:// URIs, which Gazebo's URDF loader cannot resolve; rewrite them.
FANUC=/opt/ros/humble/share/moveit_resources_fanuc_description/urdf/fanuc.urdf
FANUC_SHARE=/opt/ros/humble/share/moveit_resources_fanuc_description
if [ -f "$FANUC" ]; then
  cp "$FANUC" /tmp/fanuc_m10ia.urdf
  sed -i "s|package://moveit_resources_fanuc_description/|file://$FANUC_SHARE/|g" /tmp/fanuc_m10ia.urdf
  python3 "$REPO/scripts/ensure_urdf_inertials.py" /tmp/fanuc_m10ia.urdf
  spawn fanuc_m10ia_real /tmp/fanuc_m10ia.urdf -2.6 1.8 0.42
fi

if [ -d "$TREE/kuka_experimental" ]; then
  render "$TREE/kuka_experimental/kuka_kr10_support/urdf/kr10r1100sixx.xacro" /tmp/kuka_kr10.urdf \
    && spawn kuka_kr10_real /tmp/kuka_kr10.urdf -2.6 -1.8 0.42
fi

if [ -d "$TREE/dobot_6axis" ]; then
  render "$TREE/dobot_6axis/cra_description/urdf/cr10_robot.xacro" /tmp/dobot_cr10.urdf \
    && spawn dobot_cr10_real /tmp/dobot_cr10.urdf 1.2 -1.8 0.02
fi

echo "arms spawned; see /model_states"
