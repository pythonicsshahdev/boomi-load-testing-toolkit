"""HTTP server for the load testing console. Standard library only (plus PyYAML via profiles)."""
import argparse
import hmac
import ipaddress
import json
import mimetypes
import os
import subprocess
import sys
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from console import extras, profiles
from console.runs import RunError, RunManager

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
COOKIE_NAME = "console_token"
MAX_BODY = 1024 * 1024
MIN_TOKEN_LEN = 16
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


# --- access rules (pure functions, unit tested) -------------------------------------------

def is_loopback(host):
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def startup_problem(bind_host, token):
    """Why the server must not start with these settings, or None if they're fine."""
    if token is not None and len(token) < MIN_TOKEN_LEN:
        return f"CONSOLE_TOKEN must be at least {MIN_TOKEN_LEN} characters."
    if not is_loopback(bind_host) and not token:
        return (f"Refusing to listen on {bind_host}: this console can start load against real systems, "
                f"so anything other than localhost needs CONSOLE_TOKEN set (at least {MIN_TOKEN_LEN} characters).")
    return None


def _hostname(host_header):
    host = (host_header or "").strip()
    if host.startswith("["):
        return host[1:].split("]")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header, bind_host, extra_hosts=()):
    """Blocks DNS-rebinding: on a loopback bind only localhost names are accepted."""
    name = _hostname(host_header).lower()
    if name in {h.lower() for h in extra_hosts}:
        return True
    if is_loopback(bind_host):
        return is_loopback(name)
    return bool(name)


def origin_ok(origin, host_header):
    """A browser-sent Origin must be this same server; absent Origin (curl, scripts) is fine."""
    if not origin:
        return True
    return urlparse(origin).netloc.lower() == (host_header or "").lower()


def token_ok(provided, expected):
    if not expected:
        return True
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


# --- application ----------------------------------------------------------------------------

class App:
    def __init__(self, manager, token=None, bind_host="127.0.0.1", extra_hosts=(), grafana_url=None):
        self.manager = manager
        self.token = token
        self.bind_host = bind_host
        self.extra_hosts = tuple(extra_hosts)
        self.grafana_url = grafana_url
        dash = REPO_ROOT / "docker" / "grafana" / "dashboards" / "jmeter-performance.json"
        try:
            self.dashboard_uid = json.loads(dash.read_text()).get("uid")
        except (OSError, ValueError):
            self.dashboard_uid = None


