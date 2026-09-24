#!/bin/sh
# Install jget and its AI agent skill (macOS / Linux / Git Bash on Windows).
#
#   curl -fsSL https://raw.githubusercontent.com/wyp0596/jget/main/install.sh | sh
#
# Extra options are passed through to `jget install`, e.g.:
#
#   curl -fsSL .../install.sh | sh -s -- --cursor --bin-dir /usr/local/bin
set -eu

REPO="${JGET_REPO:-wyp0596/jget}"
REF="${JGET_REF:-main}"
BASE_URL="${JGET_BASE_URL:-https://raw.githubusercontent.com/${REPO}/${REF}}"

err() {
	printf 'jget-install: %s\n' "$*" >&2
	exit 1
}

PYTHON=""
for candidate in python3 python; do
	if command -v "$candidate" >/dev/null 2>&1 \
		&& "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
		PYTHON="$candidate"
		break
	fi
done
[ -n "$PYTHON" ] || err "Python 3.8+ is required (python3 or python), please install it first"

if command -v curl >/dev/null 2>&1; then
	fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
	fetch() { wget -q "$1" -O "$2"; }
else
	err "curl or wget is required"
fi

tmp="$(mktemp -d 2>/dev/null || mktemp -d -t jget)"
trap 'rm -rf "$tmp"' EXIT INT TERM

echo "Downloading jget from ${BASE_URL} ..."
fetch "${BASE_URL}/jget.py" "$tmp/jget.py" || err "failed to download ${BASE_URL}/jget.py"
fetch "${BASE_URL}/SKILL.md" "$tmp/SKILL.md" || err "failed to download ${BASE_URL}/SKILL.md"
grep -q "jget: fetch and display a Jira ticket" "$tmp/jget.py" || err "downloaded file does not look like jget"

"$PYTHON" "$tmp/jget.py" install "$@"

cat <<'EOF'

Next, set your Jira credentials (e.g. in ~/.zshrc or ~/.bashrc):

  export JIRA_URL=https://your-domain.atlassian.net
  export JIRA_USER=you@example.com
  export JIRA_TOKEN=<api-token>

For Jira Server / Data Center Personal Access Tokens, also set JIRA_AUTH=bearer
(JIRA_USER is then not needed).

Then try:  jget PROJ-123
EOF
