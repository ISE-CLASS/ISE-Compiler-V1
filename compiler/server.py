"""Local HTTP service for COMPILED.

Run it from the repository root::

    python -m compiler.server
    python -m compiler.server --port 8000 --open

It exposes the JSON API expected by ``ui/README.md`` and, when
``ui/dist`` exists, serves the built React app from the same origin.  That
means the UI can auto-connect to ``http://localhost:8000`` without any
environment configuration.

The service is intentionally a local development tool: it compiles and runs
submitted C++ code with the host toolchain.  It binds to ``127.0.0.1`` by
default and must not be exposed to untrusted networks without process
isolation, authentication and resource limits.
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import compile_source
from .run_session import RunSessionManager, interactive_command
from .toolchain import detect_toolchain

ROOT = Path(__file__).resolve().parent.parent
UI_DIST = ROOT / "ui" / "dist"
MAX_BODY = 2 * 1024 * 1024
SESSIONS = RunSessionManager()
RUN_POLL_PATH = re.compile(r"^/api/run/([^/]+)/poll$")
RUN_INPUT_PATH = re.compile(r"^/api/run/([^/]+)/input$")
RUN_STOP_PATH = re.compile(r"^/api/run/([^/]+)/stop$")

POST_STAGES = {
    "/api/compile": "compile",
    "/api/run": "run",
    "/api/tokenize": "tokenize",
    "/api/parse": "parse",
    "/api/semantic": "semantic",
    "/api/optimize": "optimize",
    "/api/ir": "ir",
    "/api/assembly": "assembly",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".txt": "text/plain; charset=utf-8",
}


class CompilerHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "COMPILED/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # CORS and helpers
    # ------------------------------------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON: {}".format(exc))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    @staticmethod
    def _timeout(payload):
        try:
            return max(1, min(60, int(payload.get("timeout", 10))))
        except (TypeError, ValueError):
            return 10

    def _after(self, source):
        try:
            if isinstance(source, dict):
                value = source.get("after", 0)
            else:
                query = parse_qs(urlparse(self.path).query)
                value = query.get("after", ["0"])[0]
            return max(0, int(value))
        except (TypeError, ValueError, IndexError):
            return 0

    def _missing_session(self):
        self._send_json(404, {"ok": False, "diagnostics": ["run session not found"]})

    # ------------------------------------------------------------------
    # Interactive run sessions
    # ------------------------------------------------------------------
    def _start_run_session(self):
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "diagnostics": [str(exc)]})
            return

        source = str(payload.get("source", "") or "")
        filename = str(payload.get("filename") or "main.cpp")
        optimize = bool(payload.get("optimize", True))
        timeout = self._timeout(payload)

        try:
            result = compile_source(
                source,
                filename=filename,
                stage="compile",
                optimize=optimize,
                stdin="",
                run=False,
                timeout=timeout,
            )
        except Exception as exc:
            self._send_json(500, {
                "ok": False,
                "stage": "run",
                "diagnostics": ["Internal compiler error: {}".format(exc)],
                "output": "Internal compiler error: {}".format(exc),
            })
            return

        build = result.get("build") or {}
        executable = build.get("executable")
        if not result.get("ok") or not build.get("ok") or not executable:
            result["session"] = None
            self._send_json(200, result)
            return

        session = SESSIONS.create(
            interactive_command(executable),
            timeout=timeout,
            cwd=str(Path(executable).parent),
            executable=str(executable),
        )
        result["session"] = {"id": session.id, "status": "running"}
        result["output"] = "Program started. Type input below when it waits for cin."
        self._send_json(200, result)

    def _run_session_input(self, session_id):
        session = SESSIONS.get(session_id)
        if session is None:
            self._missing_session()
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "diagnostics": [str(exc)]})
            return
        text = payload.get("input", "")
        session.send("" if text is None else str(text))
        if bool(payload.get("close", False)):
            session.close_stdin()
        self._send_json(200, session.snapshot(after=self._after(payload)))

    def _run_session_poll(self, session_id):
        session = SESSIONS.get(session_id)
        if session is None:
            self._missing_session()
            return
        self._send_json(200, session.snapshot(after=self._after(None)))

    def _run_session_stop(self, session_id):
        session = SESSIONS.get(session_id)
        if session is None:
            self._missing_session()
            return
        session.stop()
        self._send_json(200, session.snapshot())

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/api/health", "/api"):
            self._send_json(200, {
                "ok": True,
                "service": "COMPILED",
                "version": "1.0",
                "stages": list(POST_STAGES.values()),
                "toolchain": (detect_toolchain().to_dict()
                              if detect_toolchain() else None),
                "ui": str(UI_DIST) if (UI_DIST / "index.html").exists() else None,
            })
            return
        if path == "/api/toolchain":
            toolchain = detect_toolchain()
            self._send_json(200, {
                "ok": True,
                "toolchain": toolchain.to_dict() if toolchain else None,
            })
            return
        if path == "/api/stages":
            self._send_json(200, {"ok": True, "stages": list(POST_STAGES.values())})
            return
        match = RUN_POLL_PATH.match(path)
        if match:
            self._run_session_poll(match.group(1))
            return
        self._serve_static(path)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/run/start":
            self._start_run_session()
            return
        match = RUN_INPUT_PATH.match(path)
        if match:
            self._run_session_input(match.group(1))
            return
        match = RUN_STOP_PATH.match(path)
        if match:
            self._run_session_stop(match.group(1))
            return
        stage = POST_STAGES.get(path)
        if stage is None:
            self._send_json(404, {"ok": False, "diagnostics": ["unknown endpoint"]})
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "diagnostics": [str(exc)]})
            return

        source = payload.get("source", "")
        filename = payload.get("filename") or "main.cpp"
        stdin = payload.get("stdin") or ""
        optimize = payload.get("optimize", True)
        timeout = payload.get("timeout", 10)
        try:
            timeout = max(1, min(60, int(timeout)))
        except (TypeError, ValueError):
            timeout = 10

        try:
            result = compile_source(
                source,
                filename=str(filename),
                stage=stage,
                optimize=bool(optimize),
                stdin=str(stdin),
                timeout=timeout,
            )
        except Exception as exc:  # never return an HTML traceback
            self._send_json(500, {
                "ok": False,
                "stage": stage,
                "diagnostics": ["Internal compiler error: {}".format(exc)],
                "output": "Internal compiler error: {}".format(exc),
            })
            return
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # Static UI
    # ------------------------------------------------------------------
    def _serve_static(self, path):
        if not (UI_DIST / "index.html").exists():
            self._send_json(200, {
                "ok": True,
                "service": "COMPILED",
                "message": "API is running. Build the UI with 'cd ui; npm run build' "
                           "or use the Vite dev server and set VITE_COMPILER_API_URL.",
                "ui": None,
            })
            return
        relative = path.lstrip("/") or "index.html"
        candidate = (UI_DIST / relative).resolve()
        try:
            candidate.relative_to(UI_DIST.resolve())
        except ValueError:
            self._send_json(403, {"ok": False, "diagnostics": ["forbidden path"]})
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.exists():
            candidate = UI_DIST / "index.html"
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_json(404, {"ok": False, "diagnostics": ["not found"]})
            return
        content_type = CONTENT_TYPES.get(
            candidate.suffix.lower(),
            mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate.suffix == ".html"
                         else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if os.environ.get("COMPILED_QUIET"):
            return
        sys.stderr.write("[compiled] {}\n".format(format % args))


def main(argv=None):
    default_host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    default_port = int(os.environ.get("PORT", "8000"))

    parser = argparse.ArgumentParser(description="COMPILED compiler HTTP service")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--open", action="store_true", help="open the UI in a browser")
    parser.add_argument("--quiet", action="store_true", help="suppress request logging")
    args = parser.parse_args(argv)

    if args.quiet:
        os.environ["COMPILED_QUIET"] = "1"

    server = ThreadingHTTPServer((args.host, args.port), CompilerHTTPRequestHandler)
    server.daemon_threads = True
    url = "http://{}:{}/".format(
        "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host,
        args.port)
    toolchain = detect_toolchain()
    print("COMPILED compiler service")
    print("  API:       {}/api".format(url.rstrip("/")))
    print("  UI:        {}".format(str(UI_DIST) if (UI_DIST / "index.html").exists()
                                   else "(run 'cd ui; npm run build')"))
    print("  Toolchain: {}".format(toolchain.version if toolchain else "not found"))
    print("Press Ctrl+C to stop.")
    if args.open and not os.environ.get("PORT"):
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping COMPILED.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
