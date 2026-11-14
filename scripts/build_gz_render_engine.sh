#!/usr/bin/env bash
# Build the one piece of gz-sim that Ubuntu does not package: the ogre2
# render engine.
#
# The depth camera needs it -- a sensor renders through it -- and the
# Fortress packages on Ubuntu ship the rendering library but not the engine
# that uses it, so a camera there advertises its topics and never publishes
# anything. The rest of gz-sim is installed, so this builds only the
# engine and installs it next to the other system plugins.
#
#   scripts/build_gz_render_engine.sh
set -euo pipefail

WORK=/tmp/gz_sim_render_build
TAG=ignition-gazebo6_6.18.0
PREFIX=/usr

echo "== fetching the source =="
if [ ! -d "$WORK/gz-sim" ]; then
  mkdir -p "$WORK"
  git clone --depth 1 --branch "$TAG" https://github.com/gazebosim/gz-sim.git "$WORK/gz-sim"
fi

cd "$WORK/gz-sim"

echo "== configuring =="
# Only the server and the render engine; the GUI, the demos and the tests
# are not needed to run the stack.
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_TESTING=OFF \
  -DBUILD_GUI=OFF \
  -DBUILD_DEMOS=OFF \
  -DCMAKE_PREFIX_PATH=/usr \
  -DUSE_EXTERNAL_OGRE=OFF \
  2>&1 | tail -20

echo "== building the render engine =="
cmake --build build --target ignition-gazebo6-render-engine-ogre2 -j"$(nproc)" 2>&1 | tail -20

echo "== installing =="
cmake --install build 2>&1 | tail -5

echo "== the engine is here =="
ls -la /usr/lib/x86_64-linux-gnu/ign-gazebo-6/plugins/ | grep -i render-engine
