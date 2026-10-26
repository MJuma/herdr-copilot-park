#!/usr/bin/env python3
"""Park idle GitHub Copilot CLI sessions that run in Herdr panes.

Parking exits Copilot and starts a small placeholder in the same pane. The
placeholder shows the session and loads it again when you press Enter. It also
reports itself to Herdr, so a Herdr restart brings back the placeholder instead
of a running Copilot process.

Usage: park.py focused | all
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_SOURCE = PLUGIN_ROOT / "bin" / "parked.sh"

PARKED_AGENT = "parked"
COPILOT_AGENT = "copilot"
READY_STATES = {"idle", "done"}

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Copilot prints "Resume  copilot --resume=<id>" in its exit summary.
EXIT_SUMMARY = re.compile(r"Resume\s+copilot\s+--resume=(" + UUID + r")")
# Each Copilot process logs the session that it shows.
LOG_SESSION = re.compile(r"Registering foreground session:\s*(" + UUID + r")")

# Copilot options that choose a session. Parking replaces them with --resume.
SESSION_OPTIONS_WITH_VALUE = {"--session-id", "-n", "--name"}
SESSION_OPTIONS_OPTIONAL_VALUE = {"-r", "--resume", "--connect"}
SESSION_OPTIONS_WITHOUT_VALUE = {"--continue"}

TITLE_SUFFIX = " - GitHub Copilot"
DEFAULT_TITLE = "Copilot session"
MAX_TITLE_LENGTH = 120

# Herdr limits for a reported resume command.
MAX_RESUME_ARGS = 64
MAX_RESUME_BYTES = 8 * 1024

EXIT_TIMEOUT_SECONDS = 10.0
AGENT_CLEAR_SECONDS = 5.0
PLACEHOLDER_TIMEOUT_SECONDS = 6.0
POLL_SECONDS = 0.25


class ParkError(Exception):
    """A pane cannot be parked. The message tells the user why."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def strip_session_options(args: list[str]) -> list[str]:
    """Remove the Copilot options that select or name a session."""
    kept: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        name, has_value = arg.split("=", 1)[0], "=" in arg
        if name in SESSION_OPTIONS_WITHOUT_VALUE:
            i += 1
        elif name in SESSION_OPTIONS_WITH_VALUE:
            i += 1 if has_value else 2
        elif name in SESSION_OPTIONS_OPTIONAL_VALUE:
            next_is_value = i + 1 < len(args) and not args[i + 1].startswith("-")
            i += 2 if not has_value and next_is_value else 1
        else:
            kept.append(arg)
            i += 1
    return kept


def copilot_command(processes: list[dict]) -> tuple[list[str], list[int]] | None:
    """Return the Copilot options and process IDs from Herdr process info.

    Copilot runs as a Node wrapper ("node /usr/bin/copilot ...") and a native
    binary ("/.../copilot ..."). Both have the same options.
    """
    options: list[str] | None = None
    pids: list[int] = []
    for proc in processes:
        argv = proc.get("argv") or []
        if not argv:
            continue
        first = os.path.basename(argv[0])
        if first.startswith("node") and len(argv) > 1 and os.path.basename(argv[1]) == "copilot":
            options = argv[2:] if options is None else options
            pids.append(int(proc["pid"]))
        elif first == "copilot":
            options = argv[1:] if options is None else options
            pids.append(int(proc["pid"]))
    if options is None:
        return None
    return options, pids


def clean_title(terminal_title: str | None) -> str:
    """Make a short, safe title from the Copilot terminal title."""
    title = (terminal_title or "").strip()
    if title.endswith(TITLE_SUFFIX):
        title = title[: -len(TITLE_SUFFIX)]
    if title in ("", "GitHub Copilot"):
        title = DEFAULT_TITLE
    # Herdr refuses apostrophes and control characters in a resume command.
    title = title.replace("'", "\u2019")
    title = "".join(ch for ch in title if ch.isprintable())
    title = " ".join(title.split())
    return title[:MAX_TITLE_LENGTH] or DEFAULT_TITLE


