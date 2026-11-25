"""Long-lived program sessions for interactive stdin/stdout.

The normal ``/api/run`` endpoint intentionally runs a program to completion:
it sends all stdin at once and returns all output in one JSON response.  That
is useful for tests, but it cannot behave like a terminal.  This module keeps a
native executable alive so the UI can stream output as it is produced and send
more stdin only when the program asks for it.

Sessions are held in memory by :class:`RunSessionManager`.  The HTTP layer
exposes start/poll/input/stop endpoints around them.
"""
import codecs
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


def interactive_command(executable):
    """Return the command used to run ``executable`` interactively.

    When a program writes to a pipe, libc buffers stdout in blocks.  A prompt
    without ``endl`` can therefore stay invisible even though the program is
    waiting for input.  On Linux, ``stdbuf`` makes stdout/stderr unbuffered so
    the UI sees prompts immediately.  Falling back to the plain executable
    keeps the session working on platforms without ``stdbuf``.
    """
    command = [str(executable)]
    if os.name == "posix":
        stdbuf = shutil.which("stdbuf")
        if stdbuf:
            return [stdbuf, "-o0", "-e0", str(executable)]
    return command


class InteractiveRunSession:
    """One running executable with a streamed event log."""

    def __init__(self, command: List[str], timeout: int = 10,
                 cwd: Optional[str] = None, executable: Optional[str] = None):
        self.id = uuid.uuid4().hex
        self.command = [str(part) for part in command]
        self.timeout = max(1, int(timeout))
        self.started_at = time.monotonic()
        self.finished_at: Optional[float] = None
        self.timed_out = False
        self.stopped = False

        self._lock = threading.Lock()
        self._events: List[dict] = []
        self._next_event = 0
        self._stdout: List[str] = []
        self._stderr: List[str] = []
        self._stdin_closed = False
        self._readers_done = 0
        self._readers: List[threading.Thread] = []
        self.process: Optional[subprocess.Popen] = None

        env = dict(os.environ)
        if executable:
            env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
        elif self.command:
            env["PATH"] = str(Path(self.command[0]).parent) + os.pathsep + env.get("PATH", "")

        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except OSError as exc:
            self._emit("system", "Could not start the program: {}".format(exc))
            self._mark_finished()
            return

        for stream, name in ((self.process.stdout, "stdout"),
                             (self.process.stderr, "stderr")):
            if stream is None:
                continue
            reader = threading.Thread(
                target=self._read_stream, args=(stream, name), daemon=True,
                name="compiled-run-{}".format(name))
            reader.start()
            self._readers.append(reader)

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------
    def _emit(self, stream: str, text: str):
        if not text:
            return
        with self._lock:
            self._next_event += 1
            self._events.append({
                "id": self._next_event,
                "stream": stream,
                "text": text,
            })
            if stream == "stdout":
                self._stdout.append(text)
            elif stream == "stderr":
                self._stderr.append(text)

    def _read_stream(self, stream, name):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                # read1() returns whatever is currently available.  read(1024)
                # waits for a full buffer and would delay short prompts.
                chunk = stream.read1(1024) if hasattr(stream, "read1") else stream.read(1024)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self._emit(name, text)
            tail = decoder.decode(b"", final=True)
            if tail:
                self._emit(name, tail)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            with self._lock:
                self._readers_done += 1

    def _close_stdin_pipe(self):
        if self.process is not None and self.process.stdin is not None:
            try:
                if not self.process.stdin.closed:
                    self.process.stdin.close()
            except (OSError, ValueError):
                pass

    def _mark_finished(self):
        self._close_stdin_pipe()
        with self._lock:
            if self.finished_at is None:
                self.finished_at = time.monotonic()

    # ------------------------------------------------------------------
    # Process control
    # ------------------------------------------------------------------
    def send(self, text: str) -> bool:
        """Write ``text`` to the program's stdin.  Returns False on EOF."""
        if self.process is None or self.process.poll() is not None:
            return False
        text = "" if text is None else str(text)
        with self._lock:
            if self._stdin_closed:
                return False
        if text:
            # A real terminal echoes input before the program consumes it.
            self._emit("stdin", text)
        try:
            if self.process.stdin is not None:
                self.process.stdin.write(text.encode("utf-8"))
                self.process.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def close_stdin(self):
        """Send EOF so programs using getline() can finish."""
        with self._lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
        self._emit("system", "Input closed (EOF).")
        self._close_stdin_pipe()

    def stop(self, announce: bool = True):
        """Terminate the program and drain its remaining output."""
        if self.stopped:
            return
        self.stopped = True
        if announce:
            self._emit("system", "Program stopped.")
        self.close_stdin()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=0.5)
            except (OSError, subprocess.SubprocessError):
                pass
        self._drain()
        self._mark_finished()

    def _check_timeout(self):
        if self.process is None or self.process.poll() is not None:
            self._mark_finished()
            return
        if time.monotonic() - self.started_at <= self.timeout:
            return
        self.timed_out = True
        self._emit("system", "Execution time limit reached; program stopped.")
        self.stop(announce=False)

    def _drain(self):
        for reader in self._readers:
            if reader.is_alive():
                reader.join(timeout=0.3)

    # ------------------------------------------------------------------
    # Snapshot for HTTP responses
    # ------------------------------------------------------------------
    def snapshot(self, after: int = 0) -> dict:
        self._check_timeout()
        exited = self.process is None or self.process.poll() is not None
        if exited:
            self._drain()
            self._mark_finished()

        with self._lock:
            events = [dict(event) for event in self._events if event["id"] > after]
            stdout = "".join(self._stdout)
            stderr = "".join(self._stderr)
            next_cursor = self._next_event

        returncode = self.process.poll() if self.process is not None else None
        log = ""
        if self.timed_out:
            log = "Program exceeded the {}s execution limit.".format(self.timeout)
        elif self.stopped:
            log = "Program was stopped."

        return {
            "id": self.id,
            "next": next_cursor,
            "status": "exited" if exited else "running",
            "events": events,
            "returncode": returncode,
            "timed_out": self.timed_out,
            "log": log,
            "execution": {
                "ok": bool(exited and not self.timed_out and not self.stopped
                           and returncode == 0),
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "log": log,
            } if exited else None,
        }


class RunSessionManager:
    """Thread-safe registry of interactive program sessions."""

    def __init__(self, ttl: int = 600):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._sessions: Dict[str, InteractiveRunSession] = {}

    def create(self, command: List[str], timeout: int = 10,
               cwd: Optional[str] = None,
               executable: Optional[str] = None) -> InteractiveRunSession:
        self.cleanup()
        session = InteractiveRunSession(command, timeout=timeout, cwd=cwd,
                                        executable=executable)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Optional[InteractiveRunSession]:
        self.cleanup()
        with self._lock:
            return self._sessions.get(session_id)

    def cleanup(self):
        now = time.monotonic()
        expired = []
        with self._lock:
            sessions = list(self._sessions.items())
        for session_id, session in sessions:
            session._check_timeout()
            if session.finished_at is not None and now - session.finished_at > self.ttl:
                expired.append(session_id)
        if expired:
            with self._lock:
                for session_id in expired:
                    self._sessions.pop(session_id, None)
