#!/bin/bash
# Auto-commit and push index.html (and any other tracked changes) at end of session.
# Runs as a Stop hook — must exit 0 to allow the session to end.

cd /home/user/over25-scanner || exit 0

# Nothing to do if working tree is clean
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  exit 0
fi

# Build a one-line commit message from changed files
changed=$(git diff --name-only HEAD 2>/dev/null | head -5 | tr '\n' ' ' | sed 's/ $//')
untracked=$(git ls-files --others --exclude-standard | head -3 | tr '\n' ' ' | sed 's/ $//')
all_changed="${changed} ${untracked}"
msg="auto: guardar alterações — ${all_changed}"

git add -A
git commit -m "$msg" 2>&1 || exit 0

# Push to the configured upstream remote branch (handles local/remote name mismatch)
upstream=$(git rev-parse --abbrev-ref @{upstream} 2>/dev/null || echo "")
if [ -n "$upstream" ]; then
  remote_branch="${upstream#*/}"
  git push origin "HEAD:${remote_branch}" 2>&1
else
  branch=$(git branch --show-current)
  git push -u origin "$branch" 2>&1
fi

exit 0
