#!/bin/bash
# Spawn the official robot arms into the running workshop.
#
#   scripts/spawn_workshop_arms.sh                    # spawn into the workshop
#   scripts/spawn_workshop_arms.sh --render-only DIR  # only write the SDFs
#
# FANUC comes from the apt package moveit_resources_fanuc_description.
# KUKA and Dobot come from third_party/robot_descriptions (see
# scripts/fetch_robot_descriptions.sh).
#
# Their xacro trees reference sibling packages through $(find …) inside
# included files, so the tree is copied to /tmp and every reference is rewritten
# to an absolute path before xacro runs. package:// is rewritten to file:// for
# Gazebo's URDF loader.
#
# The render-only mode writes the arms' static SDFs and stops: the gz-sim
# workshop has them in its world file rather than spawning them, because
# the robot has to be in the world file too (the diff-drive system is
# configured before anything is spawned into it), and one file that holds
# the whole workcell is easier to reason about than a world plus a
# spawning script.
set -o pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESC="$REPO/third_party/robot_descriptions"
source /opt/ros/humble/setup.bash
source "$REPO/install/setup.bash"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
GAZEBO_MODELS="$(bash "$REPO/scripts/setup_gazebo_models.sh")"
export GAZEBO_MODEL_PATH="$GAZEBO_MODELS:${GAZEBO_MODEL_PATH:-}"

RENDER_ONLY=0
OUT_DIR=/tmp/workshop_arms
if [ "${1:-}" = "--render-only" ]; then
  RENDER_ONLY=1
  OUT_DIR="${2:-$OUT_DIR}"
  mkdir -p "$OUT_DIR"
fi

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
  src="$1"; out="$2"; brand="${3:-}"
  xacro "$src" > "$out" || { echo "xacro failed: $src"; return 1; }
  for p in $(grep -o 'package://[a-zA-Z0-9_]*' "$out" | sed 's|package://||' | sort -u); do
    path=$(find "$TREE" -maxdepth 3 -type d -name "$p" | head -1)
    [ -n "$path" ] && sed -i "s|package://$p/|file://$path/|g" "$out"
  done
  if [ -n "$brand" ]; then
    python3 "$REPO/scripts/prepare_arms.py" "$brand" "$out" "$out" >/dev/null
  fi
  python3 "$REPO/scripts/ensure_urdf_inertials.py" "$out" >/dev/null
  echo "rendered $out"
}

render_static() {
  name="$1"; file="$2"
  # Planning URDFs fall over when spawned dynamically, so convert to a static
  # SDF first.
  sdf="$OUT_DIR/${name}.sdf"
  python3 "$REPO/scripts/make_static_sdf.py" "$file" "$sdf" >/dev/null || return 1
  echo "$sdf"
}

spawn() {
  name="$1"; file="$2"; x="$3"; y="$4"; z="$5"
  # spawn_entity.py also depends on ros2cli discovery, which is unreliable in
  # this WSL + CycloneDDS setup; call the service directly.
  sdf="$(render_static "$name" "$file")" || return 1
  python3 "$REPO/scripts/spawn_entity_direct.py" spawn "$name" "$sdf" "$x" "$y" "$z"
}

# FANUC: plain URDF from the apt description package. Its meshes use
# package:// URIs, which Gazebo's URDF loader cannot resolve; rewrite them.
FANUC=/opt/ros/humble/share/moveit_resources_fanuc_description/urdf/fanuc.urdf
FANUC_SHARE=/opt/ros/humble/share/moveit_resources_fanuc_description
if [ -f "$FANUC" ]; then
  cp "$FANUC" "$OUT_DIR/fanuc_m10ia.urdf"
  sed -i "s|package://moveit_resources_fanuc_description/|file://$FANUC_SHARE/|g" "$OUT_DIR/fanuc_m10ia.urdf"
  python3 "$REPO/scripts/prepare_arms.py" fanuc "$OUT_DIR/fanuc_m10ia.urdf" "$OUT_DIR/fanuc_m10ia.urdf" >/dev/null
  python3 "$REPO/scripts/ensure_urdf_inertials.py" "$OUT_DIR/fanuc_m10ia.urdf" >/dev/null
  if [ "$RENDER_ONLY" = "1" ]; then
    render_static fanuc_m10ia_real "$OUT_DIR/fanuc_m10ia.urdf"
  else
    spawn fanuc_m10ia_real "$OUT_DIR/fanuc_m10ia.urdf" -2.6 1.8 0.42
  fi
fi

if [ -d "$TREE/kuka_experimental" ]; then
  render "$TREE/kuka_experimental/kuka_kr10_support/urdf/kr10r1100sixx.xacro" "$OUT_DIR/kuka_kr10.urdf" kuka
  if [ "$RENDER_ONLY" = "1" ]; then
    render_static kuka_kr10_real "$OUT_DIR/kuka_kr10.urdf"
  else
    spawn kuka_kr10_real "$OUT_DIR/kuka_kr10.urdf" -2.6 -1.8 0.42
  fi
fi

# The Dobot is mounted on the mobile base by scripts/mount_dobot.py and shipped
# inside the robot's own URDF, so it is not spawned separately here.

if [ "$RENDER_ONLY" = "1" ]; then
  echo "arms rendered into $OUT_DIR"
else
  echo "arms spawned; see /model_states"
fi
