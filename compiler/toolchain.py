"""Host toolchain detection and native assembly/link/run helpers.

The compiler always emits x86-64 GNU assembler syntax (Intel form).  This
module finds a compiler driver that can turn that assembly into a native
executable on the machine running the service:

    g++ / gcc / clang++  (MinGW-w64, Linux, macOS)
    cl                   (MSVC, no GNU assembly support)

If nothing suitable is installed the assembly is still returned to the UI and
the diagnostic explains how to enable native execution.
"""
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Toolchain:
    name: str
    path: str
    version: str = ""
    kind: str = "gnu"          # gnu, msvc
    target: str = ""

    @property
    def is_gnu(self):
        return self.kind == "gnu"

    @property
    def supports_assembly(self):
        return self.kind == "gnu"

    def to_dict(self):
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "kind": self.kind,
            "target": self.target,
            "supports_assembly": self.supports_assembly,
        }


CANDIDATES = (
    ("g++", "gnu"),
    ("clang++", "gnu"),
    ("c++", "gnu"),
    ("gcc", "gnu"),
    ("clang", "gnu"),
    ("cl", "msvc"),
)


def _version(path, kind):
    try:
        if kind == "msvc":
            completed = subprocess.run(
                [path], capture_output=True, text=True, timeout=8,
                errors="replace",
            )
            text = (completed.stdout or "") + (completed.stderr or "")
        else:
            completed = subprocess.run(
                [path, "--version"], capture_output=True, text=True,
                timeout=8, errors="replace",
            )
            text = (completed.stdout or "").splitlines()
            text = text[0] if text else ""
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return ""


def _target(path):
    """Return the default target triple/architecture reported by the driver."""
    try:
        completed = subprocess.run(
            [path, "-dumpmachine"], capture_output=True, text=True,
            timeout=8, errors="replace",
        )
        return (completed.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_toolchain(preferred: Optional[str] = None) -> Optional[Toolchain]:
    """Find an installed compiler driver, preferring ``preferred``."""
    order = list(CANDIDATES)
    if preferred:
        lower = preferred.lower()
        order.sort(key=lambda item: 0 if item[0] == lower else 1)
    for name, kind in order:
        path = shutil.which(name)
        if not path:
            continue
        target = _target(path) if kind == "gnu" else platform.machine()
        return Toolchain(
            name=name,
            path=path,
            version=_version(path, kind),
            kind=kind,
            target=target,
        )
    return None


def host_architecture_supported():
    machine = platform.machine().lower()
    return machine in ("x86_64", "amd64", "x64", "i386", "i686")


def assembler_command(toolchain: Toolchain, asm_path, exe_path) -> List[str]:
    if toolchain.kind == "gnu":
        command = [toolchain.path, "-O0", "-o", str(exe_path), str(asm_path)]
        if platform.system().lower() == "windows":
            # Avoid requiring MinGW runtime DLLs on PATH when the test binary runs.
            command.insert(1, "-static")
        return command
    raise ValueError("MSVC 'cl' cannot assemble GNU Intel-syntax output")


def build_assembly(assembly, toolchain, out_dir=None, timeout=60):
    """Assemble and link ``assembly``.  Returns a result dictionary."""
    if toolchain is None:
        return {
            "ok": False,
            "toolchain": None,
            "executable": None,
            "log": "No native toolchain (g++, clang++ or gcc) was found on PATH.",
        }
    if not toolchain.supports_assembly:
        return {
            "ok": False,
            "toolchain": toolchain.to_dict(),
            "executable": None,
            "log": "{} is installed but cannot assemble GNU x86-64 output. "
                   "Install MinGW-w64, g++ or clang++.".format(toolchain.name),
        }
    if not host_architecture_supported() and not any(
            token in toolchain.target.lower() for token in ("x86_64", "amd64", "i386", "i686")):
        return {
            "ok": False,
            "toolchain": toolchain.to_dict(),
            "executable": None,
            "log": "Generated assembly targets x86-64, but this machine reports '{}'.".format(
                platform.machine()),
        }

    directory = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="compiled-"))
    directory.mkdir(parents=True, exist_ok=True)
    import uuid
    stem = "program-{}".format(uuid.uuid4().hex[:10])
    asm_path = directory / (stem + ".s")
    exe_name = stem + (".exe" if platform.system().lower() == "windows" else "")
    exe_path = directory / exe_name
    asm_path.write_text(assembly, encoding="utf-8")
    command = assembler_command(toolchain, asm_path, exe_path)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            cwd=str(directory),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "toolchain": toolchain.to_dict(),
            "executable": None,
            "log": "Could not run {}: {}".format(toolchain.name, exc),
        }
    log = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return {
        "ok": completed.returncode == 0 and exe_path.exists(),
        "toolchain": toolchain.to_dict(),
        "executable": str(exe_path) if completed.returncode == 0 and exe_path.exists() else None,
        "log": log,
        "command": command,
        "assembly_path": str(asm_path),
    }


def run_executable(executable, stdin_text="", timeout=10):
    """Run a built executable with optional stdin.  Never raises."""
    if not executable:
        return {"ok": False, "stdout": "", "stderr": "", "returncode": None,
                "log": "No executable to run."}
    env = dict(os.environ)
    if executable:
        env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            [executable],
            input=stdin_text or "",
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "",
                "returncode": None,
                "log": "Program exceeded the {}s execution limit.".format(timeout)}
    except OSError as exc:
        return {"ok": False, "stdout": "", "stderr": "", "returncode": None,
                "log": "Could not execute the program: {}".format(exc)}
    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "returncode": completed.returncode,
        "log": "",
    }
