# Compiler UI

The compiler workspace is a React + TypeScript + Vite app. It uses Monaco for
the editor and Lucide React for icons. Compiler implementation stays in the
Python project; this app is the interactive client.

## Run locally

```powershell
cd ui
npm install
npm run dev
```

The editor, multiple projects, virtual folders, file rename, theme,
source copy/download work without a backend. Projects and
theme are saved in the current browser's local storage. Without an API URL,
**Run** explains that the Python compiler is not connected; it does not compile
source or pretend to return program output.

See [SHORTCUTS.md](./SHORTCUTS.md) for workspace and editor keyboard shortcuts.

## Projects, folders and GitHub saving

Projects and folders currently exist in the browser workspace and are saved
to that browser's local storage. They are not actual folders on disk and do
not automatically sync with GitHub. Download individual source files to keep
an external copy.

For contributors, the safest initial workflow is to keep source in the shared
GitHub repository: create a feature branch, organize compiler work under a
project directory, commit meaningful changes, and open a pull request. The
UI's browser projects are a prototype workspace and are not yet connected to
that repository.

For a future "save to GitHub" feature, put GitHub authorization and repository
operations in a trusted backend using GitHub App/OAuth permissions, then have
the UI call that backend. Do not put a personal access token or other GitHub
secret in frontend code or browser storage. Agree on a repository/project
layout and permission scope before implementing that integration.

To connect a local backend, copy `.env.example` to `.env.local`, set
`VITE_COMPILER_API_URL` to the backend origin, restart Vite, and implement the
HTTP contract below. The UI then sends requests to that service and surfaces
connection and HTTP errors in the output panel.

## Python integration contract

Set `VITE_COMPILER_API_URL` to the base origin (for example
`http://localhost:8000`). The UI calls these endpoints:

| UI selection | Request |
|---|---|
| Compile | `POST /api/compile` |
| Tokenize | `POST /api/tokenize` |
| Parse | `POST /api/parse` |

Requests use JSON and include the active editor contents. `stdin` is reserved
for future compiler input support and is currently sent as an empty string:

```json
{
  "filename": "main.cpp",
  "source": "#include <iostream>\nint main() { return 0; }",
  "stdin": ""
}
```

Return JSON with the following shape. Result fields are optional by stage;
provide `diagnostics` when the compiler has errors or warnings.

```json
{
  "output": "Program output or a readable stage summary",
  "diagnostics": [],
  "tokens": [
    { "kind": "KEYWORD", "text": "int", "line": 1, "col": 1 }
  ],
  "ast": "Program\\n└── Function: main"
}
```

The service should return a non-2xx HTTP status for request/service failures
and a JSON response for successful compiler results, including source
diagnostics. Configure CORS to allow the Vite development origin
(`http://localhost:5173`). Keep compilation limits, timeouts and process
isolation in the backend; do not execute submitted source inside the browser.

## Connecting existing compiler phases

The current Python tokenizer exposes `cpp_tokenizer.tokenize(source)`, which
returns `(tokens, errors)`. The parser exposes
`syntax_analysis.parser.parse_source(source)`; its result includes `ast`,
`lexical_errors`, `syntax_errors` and `ok`. The UI does not import Python
modules directly. Add a small HTTP adapter in the Python app, map these
existing phase results to the JSON contract, and start it separately from the
Vite frontend. A full compile endpoint can call tokenizer, parser and future
compiler phases in order.

## Checks

```powershell
npm run build
```
