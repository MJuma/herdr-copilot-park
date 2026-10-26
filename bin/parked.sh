#!/usr/bin/env bash
# The placeholder for a parked GitHub Copilot CLI session.
#
# Usage: parked.sh SESSION_ID TITLE PARKED_AT -- [COPILOT_OPTION...]
#
# It shows the parked session. Press Enter to load the session with
# "copilot --resume=SESSION_ID COPILOT_OPTION...". Press Ctrl+C to go back to
# the shell.
#
# It reports itself to Herdr as the agent "parked", with this command as its
# resume command. Thus a Herdr restart starts the placeholder again, not
# Copilot.

set -u

SOURCE=copilot-park
AGENT=parked

session_id=${1:?usage: parked.sh SESSION_ID TITLE PARKED_AT -- [COPILOT_OPTION...]}
title=${2:-Copilot session}
parked_at=${3:-}
shift 3 2>/dev/null || shift $#
[ "${1:-}" = "--" ] && shift
options=("$@")
self=$0
herdr=${HERDR_BIN_PATH:-herdr}

now_ns() { python3 -c 'import time; print(time.time_ns())'; }

in_herdr() {
    [ "${HERDR_ENV:-}" = 1 ] && command -v "$herdr" >/dev/null 2>&1
}

# HERDR_PANE_ID can be old after a pane moves. Herdr resolves it to the
# current ID.
pane_id() {
    "$herdr" pane current 2>/dev/null | python3 -c \
        'import json, sys; print(json.load(sys.stdin)["result"]["pane"]["pane_id"])' 2>/dev/null
}

report_parked() {
    in_herdr || return 0
    local pane
    pane=$(pane_id) && [ -n "$pane" ] || return 0
    "$herdr" pane report-agent "$pane" --source "$SOURCE" --agent "$AGENT" \
        --state idle --seq "$(now_ns)" --agent-session-id "$session_id" \
        -- bash "$self" "$session_id" "$title" "$parked_at" -- \
        ${options[@]+"${options[@]}"} >/dev/null 2>&1 || true
}

release() {
    in_herdr || return 0
    local pane
    pane=$(pane_id) && [ -n "$pane" ] || return 0
    "$herdr" pane release-agent "$pane" --source "$SOURCE" --agent "$AGENT" \
        --seq "$(now_ns)" >/dev/null 2>&1 || true
}

resume_command() {
    printf 'copilot --resume=%s' "$session_id"
    [ ${#options[@]} -gt 0 ] && printf ' %s' "${options[@]}"
}

draw() {
    printf '\033]0;parked: %s\007' "$title"
    printf '\033[H\033[2J\033[3J'
    printf '\n  \033[1m%s\033[0m\n\n' "$title"
    printf '  \033[2mParked Copilot session. Copilot is not running.\033[0m\n\n'
    printf '  Session   %s\n' "$session_id"
    [ -n "$parked_at" ] && printf '  Parked    %s\n' "$parked_at"
    printf '  Command   %s\n\n' "$(resume_command)"
    printf '  Press \033[1mEnter\033[0m to load.  Press \033[1mCtrl+C\033[0m to return to the shell.\n'
}

leave() {
    stop_rereport
    release
    printf '\033[H\033[2J\033[3J'
    printf 'Parked session not loaded. To load it later, run:\n  %s\n' "$(resume_command)"
    exit 130
}

stop_rereport() {
    [ -n "${rereport_pid:-}" ] && kill "$rereport_pid" 2>/dev/null
    rereport_pid=
}

# Handle only Ctrl+C. A hangup or terminate signal comes from Herdr when the
# server or the pane stops. Then the placeholder must stop at once without a
# release, so that Herdr keeps the resume command.
trap leave INT
trap draw WINCH

report_parked
# Herdr can clear the first report while it notices that Copilot exited.
# Report again a short time later.
( sleep 2; report_parked; sleep 6; report_parked ) </dev/null >/dev/null 2>&1 &
rereport_pid=$!
draw

# "read" stops early when a resize signal arrives. Then wait again.
while :; do
    IFS= read -r _ && break
    status=$?
    [ "$status" -gt 128 ] && continue
    leave
done

trap - INT WINCH
stop_rereport
release
printf '\033[H\033[2J\033[3J'

if ! command -v copilot >/dev/null 2>&1; then
    printf 'copilot is not on PATH. Load the session later with:\n  %s\n' "$(resume_command)"
    exit 1
fi

# Herdr's Copilot integration reports the session ID only after the first
# prompt. Send the same report a few seconds after Copilot starts, so that a
# Herdr restart brings back this session also before that prompt.
if in_herdr; then
    pane=$(pane_id)
    if [ -n "$pane" ]; then
        (
            sleep 5
            "$herdr" pane report-agent-session "$pane" --source herdr:copilot \
                --agent copilot --agent-session-id "$session_id" \
                --seq "$(now_ns)" >/dev/null 2>&1
        ) </dev/null >/dev/null 2>&1 &
    fi
fi

exec copilot --resume="$session_id" ${options[@]+"${options[@]}"}
