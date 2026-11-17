#!/usr/bin/env bash
cd /mnt/d/study_claudecode/ros2-multimodal-robot-collab-main || exit 1
cat > /tmp/msg.txt <<'EOF'
chore: the commit message file does not belong in the repository

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
git add -A
git commit -F /tmp/msg.txt > /tmp/commit_out.txt 2>&1
echo "exit=$?"
tail -6 /tmp/commit_out.txt
rm -f /tmp/msg.txt
git log --oneline -4
echo "--- status ---"
git status --short