def session_from_exit_summary(text: str) -> str | None:
    matches = EXIT_SUMMARY.findall(text)
    return matches[-1] if matches else None


def session_from_log(text: str) -> str | None:
    matches = LOG_SESSION.findall(text)
    return matches[-1] if matches else None


def check_resume_argv(argv: list[str]) -> None:
    """Raise ParkError if Herdr would refuse this resume command."""
    for arg in argv:
        if "'" in arg:
            raise ParkError(f"an option contains an apostrophe: {arg}")
        if any(not ch.isprintable() for ch in arg):
            raise ParkError(f"an option contains a control character: {arg!r}")
    if len(argv) > MAX_RESUME_ARGS:
        raise ParkError(f"the command has more than {MAX_RESUME_ARGS} arguments")
    if len(" ".join(argv).encode()) > MAX_RESUME_BYTES:
        raise ParkError("the command is longer than 8 KiB")


def placeholder_argv(placeholder: Path, session_id: str, title: str,
                     parked_at: str, options: list[str]) -> list[str]:
    return ["bash", str(placeholder), session_id, title, parked_at, "--", *options]


# ---------------------------------------------------------------------------
# Herdr and Copilot access
# ---------------------------------------------------------------------------


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"


def herdr_raw(*args: str) -> str:
    proc = subprocess.run([herdr_bin(), *args], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip().splitlines()
        raise ParkError(f"herdr {' '.join(args[:2])} failed: "
                        f"{message[-1] if message else proc.returncode}")
    return proc.stdout


def herdr(*args: str) -> dict:
    """Run a Herdr CLI command and return the "result" of its JSON output."""
    out = herdr_raw(*args).strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("result", {})
    except json.JSONDecodeError:
        return {}


def notify(title: str, body: str = "") -> None:
    args = [herdr_bin(), "notification", "show", title]
    if body:
        args += ["--body", body]
    subprocess.run(args, capture_output=True, timeout=10)


def get_pane(pane_id: str) -> dict:
    return herdr("pane", "get", pane_id)["pane"]


def foreground_processes(pane_id: str) -> list[dict]:
    info = herdr("pane", "process-info", "--pane", pane_id)
    return info.get("process_info", {}).get("foreground_processes", [])


def copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or "~/.copilot").expanduser()


def session_from_process_logs(pids: list[int]) -> str | None:
    for pid in pids:
        for log in copilot_home().glob(f"logs/process-*-{pid}.log"):
            try:
                found = session_from_log(log.read_text(errors="replace"))
            except OSError:
                continue
            if found:
                return found
    return None


def install_placeholder() -> Path:
    """Copy the placeholder to a stable path and return that path.

    Parked panes keep this path in their resume command. The plugin state
    directory does not change when the plugin is updated or moved.
    """
    state_dir = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR")
                     or Path.home() / ".local" / "state" / "herdr-copilot-park")
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "parked.sh"
    source = PLACEHOLDER_SOURCE.read_bytes()
    if not target.exists() or target.read_bytes() != source:
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(source)
        tmp.chmod(0o755)
        tmp.replace(target)
    return target


def wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_SECONDS)
    return predicate()


# ---------------------------------------------------------------------------
# Park
# ---------------------------------------------------------------------------


