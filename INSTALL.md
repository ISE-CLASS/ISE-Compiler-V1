# Install & Run Guide — ISE-Compiler-V1 (COMPILED)

This repository contains a C++-subset compiler pipeline implemented in Python (tokenizer, parser, semantic analysis, optimizer, IR, x86-64 assembly/code generation, build/run integration) plus a React/TypeScript web UI.

## Contents
- [Prerequisites](#prerequisites)
- [Get the source](#get-the-source)
- [Python setup](#python-setup)
- [Run the compiler (CLI)](#run-the-compiler-cli)
- [Run tests](#run-tests)
- [Run the compiler API server](#run-the-compiler-api-server)
- [Run the web UI](#run-the-web-ui)
  - [Option A: Use the built UI served by Python](#option-a-use-the-built-ui-served-by-python)
  - [Option B: UI dev server (Vite) + API server](#option-b-ui-dev-server-vite--api-server)
- [Deploy with Docker / Render](#deploy-with-docker--render)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required
- **Python**: 3.10+ recommended (3.12 used in Docker). The core project is standard-library based.
  - Check: `python --version` (Windows) or `python3 --version`
- **Git** (to clone/pull updates)

### For compiling/running generated native programs
To go beyond diagnostics/IR/assembly and actually assemble/link/execute generated code, you need a host C++ toolchain:
- **Windows**: MinGW-w64 `g++`/`gcc` (or a compatible toolchain) available in `PATH`
- **Linux/macOS**: `g++`, `gcc`, or `clang` in `PATH`

The pipeline can still produce tokens/AST/semantic results/IR/assembly without a native toolchain; build/run steps will report toolchain unavailability or build issues.

### For the web UI
- **Node.js**: 18+ recommended (Docker build uses Node 20)
- **npm** (comes with Node)

## Get the source

```bash
git clone https://github.com/ISE-CLASS/ISE-Compiler-V1.git
cd ISE-Compiler-V1
```

If you already cloned it:
```bash
git pull origin main
```

## Python setup

No third-party pip packages are required for the core compiler/server.

```bash
# Optional: create and activate a virtual environment
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux/macOS
python3 -m venv .venv
source .venv/bin/activate

# Install Python deps metadata (none required; optional extras are commented)
pip install -r requirements.txt
```

> `cpp_tokenizer.py` also has an optional `graphviz` export helper; install `graphviz` Python package and Graphviz system binaries only if you need diagram export.

## Run the compiler (CLI)

Run from the repository root (or ensure `ISE-Compiler-V1` is on your working path/module path).

```bash
# Compile (and run, if toolchain available) a C++ source file
python -m compiler path/to/file.cpp

# Stop at a specific stage
python -m compiler path/to/file.cpp --stage tokenize
python -m compiler path/to/file.cpp --stage parse
python -m compiler path/to/file.cpp --stage semantic
python -m compiler path/to/file.cpp --stage optimize
python -m compiler path/to/file.cpp --stage ir
python -m compiler path/to/file.cpp --stage assembly

# Compile without optimization
python -m compiler path/to/file.cpp --no-optimize

# Provide stdin to the compiled program
python -m compiler path/to/file.cpp --stdin "input text"

# Write generated assembly to a file and/or print full JSON result
python -m compiler path/to/file.cpp --asm out.s --json
```

You can also run the tokenizer directly:
```bash
python cpp_tokenizer.py
python cpp_tokenizer.py main.cpp
python cpp_tokenizer.py main.cpp --comments
```

## Run tests

```bash
# All project tests
python -m unittest discover -s tests -p "test*.py"

# Specific suites
python -m unittest tests.test_parser tests.test_optimizer
python -m unittest tests.test_features
```

## Run the compiler API server

The HTTP service exposes JSON endpoints (e.g. `/api/health`, `/api/compile`, `/api/tokenize`, `/api/parse`, `/api/semantic`, `/api/optimize`, `/api/ir`, `/api/assembly`) and can serve the built UI if `ui/dist` exists.

```bash
python -m compiler.server
# options:
#   --host 127.0.0.1 (default outside PORT env) | 0.0.0.0
#   --port 8000
#   --open   (open UI in browser; local use)
#   --quiet
```

Default local URL: `http://127.0.0.1:8000/` (API at `/api/...`).

**Security note:** the server compiles/runs submitted C++ using the host toolchain. Keep it local/trusted by default; do not expose publicly without authentication, rate limiting, sandboxing/isolation, and resource controls.

## Run the web UI

### Option A: Use the built UI served by Python
1. Build the UI:
   ```bash
   cd ui
   npm install
   npm run build
   cd ..
   ```
2. Start the server from repo root:
   ```bash
   python -m compiler.server --port 8000
   ```
3. Open `http://127.0.0.1:8000/` — UI and API are same-origin (no `VITE_COMPILER_API_URL` needed).

### Option B: UI dev server (Vite) + API server
1. Start API server:
   ```bash
   python -m compiler.server --port 8000
   ```
2. In another terminal, configure and run UI:
   ```bash
   cd ui
   npm install
   copy .env.example .env.local   # Windows (PowerShell/CMD: copy)
   # set VITE_COMPILER_API_URL=http://localhost:8000 in .env.local
   npm run dev
   ```
3. Open the Vite dev URL shown in terminal (usually `http://localhost:5173`).

See [`ui/README.md`](ui/README.md) and [`ui/SHORTCUTS.md`](ui/SHORTCUTS.md) for UI behavior, workspace persistence, and shortcuts.

## Deploy with Docker / Render

This repo includes a root [`Dockerfile`](Dockerfile) and [`render.yaml`](render.yaml) to deploy API + built UI as one service.

- **Docker (local build/run example):**
  ```bash
  docker build -t compiled-compiler .
  docker run --rm -p 8000:8000 compiled-compiler
  ```
- **Render Blueprint:** deploy from repo (uses `render.yaml`), or create a Docker web service with root context as this repo and Dockerfile `./Dockerfile`.
- Container builds `ui/dist`, installs `build-essential` (g++), and runs `python -m compiler.server` on `$PORT`.

## Troubleshooting

- **`python` not found (Windows):** try `py -3` or install Python and enable “Add to PATH”.
- **No native toolchain / build unavailable:** install `g++`/MinGW-w64/clang and ensure it’s on `PATH`. You can still inspect tokens/AST/semantic/IR/assembly outputs.
- **Execution blocked on Windows (Application Control / WinError 4551) or timeouts:** some environments restrict running newly built binaries from certain locations. Prefer setting a writable/allowed build dir:
  - PowerShell: `$env:COMPILED_BUILD_DIR = "$env:TEMP\opencode\compiled-build"`
  - then rerun compile/run. (The pipeline may also choose a safe default; policies vary by machine.)
- **UI shows compiler not connected:** start `python -m compiler.server` and/or set `VITE_COMPILER_API_URL` correctly for Vite dev mode; rebuild UI (`npm run build`) if serving from Python.
- **Port in use:** change with `--port` (server) or Vite config/env as needed.
- **CORS issues in dev:** server enables permissive CORS headers for local/dev API use; ensure requests target the correct API origin.
