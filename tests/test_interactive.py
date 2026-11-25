"""Tests for interactive stdin/stdout program sessions."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from compiler import server as server_module  # noqa: E402
from compiler.run_session import RunSessionManager  # noqa: E402


class InteractiveRunSessionTests(unittest.TestCase):
    def _write_script(self, source):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8")
        try:
            handle.write(source)
            handle.flush()
        finally:
            handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.remove(handle.name))
        return handle.name

    def _wait_for(self, session, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = session.snapshot()
            text = "".join(event["text"] for event in last["events"])
            if predicate(last, text):
                return last, text
            time.sleep(0.02)
        self.fail("timed out waiting for session output: {}".format(last))

    def test_program_waits_until_input_is_sent(self):
        script = self._write_script(
            "import sys\n"
            "print('Name?', flush=True)\n"
            "line = sys.stdin.readline()\n"
            "print('Hello ' + line.strip(), flush=True)\n"
        )
        manager = RunSessionManager()
        session = manager.create([sys.executable, script], timeout=5)

        snapshot, text = self._wait_for(session, lambda snap, out: "Name?" in out)
        self.assertEqual(snapshot["status"], "running")
        self.assertNotIn("Hello", text)

        self.assertTrue(session.send("Ada\n"))
        snapshot, text = self._wait_for(
            session,
            lambda snap, out: "Hello Ada" in out and snap["status"] == "exited",
        )
        self.assertEqual(snapshot["returncode"], 0)
        self.assertTrue(snapshot["execution"]["ok"])
        self.assertTrue(any(event["stream"] == "stdin" for event in snapshot["events"]))
        self.assertIn("Ada\n", text)

    def test_eof_lets_getline_loop_finish(self):
        script = self._write_script(
            "import sys\n"
            "print('Lines:', flush=True)\n"
            "for line in sys.stdin:\n"
            "    print(line.strip().upper(), flush=True)\n"
            "print('Done', flush=True)\n"
        )
        manager = RunSessionManager()
        session = manager.create([sys.executable, script], timeout=5)

        _, _ = self._wait_for(session, lambda snap, out: "Lines:" in out)
        self.assertTrue(session.send("one\n"))
        session.close_stdin()

        snapshot, text = self._wait_for(
            session,
            lambda snap, out: "Done" in out and snap["status"] == "exited",
        )
        self.assertEqual(snapshot["returncode"], 0)
        self.assertIn("ONE", text)
        self.assertIn("Input closed (EOF).", text)


class InteractiveHTTPTests(InteractiveRunSessionTests):
    def _request(self, base, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_http_start_poll_input_and_stop(self):
        script = self._write_script(
            "import sys\n"
            "print('Prompt:', flush=True)\n"
            "line = sys.stdin.readline()\n"
            "print('Got ' + line.strip(), flush=True)\n"
        )
        fake_result = {
            "ok": True,
            "stage": "compile",
            "build": {"ok": True, "executable": script},
            "stages": [],
            "diagnostics": [],
            "output": "",
        }
        previous_quiet = os.environ.get("COMPILED_QUIET")
        os.environ["COMPILED_QUIET"] = "1"

        def restore_quiet():
            if previous_quiet is None:
                os.environ.pop("COMPILED_QUIET", None)
            else:
                os.environ["COMPILED_QUIET"] = previous_quiet

        self.addCleanup(restore_quiet)
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), server_module.CompilerHTTPRequestHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = "http://127.0.0.1:{}".format(httpd.server_port)

        with patch.object(server_module, "compile_source", return_value=fake_result), \
                patch.object(server_module, "interactive_command",
                             return_value=[sys.executable, script]):
            started = self._request(base, "/api/run/start", {"source": "int main(){}"})
            session_id = started["session"]["id"]

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = self._request(base, "/api/run/{}/poll".format(session_id))
                text = "".join(event["text"] for event in snapshot["events"])
                if "Prompt:" in text:
                    break
                time.sleep(0.02)
            else:
                self.fail("HTTP poll never showed the prompt")

            snapshot = self._request(
                base,
                "/api/run/{}/input".format(session_id),
                {"input": "Ada\n"},
            )
            text = "".join(event["text"] for event in snapshot["events"])
            self.assertIn("Ada\n", text)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = self._request(base, "/api/run/{}/poll".format(session_id))
                text = "".join(event["text"] for event in snapshot["events"])
                if "Got Ada" in text:
                    break
                time.sleep(0.02)
            self.assertIn("Got Ada", text)

            stopped = self._request(
                base, "/api/run/{}/stop".format(session_id), {})
            self.assertEqual(stopped["status"], "exited")


if __name__ == "__main__":
    unittest.main()