def park(pane_id: str, placeholder: Path) -> str:
    """Park one pane. Return its title. Raise ParkError if it is not parked."""
    pane = get_pane(pane_id)
    pane_id = pane["pane_id"]
    title = clean_title(pane.get("terminal_title_stripped") or pane.get("terminal_title"))
    agent, state = pane.get("agent"), pane.get("agent_status")
    if agent == PARKED_AGENT:
        raise ParkError("it is already parked")
    if agent != COPILOT_AGENT:
        raise ParkError("it does not run Copilot CLI")
    if state not in READY_STATES:
        raise ParkError(f"Copilot is {state}")

    found = copilot_command(foreground_processes(pane_id))
    if found is None:
        raise ParkError("the Copilot process was not found")
    options, pids = strip_session_options(found[0]), found[1]
    logged_session = session_from_process_logs(pids)

    parked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Check the command before Copilot exits. The session ID is a UUID.
    check_resume_argv(placeholder_argv(placeholder, "0" * 36, title, parked_at, options))

    # Check again just before the exit, because the state can change.
    if get_pane(pane_id).get("agent_status") not in READY_STATES:
        raise ParkError("Copilot started to work")

    # Ctrl+D exits Copilot only when the input box is empty. Thus a draft
    # prompt stops the park, and Copilot keeps the draft.
    herdr("pane", "send-keys", pane_id, "ctrl+d")
    exited = wait_until(
        lambda: copilot_command(foreground_processes(pane_id)) is None,
        EXIT_TIMEOUT_SECONDS,
    )
    if not exited:
        raise ParkError("Copilot did not exit. Clear the input box, then try again")
    # Herdr clears the pane's agent a short time after Copilot exits. Wait for
    # that, so that it does not also clear the report of the placeholder.
    wait_until(lambda: get_pane(pane_id).get("agent") != COPILOT_AGENT, AGENT_CLEAR_SECONDS)

    # The exit summary is at the bottom. A short read keeps an older summary
    # in the scrollback out of the match.
    output = herdr_raw("pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", "40")
    session_id = session_from_exit_summary(output) or logged_session
    if not session_id:
        raise ParkError("Copilot exited, but its session ID was not found. "
                        "Use the resume command that Copilot printed")

    argv = placeholder_argv(placeholder, session_id, title, parked_at, options)
    # The leading space keeps the command out of shell history when the shell
    # ignores commands that start with a space.
    herdr("pane", "run", pane_id, " " + " ".join(shlex.quote(arg) for arg in argv))
    if not wait_until(lambda: get_pane(pane_id).get("agent") == PARKED_AGENT,
                      PLACEHOLDER_TIMEOUT_SECONDS):
        raise ParkError("the placeholder did not start. "
                        f"Load the session with: copilot --resume={session_id}")
    return title


def focused_pane() -> str:
    pane_id = os.environ.get("HERDR_PANE_ID")
    if pane_id:
        return pane_id
    return herdr("pane", "current")["pane"]["pane_id"]


def idle_copilot_panes() -> list[str]:
    agents = herdr("agent", "list").get("agents", [])
    return [a["pane_id"] for a in agents
            if a.get("agent") == COPILOT_AGENT and a.get("agent_status") in READY_STATES]


def run(mode: str) -> int:
    placeholder = install_placeholder()
    if mode == "focused":
        try:
            title = park(focused_pane(), placeholder)
        except ParkError as err:
            notify("Not parked", str(err))
            print(f"not parked: {err}", file=sys.stderr)
            return 1
        print(f"parked: {title}")
        return 0

    targets = idle_copilot_panes()
    parked, failed = [], []
    for pane_id in targets:
        try:
            parked.append(park(pane_id, placeholder))
            print(f"parked {pane_id}: {parked[-1]}")
        except ParkError as err:
            failed.append(f"{pane_id}: {err}")
            print(f"not parked {pane_id}: {err}", file=sys.stderr)
    if not targets:
        notify("Nothing to park", "No Copilot tab is idle.")
    else:
        body = "\n".join(failed[:5]) if failed else ""
        notify(f"Parked {len(parked)} of {len(targets)} idle Copilot tabs", body)
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("focused", "all"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    state_dir = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR")
                     or Path.home() / ".local" / "state" / "herdr-copilot-park")
    state_dir.mkdir(parents=True, exist_ok=True)
    # One park at a time. A second key press while a park runs does nothing.
    with open(state_dir / "park.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            notify("Not parked", "A park is already in progress.")
            return 1
        return run(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
