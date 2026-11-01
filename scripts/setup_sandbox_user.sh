#!/usr/bin/env bash
# Create the unprivileged user that `agent-lab run --isolation linux-user` runs agent
# commands as, and open /opt/agentlab (the venv and the Python install) to it read-only.
#
# Usage (as root, e.g. from CI):   sudo scripts/setup_sandbox_user.sh [USER]
#
# Layout contract: uv installs into /opt/agentlab, so the repo and the runner's home are
# never touched by this script:
#   UV_PROJECT_ENVIRONMENT=/opt/agentlab/venv   UV_PYTHON_INSTALL_DIR=/opt/agentlab/python
# set before `uv python install` / `uv sync`. Anything else the sandbox user must NOT read
# (the repo, tasks/, .git, $HOME, $RUNNER_TEMP) is simply not made readable.
#
# The harness user also needs passwordless `sudo` for unshare, setpriv and pkill (GitHub-hosted
# runners already have it). That makes the harness user root-equivalent: use this on
# ephemeral machines only.
set -euo pipefail

SANDBOX_USER="${1:-agentlab-sandbox}"
OPT_DIR="${AGENTLAB_OPT_DIR:-/opt/agentlab}"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (sudo $0)" >&2
    exit 1
fi
if [ ! -d "$OPT_DIR/venv" ] || [ ! -d "$OPT_DIR/python" ]; then
    echo "error: $OPT_DIR/venv and $OPT_DIR/python must exist (run uv sync with" >&2
    echo "       UV_PROJECT_ENVIRONMENT and UV_PYTHON_INSTALL_DIR pointing there first)" >&2
    exit 1
fi

if id -u "$SANDBOX_USER" >/dev/null 2>&1; then
    echo "user $SANDBOX_USER already exists"
else
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$SANDBOX_USER"
    echo "created user $SANDBOX_USER"
fi
if [ "$(id -u "$SANDBOX_USER")" -eq 0 ]; then
    echo "error: $SANDBOX_USER must not be root" >&2
    exit 1
fi

# Readable and executable for everyone, writable for no one but the owner.
chmod o+rx "$OPT_DIR"
chmod -R o+rX,o-w "$OPT_DIR"
echo "$OPT_DIR is readable and executable, not writable, for $SANDBOX_USER"

as_sandbox() { setpriv --reuid="$SANDBOX_USER" --regid="$SANDBOX_USER" --clear-groups "$@"; }

as_sandbox test -r "$OPT_DIR/venv/bin/python" || { echo "self-check failed: venv not readable" >&2; exit 1; }
as_sandbox test -x "$OPT_DIR/venv/bin/python" || { echo "self-check failed: venv not executable" >&2; exit 1; }
if as_sandbox test -w "$OPT_DIR"; then
    echo "self-check failed: $OPT_DIR is writable by $SANDBOX_USER" >&2
    exit 1
fi
as_sandbox "$OPT_DIR/venv/bin/python" -c "import sys; print('sandbox user can run', sys.executable)" \
    || { echo "self-check failed: cannot run the venv's python" >&2; exit 1; }
echo "setup finished"
