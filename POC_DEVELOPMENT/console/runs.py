"""Starts ramp.py runs, tracks them on disk, and reports their status.

State lives under runs_dir/<run>/ (state.json, log.txt, summary.json, profile.yaml),
so the console can be restarted without losing history, and a run that outlives
the console is picked up again.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from console import profiles

STATUS_BY_EXIT = {0: "passed", 1: "error", 2: "stop_rule", 3: "instance_failed", 4: "regression"}
RUN_ID_RE = re.compile(r"^Run ID: (\S+)", re.M)
STEP_RE = re.compile(r"^\[(step-\d+)\] launching", re.M)
LOG_CHUNK = 256 * 1024


class RunError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


class RunManager:
    def __init__(self, runs_dir, repo_root, profiles_dir, baselines_dir, command=None):
        """command(snapshot_path, summary_path, save_baseline) -> argv; injectable for tests."""
        self.runs_dir = Path(runs_dir)
        self.repo_root = Path(repo_root)
        self.profiles_dir = Path(profiles_dir)
        self.baselines_dir = Path(baselines_dir)
        self.command = command or self._ramp_command
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._procs = {}

    def _ramp_command(self, snapshot, summary, save_baseline):
        argv = [sys.executable, "-u", str(self.repo_root / "ramp.py"), "--yes", "--summary-json", str(summary)]
        if save_baseline:
            argv.append("--save-baseline")
        return argv + [str(snapshot)]

    def validate(self, yaml_text):
        """(ok, message) from ramp.py's own validator, so the console never disagrees with the CLI."""
        scratch = self.runs_dir / ".validate.yaml"
        scratch.write_text(yaml_text)
        try:
            result = subprocess.run(
                [sys.executable, str(self.repo_root / "ramp.py"), "--validate", str(scratch)],
                capture_output=True, text=True, timeout=30, cwd=self.repo_root)
        finally:
            scratch.unlink(missing_ok=True)
        text = (result.stdout + result.stderr).strip()
        return result.returncode == 0, text

    def _state_path(self, run_id):
        return self.runs_dir / run_id / "state.json"

    def _read_state(self, run_id):
        try:
            return json.loads(self._state_path(run_id).read_text())
        except (OSError, ValueError):
            return None

    def _finalize(self, state, exit_code):
        run_dir = self.runs_dir / state["id"]
        summary = run_dir / "summary.json"
        if summary.exists():
            try:
                exit_code = json.loads(summary.read_text()).get("exit_code", exit_code)
            except ValueError:
                pass
        if state.get("stop_requested"):
            status = "stopped"
        elif exit_code is None:
            status = "lost"
        else:
            status = STATUS_BY_EXIT.get(exit_code, "error")
        state.update(status=status, exit_code=exit_code, ended_at=time.time())
        _write_json(self._state_path(state["id"]), state)

    def _refresh(self, state):
        """If a run marked running has ended, record how it ended. Returns the (updated) state."""
        if state["status"] != "running":
            return state
        proc = self._procs.get(state["id"])
        if proc is not None:
            code = proc.poll()
            if code is None:
                return state
            self._procs.pop(state["id"], None)
            self._finalize(state, code)
        elif not _pid_alive(state["pid"]):
            self._finalize(state, None)
        return state

    def _decorate(self, state):
        log = self._log_text(state["id"])
        match = RUN_ID_RE.search(log)
        state["ramp_run_id"] = match.group(1) if match else None
        steps = STEP_RE.findall(log)
        state["current_step"] = steps[-1] if steps else None
        return state

    def _log_text(self, run_id):
        try:
            return (self.runs_dir / run_id / "log.txt").read_text(errors="replace")
        except OSError:
            return ""

    def active(self):
        for state in self._all_states():
            if self._refresh(state)["status"] == "running":
                return state
        return None

    def _all_states(self):
        states = []
        for child in self.runs_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                state = self._read_state(child.name)
                if state:
                    states.append(state)
        return sorted(states, key=lambda s: s["started_at"], reverse=True)

    def start(self, profile_name, save_baseline=False, confirmed=False):
        path = profiles.safe_profile_path(self.profiles_dir, profile_name)
        if path is None or not path.exists():
            raise RunError(f"No such profile: {profile_name!r}", 404)
        summary = profiles.load_summary(path, self.baselines_dir)
        if "error" in summary:
            raise RunError(summary["error"], 400)
        ok, message = self.validate(path.read_text())
        if not ok:
            raise RunError(message or "Profile is invalid", 400)
        if summary["needs_confirmation"] and not confirmed:
            raise RunError(
                f"This profile launches up to {summary['peak_instances']} JMeter instances at once "
                f"(more than {profiles.LARGE_RUN_INSTANCES}). Confirm to run it.", 428)
        with self._lock:
            if self.active():
                raise RunError("A run is already in progress. Wait for it to finish or stop it.", 409)
            base = time.strftime("run-%Y%m%d-%H%M%S")
            run_id, n = base, 1
            while (self.runs_dir / run_id).exists():  # two runs can start within the same second
                n += 1
                run_id = f"{base}-{n}"
            run_dir = self.runs_dir / run_id
            run_dir.mkdir()
            snapshot = run_dir / "profile.yaml"
            snapshot.write_text(path.read_text())
            summary_path = run_dir / "summary.json"
            log = open(run_dir / "log.txt", "wb")
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen(
                self.command(snapshot, summary_path, save_baseline),
                stdout=log, stderr=subprocess.STDOUT, cwd=self.repo_root, env=env,
                start_new_session=True)
            log.close()
            self._procs[run_id] = proc
            state = {
                "id": run_id, "profile": path.stem, "save_baseline": bool(save_baseline),
                "status": "running", "exit_code": None, "pid": proc.pid,
                "started_at": time.time(), "ended_at": None, "stop_requested": False,
                "total_sec": summary["total_sec"], "steps": summary["steps"],
                "warmup_sec": summary["warmup_sec"],
            }
            _write_json(self._state_path(run_id), state)
        return self._decorate(state)

    def get(self, run_id):
        if not re.fullmatch(r"run-[0-9-]+", run_id):
            return None
        state = self._read_state(run_id)
        if state is None:
            return None
        state = self._decorate(self._refresh(state))
        summary = self.runs_dir / run_id / "summary.json"
        state["summary"] = json.loads(summary.read_text()) if summary.exists() else None
        return state

    def list(self, limit=50):
        return [self._decorate(self._refresh(s)) for s in self._all_states()[:limit]]

    def log(self, run_id, offset=0):
        if self.get(run_id) is None:
            return None
        path = self.runs_dir / run_id / "log.txt"
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(LOG_CHUNK)
        except OSError:
            return {"text": "", "offset": offset}
        return {"text": data.decode("utf-8", errors="replace"), "offset": offset + len(data)}

    def stop(self, run_id):
        state = self.get(run_id)
        if state is None:
            raise RunError("No such run", 404)
        if state["status"] != "running":
            raise RunError("That run is not running", 409)
        state["stop_requested"] = True
        _write_json(self._state_path(run_id), {k: v for k, v in state.items()
                                               if k not in ("summary", "ramp_run_id", "current_step")})
        try:
            os.kill(state["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        return self.get(run_id)
