#!/usr/bin/env bash
cd /mnt/d/study_claudecode/ros2-multimodal-robot-collab-main || exit 1
cat > /tmp/msg.txt <<'EOF'
fix(sim): closing the window closes the window

The window was started with a respawn, so closing it opened it again,
and again, and again: a window the person cannot get rid of. The stack
does not need the window to run, and the crash it was there to answer
(WSLg's clipboard bridge) leaves the simulator and the nodes running.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
git add -A
git commit -F /tmp/msg.txt > /tmp/commit_out.txt 2>&1
echo "exit=$?"
tail -5 /tmp/commit_out.txt
rm -f /tmp/msg.txt
git log --oneline -2
git status --short