class Handler(BaseHTTPRequestHandler):
    server_version = "BoomiLoadConsole"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass  # the UI polls every second or two; per-request logging is just noise

    # -- helpers --
    def _send(self, status, body, content_type="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message, **extra):
        self._send(status, {"error": message, **extra})

    def _provided_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None

    def _json_body(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise RunError("Content-Type must be application/json", 415)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise RunError("Request body too large", 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise RunError("Body is not valid JSON", 400)
        if not isinstance(body, dict):
            raise RunError("Body must be a JSON object", 400)
        return body

    # -- dispatch --
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        try:
            if not host_allowed(self.headers.get("Host"), self.app.bind_host, self.app.extra_hosts):
                return self._error(HTTPStatus.FORBIDDEN, "Host not allowed")
            if method != "GET" and not origin_ok(self.headers.get("Origin"), self.headers.get("Host")):
                return self._error(HTTPStatus.FORBIDDEN, "Cross-origin request refused")
            url = urlparse(self.path)
            path = url.path
            if path == "/builder.html" and method == "GET":
                return self._send(200, (REPO_ROOT / "ramp-profile-builder.html").read_bytes(), "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._static(path) if method == "GET" else self._error(HTTPStatus.NOT_FOUND, "Not found")
            if path == "/api/config" and method == "GET":
                return self._send(200, self._config())
            if path == "/api/login" and method == "POST":
                return self._login()
            if not token_ok(self._provided_token(), self.app.token):
                return self._error(HTTPStatus.UNAUTHORIZED, "Token required")
            self._api(method, path, parse_qs(url.query))
        except RunError as e:
            self._error(e.status, str(e))
        except Exception as e:  # keep the server up; report instead of dropping the connection
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def _config(self):
        return {
            "auth_required": bool(self.app.token),
            "grafana_url": self.app.grafana_url,
            "dashboard_uid": self.app.dashboard_uid,
            "large_run_instances": profiles.LARGE_RUN_INSTANCES,
        }

    def _login(self):
        body = self._json_body()
        if not token_ok(body.get("token"), self.app.token):
            time.sleep(0.5)
            return self._error(HTTPStatus.UNAUTHORIZED, "Wrong token")
        cookie = f"{COOKIE_NAME}={body.get('token', '')}; HttpOnly; SameSite=Strict; Path=/"
        self._send(200, {"ok": True}, headers={"Set-Cookie": cookie})

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else unquote(path).lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "Not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))

    def _api(self, method, path, query):
        mgr = self.app.manager
        parts = path.strip("/").split("/")[1:]  # drop "api"
        if parts == ["profiles"] and method == "GET":
            return self._send(200, profiles.list_profiles(mgr.profiles_dir, mgr.baselines_dir))
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "precheck" and method == "GET":
            path = profiles.safe_profile_path(mgr.profiles_dir, parts[1])
            if path is None or not path.exists():
                raise RunError("No such profile", 404)
            return self._send(200, {"items": extras.precheck(path, REPO_ROOT / ".env")})
        if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "try" and method == "POST":
            return self._try_request(parts[1])
        if len(parts) == 2 and parts[0] == "profiles":
            return self._profile(method, parts[1])
        if parts == ["validate"] and method == "POST":
            ok, message = mgr.validate(str(self._json_body().get("yaml", "")))
            return self._send(200, {"ok": ok, "message": message})
        if parts == ["runs"] and method == "GET":
            return self._send(200, mgr.list())
        if parts == ["runs"] and method == "POST":
            body = self._json_body()
            state = mgr.start(str(body.get("profile", "")), bool(body.get("save_baseline")), bool(body.get("confirmed")))
            return self._send(201, state)
        if len(parts) >= 2 and parts[0] == "runs":
            return self._run(method, parts[1], parts[2:], query)
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _profile(self, method, name):
        mgr = self.app.manager
        path = profiles.safe_profile_path(mgr.profiles_dir, name)
        if path is None:
            raise RunError("Invalid profile name (letters, digits, '.', '_' and '-' only)", 400)
        if method == "GET":
            if not path.exists():
                raise RunError("No such profile", 404)
            return self._send(200, {"name": path.stem, "yaml": path.read_text(),
                                    **profiles.load_summary(path, mgr.baselines_dir)})
        if method == "PUT":
            body = self._json_body()
            text = str(body.get("yaml", ""))
            ok, message = mgr.validate(text)
            if not ok:
                raise RunError(message or "Profile is invalid", 400)
            if path.exists() and not body.get("overwrite"):
                raise RunError(f"Profile {path.stem!r} already exists. Choose another name or confirm overwrite.", 409)
            mgr.profiles_dir.mkdir(exist_ok=True)
            path.write_text(text if text.endswith("\n") else text + "\n")
            return self._send(200, {"name": path.stem, **profiles.load_summary(path, mgr.baselines_dir)})
        if method == "DELETE":
            return self._send(200, {"deleted": mgr.delete_profile(name)})
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")

    def _try_request(self, name):
        mgr = self.app.manager
        path = profiles.safe_profile_path(mgr.profiles_dir, name)
        if path is None or not path.exists():
            raise RunError("No such profile", 404)
        try:
            proc = subprocess.run([sys.executable, str(REPO_ROOT / "ramp.py"), "--try-request", str(path)],
                                  capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            result = {"ok": False, "message": "The test request did not finish. Is Docker running?"}
        self._send(200, result)

    def _run(self, method, run_id, rest, query):
        mgr = self.app.manager
        if method == "GET" and not rest:
            state = mgr.get(run_id)
            if state is None:
                raise RunError("No such run", 404)
            return self._send(200, state)
        if method == "GET" and rest == ["log"]:
            chunk = mgr.log(run_id, int(query.get("offset", ["0"])[0]))
            if chunk is None:
                raise RunError("No such run", 404)
            return self._send(200, chunk)
        if method == "GET" and rest == ["live"]:
            state = mgr.get(run_id)
            if state is None:
                raise RunError("No such run", 404)
            return self._send(200, extras.live_metrics(state.get("ramp_run_id")) or {})
        if method == "PUT" and rest == ["note"]:
            return self._send(200, {"note": mgr.set_note(run_id, self._json_body().get("note", ""))})
        if method == "POST" and rest == ["stop"]:
            return self._send(200, mgr.stop(run_id))
        self._error(HTTPStatus.NOT_FOUND, "Not found")


def build_server(host, port, app):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="console", description="Web console for the Boomi load testing toolkit.")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (default: localhost only)")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--grafana-url", help="public Grafana URL for result links (default: this host, port 3004)")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="extra Host header name to accept (repeatable), e.g. the VM's DNS name")
    parser.add_argument("--runs-dir", default=str(REPO_ROOT / "console" / "runs"))
    args = parser.parse_args(argv)

    token = os.environ.get("CONSOLE_TOKEN") or None
    problem = startup_problem(args.host, token)
    if problem:
        print(problem, file=sys.stderr)
        sys.exit(1)

    manager = RunManager(args.runs_dir, REPO_ROOT, REPO_ROOT / "profiles", REPO_ROOT / "baselines")
    app = App(manager, token, args.host, args.allow_host, args.grafana_url)
    server = build_server(args.host, args.port, app)
    scope = "this machine only" if is_loopback(args.host) else "token required"
    print(f"Load testing console: http://{args.host}:{args.port}  ({scope})", flush=True)
    print("Stop with Ctrl+C. A run in progress keeps going without the console; reopen it to pick it back up.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nConsole stopped.")


if __name__ == "__main__":
    main()
