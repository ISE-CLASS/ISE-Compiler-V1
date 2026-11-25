import Editor from "@monaco-editor/react";
import {
  Activity,
  Braces,
  Check,
  CircleAlert,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Code2,
  Command,
  Cpu,
  Copy,
  Database,
  Download,
  FileCode2,
  FilePlus2,
  Folder,
  FolderPlus,
  GripVertical,
  Moon,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  Sparkles,
  Sun,
  Terminal,
  Trash2,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { type FormEvent, type MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type SourceFile = { name: string; value: string };
type Project = { id: string; name: string; files: SourceFile[]; folders: string[] };
type EntryDialog = { kind: "file" | "folder" | "project" | "rename"; value: string; source?: string };
type CompilerStage = "run" | "compile" | "tokenize" | "parse" | "optimize" | "semantic" | "ir" | "assembly";
type DiagnosticDetail = {
  stage: string;
  severity: string;
  message: string;
  line: number;
  col: number;
};
type SymbolInfo = {
  name: string;
  kind?: string;
  type?: string;
  return_type?: string;
  params?: { name: string | null; type: string }[];
  defined?: boolean;
  line?: number;
  col?: number;
};
type CompilerResult = {
  ok?: boolean;
  stage?: string;
  failed_stage?: string | null;
  output?: string;
  diagnostics?: string[];
  diagnostic_details?: DiagnosticDetail[];
  tokens?: { kind: string; text: string; line: number; col: number }[];
  ast?: string;
  semantic?: {
    ok?: boolean;
    symbols?: SymbolTable;
    functions?: SymbolInfo[];
    globals?: SymbolInfo[];
    errors?: DiagnosticDetail[];
    warnings?: DiagnosticDetail[];
  };
  symbols?: SymbolTable;
  optimization?: { total?: number; rounds?: number; summary?: string[] };
  optimized_source?: string;
  ir?: string;
  assembly?: string;
  execution?: { ok?: boolean; stdout?: string; stderr?: string; returncode?: number | null; log?: string } | null;
  toolchain?: { name?: string | null; version?: string; target?: string; supports_assembly?: boolean };
  stages?: { name: string; ok: boolean; detail?: string; duration_ms?: number }[];
};
type RunEventStream = "stdout" | "stderr" | "stdin" | "system";
type RunEvent = { id: number; stream: RunEventStream; text: string };
type SessionSnapshot = {
  id: string;
  next: number;
  status: "running" | "exited";
  events: RunEvent[];
  returncode?: number | null;
  timed_out?: boolean;
  log?: string;
  execution?: { ok?: boolean; stdout?: string; stderr?: string; returncode?: number | null; log?: string } | null;
};
type SymbolTable = {
  functions?: SymbolInfo[];
  globals?: SymbolInfo[];
  scopes?: { id: number; kind: string; name: string; symbols: SymbolInfo[] }[];
};
type Panel = "output" | "problems" | "tokens" | "ast" | "symbols" | "ir" | "assembly";
type Connection = "checking" | "connected" | "offline";

const stagePanels: Record<CompilerStage, Panel> = {
  run: "output",
  compile: "output",
  tokenize: "tokens",
  parse: "ast",
  optimize: "output",
  semantic: "symbols",
  ir: "ir",
  assembly: "assembly",
};

const starterCode = `#include <iostream>
using namespace std;

int main() {
    int first = 21;
    int second = 21;
    int total = first + second;

    cout << "Hello, compiler!" << endl;
    cout << "The answer is " << total << endl;
    return 0;
}`;

const starterFiles: SourceFile[] = [
  { name: "main.cpp", value: starterCode },
  { name: "README.md", value: "# My compiler project\n\nWrite something brilliant." },
];

const starterProject: Project = {
  id: "my-first-project",
  name: "My first project",
  files: starterFiles,
  folders: [],
};

const panels: { id: Panel; label: string; icon: typeof Terminal }[] = [
  { id: "output", label: "Output", icon: Terminal },
  { id: "problems", label: "Problems", icon: CircleAlert },
  { id: "tokens", label: "Tokens", icon: Braces },
  { id: "ast", label: "Syntax tree", icon: Activity },
  { id: "symbols", label: "Semantics", icon: Database },
  { id: "ir", label: "IR", icon: Workflow },
  { id: "assembly", label: "Assembly", icon: Cpu },
];

function loadProjects(): Project[] {
  try {
    const saved = localStorage.getItem("compiled-projects");
    if (saved) {
      const parsed: unknown = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.every((project) =>
        project && typeof project.id === "string" && typeof project.name === "string" &&
        Array.isArray(project.files) && project.files.every((file: SourceFile) =>
          file && typeof file.name === "string" && typeof file.value === "string") &&
        Array.isArray(project.folders) && project.folders.every((folder: unknown) => typeof folder === "string"))) {
        return parsed as Project[];
      }
    }
    const legacy = localStorage.getItem("forge-files");
    if (legacy) {
      const files: unknown = JSON.parse(legacy);
      if (Array.isArray(files) && files.every((file) => file && typeof file.name === "string" && typeof file.value === "string")) {
        return [{ ...starterProject, files: files as SourceFile[] }];
      }
    }
  } catch (error) {
    console.error("Could not restore saved compiler files.", error);
  }
  return [starterProject];
}

function App() {
  const [projects, setProjects] = useState<Project[]>(loadProjects);
  const [activeProjectId, setActiveProjectId] = useState(() => {
    try {
      return localStorage.getItem("compiled-active-project") ?? starterProject.id;
    } catch (error) {
      console.error("Could not restore the active project.", error);
      return starterProject.id;
    }
  });
  const activeProject = projects.find((project) => project.id === activeProjectId) ?? projects[0];
  const files = activeProject?.files ?? [];
  const [activeFile, setActiveFile] = useState("main.cpp");
  const [activeFolder, setActiveFolder] = useState("");
  const [collapsedFolders, setCollapsedFolders] = useState<string[]>([]);
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (localStorage.getItem("compiled-theme") === "light" || localStorage.getItem("forge-theme") === "light" ? "light" : "dark"),
  );
  const [panel, setPanel] = useState<Panel>("output");
  const [codePanelOpen, setCodePanelOpen] = useState(true);
  const [stage, setStage] = useState<CompilerStage>("run");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CompilerResult | null>(null);
  const [runError, setRunError] = useState("");
  const [stdinText, setStdinText] = useState("");
  const [session, setSession] = useState<{ id: string; status: "running" | "exited" } | null>(null);
  const [sessionEvents, setSessionEvents] = useState<RunEvent[]>([]);
  const [sessionReturncode, setSessionReturncode] = useState<number | null | undefined>(undefined);
  const [inputBusy, setInputBusy] = useState(false);
  const activeSessionIdRef = useRef<string | null>(null);
  const sessionCursorRef = useRef(0);
  const sessionPollBusyRef = useRef(false);
  const sessionOutputRef = useRef<HTMLPreElement>(null);
  const [connection, setConnection] = useState<Connection>("checking");
  const [toast, setToast] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [entryDialog, setEntryDialog] = useState<EntryDialog | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("compiled-layout") ?? "{}");
      return typeof saved.sidebarWidth === "number" ? Math.min(420, Math.max(160, saved.sidebarWidth)) : 218;
    } catch { return 218; }
  });
  const [rightWidth, setRightWidth] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("compiled-layout") ?? "{}");
      return typeof saved.rightWidth === "number" ? Math.min(700, Math.max(280, saved.rightWidth)) : 390;
    } catch { return 390; }
  });
  const resizeRef = useRef<{ kind: "sidebar" | "right"; startX: number; startSidebar: number; startRight: number } | null>(null);

  const currentFile = files.find((file) => file.name === activeFile) ?? files[0];
  const isCpp = currentFile?.name.endsWith(".cpp") ?? true;

  const apiUrl = useMemo(() => {
    const configured = import.meta.env.VITE_COMPILER_API_URL?.replace(/\/$/, "");
    if (configured) return configured;
    if (typeof window === "undefined") return "http://localhost:8000";

    const { origin, hostname, port } = window.location;
    if (port === "8000") return origin;
    if (port === "5173" || port === "4173") return `http://${hostname || "localhost"}:8000`;
    if (import.meta.env.DEV) return `http://${hostname || "localhost"}:8000`;
    return origin;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 2500);
    fetch(`${apiUrl}/api/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(() => {
        if (!cancelled) setConnection("connected");
      })
      .catch(() => {
        if (!cancelled) setConnection("offline");
      })
      .finally(() => window.clearTimeout(timer));
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [apiUrl]);

  useEffect(() => {
    localStorage.setItem("compiled-projects", JSON.stringify(projects));
  }, [projects]);

  useEffect(() => {
    if (activeProject) {
      localStorage.setItem("compiled-active-project", activeProject.id);
      if (!files.some((file) => file.name === activeFile) && files[0]) {
        setActiveFile(files[0].name);
      }
    }
  }, [activeProject, activeFile, files]);

  useEffect(() => {
    localStorage.setItem("compiled-theme", theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    localStorage.setItem("compiled-layout", JSON.stringify({ sidebarWidth, rightWidth }));
  }, [sidebarWidth, rightWidth]);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const drag = resizeRef.current;
      if (!drag) return;
      const delta = event.clientX - drag.startX;
      if (drag.kind === "sidebar") {
        setSidebarWidth(Math.min(420, Math.max(160, drag.startSidebar + delta)));
      } else {
        setRightWidth(Math.min(700, Math.max(280, drag.startRight - delta)));
      }
    };
    const onUp = () => {
      if (resizeRef.current) {
        resizeRef.current = null;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startResize = (kind: "sidebar" | "right") => (event: ReactMouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeRef.current = { kind, startX: event.clientX, startSidebar: sidebarWidth, startRight: rightWidth };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const applySessionSnapshot = useCallback((snapshot: SessionSnapshot) => {
    if (activeSessionIdRef.current !== snapshot.id) return;
    setSession({ id: snapshot.id, status: snapshot.status });
    if (snapshot.events.length) {
      setSessionEvents((current) => {
        const seen = new Set(current.map((event) => event.id));
        const fresh = snapshot.events.filter((event) => !seen.has(event.id));
        return fresh.length ? [...current, ...fresh] : current;
      });
    }
    sessionCursorRef.current = Math.max(sessionCursorRef.current, snapshot.next);
    setSessionReturncode(snapshot.returncode);
    if (snapshot.status === "exited" && snapshot.execution) {
      setResult((current) => ({
        ...(current ?? {}),
        ok: snapshot.execution?.ok,
        execution: snapshot.execution,
      }));
    }
  }, []);

  const stopCurrentSession = async () => {
    const id = activeSessionIdRef.current;
    activeSessionIdRef.current = null;
    setSession(null);
    setSessionEvents([]);
    setSessionReturncode(undefined);
    sessionCursorRef.current = 0;
    if (!id) return;
    try {
      await fetch(`${apiUrl}/api/run/${id}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch {
      // The server expires abandoned sessions; stopping is best-effort.
    }
  };

  const sendSessionInput = async (addNewline: boolean, closeInput = false) => {
    const id = activeSessionIdRef.current;
    if (!id || session?.status !== "running" || inputBusy) return;
    let text = closeInput ? "" : stdinText;
    if (!closeInput && addNewline && text && !text.endsWith("\n")) text = `${text}\n`;
    if (!text && !closeInput) return;

    setInputBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/run/${id}/input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input: text,
          close: closeInput,
          after: sessionCursorRef.current,
        }),
      });
      if (!response.ok) {
        throw new Error(`Input service returned ${response.status} ${response.statusText}`.trim());
      }
      const snapshot: unknown = await response.json();
      if (snapshot && typeof snapshot === "object") {
        applySessionSnapshot(snapshot as SessionSnapshot);
      }
      if (!closeInput) setStdinText("");
      setConnection("connected");
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Could not send program input.");
      setPanel("problems");
    } finally {
      setInputBusy(false);
    }
  };

  useEffect(() => {
    const id = session?.id;
    if (!id || session?.status !== "running") return;
    let cancelled = false;
    const poll = async () => {
      if (sessionPollBusyRef.current || cancelled) return;
      sessionPollBusyRef.current = true;
      try {
        const response = await fetch(`${apiUrl}/api/run/${id}/poll?after=${sessionCursorRef.current}`);
        if (!response.ok) throw new Error(`Poll returned ${response.status}`);
        const snapshot = await response.json() as SessionSnapshot;
        if (!cancelled) applySessionSnapshot(snapshot);
      } catch {
        // A missed poll is transient; keep streaming on the next tick.
      } finally {
        sessionPollBusyRef.current = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 150);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apiUrl, session?.id, session?.status, applySessionSnapshot]);

  useEffect(() => {
    if (panel !== "output") return;
    const output = sessionOutputRef.current;
    const container = output?.closest(".panel-content") as HTMLElement | null;
    if (container) container.scrollTop = container.scrollHeight;
  }, [sessionEvents, panel]);

  useEffect(() => {
    const handleShortcuts = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSearchOpen(false);
        setHelpOpen(false);
        setEntryDialog(null);
        return;
      }
      const modifier = event.ctrlKey || event.metaKey;
      if (!modifier) return;
      if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
      } else if (event.key === "Enter") {
        event.preventDefault();
        void runCompiler();
      } else if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        setToast("Project saved in this browser");
      }
    };
    window.addEventListener("keydown", handleShortcuts);
    return () => window.removeEventListener("keydown", handleShortcuts);
  });

  const updateSource = (value: string | undefined) => {
    if (!currentFile) return;
    setProjects((existing) => existing.map((project) => project.id === activeProject?.id
      ? { ...project, files: project.files.map((file) => file.name === currentFile.name ? { ...file, value: value ?? "" } : file) }
      : project));
  };

  const runCompiler = async (selectedStage: CompilerStage = stage) => {
    if (!currentFile) return;
    await stopCurrentSession();
    setBusy(true);
    setRunError("");
    setResult(null);
    setPanel("output");
    if (connection === "offline") {
      try {
        const probe = await fetch(`${apiUrl}/api/health`);
        if (!probe.ok) throw new Error(`HTTP ${probe.status}`);
        setConnection("connected");
      } catch {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        setResult({
          output: "The Python compiler backend is not connected.\n\nStart it with:\n\n  python -m compiler.server --open\n\n(or set VITE_COMPILER_API_URL). Your source is saved in this browser.",
        });
        setRunError(`Cannot reach the compiler service at ${apiUrl}`);
        setConnection("offline");
        setBusy(false);
        return;
      }
    }

    const body = JSON.stringify({
      filename: currentFile.name.split("/").pop(),
      source: currentFile.value,
      stdin: stdinText,
      optimize: true,
    });

    try {
      if (selectedStage === "run") {
        const response = await fetch(`${apiUrl}/api/run/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
        });
        if (!response.ok) {
          throw new Error(`Compiler service returned ${response.status} ${response.statusText}`.trim());
        }
        const payload: unknown = await response.json();
        if (!payload || typeof payload !== "object") {
          throw new Error("Compiler service returned an invalid response.");
        }
        const compilerResult = payload as CompilerResult & {
          session?: { id: string; status: string } | null;
        };
        setResult(compilerResult);
        setConnection("connected");
        const sessionId = compilerResult.session?.id;
        if (sessionId) {
          activeSessionIdRef.current = sessionId;
          sessionCursorRef.current = 0;
          setSession({ id: sessionId, status: "running" });
          setSessionEvents([]);
          setSessionReturncode(undefined);
          setToast("Program running — send input below when prompted");
          return;
        }
        setPanel(compilerResult.ok === false ? "problems" : "output");
        return;
      }

      const response = await fetch(`${apiUrl}/api/${selectedStage}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (!response.ok) {
        throw new Error(`Compiler service returned ${response.status} ${response.statusText}`.trim());
      }
      const payload: unknown = await response.json();
      if (!payload || typeof payload !== "object") {
        throw new Error("Compiler service returned an invalid response.");
      }
      const compilerResult = payload as CompilerResult;
      setResult(compilerResult);
      setConnection("connected");
      setPanel(compilerResult.ok === false ? "problems" : stagePanels[selectedStage]);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "Could not connect to the compiler service.");
      setConnection("offline");
      setPanel("problems");
    } finally {
      setBusy(false);
    }
  };

  const runStage = (next: CompilerStage) => {
    setStage(next);
    void runCompiler(next);
  };

  const addFile = () => {
    let number = files.length + 1;
    let suggested = `untitled-${number}.cpp`;
    while (files.some((file) => file.name === `${activeFolder ? `${activeFolder}/` : ""}${suggested}`)) {
      suggested = `untitled-${++number}.cpp`;
    }
    setEntryDialog({ kind: "file", value: `${activeFolder ? `${activeFolder}/` : ""}${suggested}` });
  };

  const addFolder = () => {
    setEntryDialog({ kind: "folder", value: activeFolder ? `${activeFolder}/new-folder` : "src" });
  };

  const addProject = () => {
    setEntryDialog({ kind: "project", value: "My project" });
  };

  const renameActiveFile = () => {
    if (!currentFile) return;
    setEntryDialog({ kind: "rename", value: currentFile.name, source: currentFile.name });
  };

  const submitEntry = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!entryDialog || !activeProject) return;
    const value = entryDialog.value.trim();

    if (entryDialog.kind === "project") {
      if (!value) {
        setToast("Enter a project name");
        return;
      }
      const project: Project = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: value,
        files: [{ name: "main.cpp", value: starterCode }],
        folders: [],
      };
      setProjects((existing) => [...existing, project]);
      setActiveProjectId(project.id);
      setActiveFile("main.cpp");
      setActiveFolder("");
      setEntryDialog(null);
      setToast(`${value} project created`);
      return;
    }

    const path = value.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    if (!path || path.split("/").some((part) => !part || part === "." || part === "..")) {
      setToast("Enter a valid project-relative path");
      return;
    }

    if (entryDialog.kind === "file") {
      if (files.some((file) => file.name === path)) {
        setToast("A file with that name already exists");
        return;
      }
      const parentFolders = path.split("/").slice(0, -1).map((_, index, parts) => parts.slice(0, index + 1).join("/"));
      setProjects((existing) => existing.map((project) => project.id === activeProject.id
        ? { ...project, files: [...project.files, { name: path, value: "// Start writing here\n" }], folders: [...new Set([...project.folders, ...parentFolders])] }
        : project));
      setActiveFile(path);
      setToast(`${path} created`);
    } else if (entryDialog.kind === "folder") {
      if (activeProject.folders.includes(path)) {
        setToast("That folder already exists");
        return;
      }
      const parentFolders = path.split("/").map((_, index, parts) => parts.slice(0, index + 1).join("/"));
      setProjects((existing) => existing.map((project) => project.id === activeProject.id
        ? { ...project, folders: [...new Set([...project.folders, ...parentFolders])] }
        : project));
      setActiveFolder(path);
      setToast(`${path} folder created`);
    } else if (entryDialog.kind === "rename" && entryDialog.source) {
      if (files.some((file) => file.name === path && file.name !== entryDialog.source)) {
        setToast("A file with that name already exists");
        return;
      }
      const parentFolders = path.split("/").slice(0, -1).map((_, index, parts) => parts.slice(0, index + 1).join("/"));
      setProjects((existing) => existing.map((project) => project.id === activeProject.id
        ? { ...project, files: project.files.map((file) => file.name === entryDialog.source ? { ...file, name: path } : file), folders: [...new Set([...project.folders, ...parentFolders])] }
        : project));
      setActiveFile(path);
      setToast(`Renamed to ${path}`);
    }
    setEntryDialog(null);
  };

  const deleteActiveFile = () => {
    if (!currentFile || files.length < 2) {
      setToast("Keep at least one project file");
      return;
    }
    const remaining = files.filter((file) => file.name !== currentFile.name);
    setProjects((existing) => existing.map((project) => project.id === activeProject?.id
      ? { ...project, files: remaining }
      : project));
    setActiveFile(remaining[0].name);
    setToast(`${currentFile.name} removed`);
  };

  const downloadFile = () => {
    if (!currentFile) return;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([currentFile.value], { type: "text/plain" }));
    link.download = currentFile.name;
    link.click();
    URL.revokeObjectURL(link.href);
    setToast("Source file downloaded");
  };

  const copySource = async () => {
    if (!currentFile) return;
    try {
      await navigator.clipboard.writeText(currentFile.value);
      setToast("Source copied to clipboard");
    } catch (error) {
      console.error("Could not copy source to clipboard.", error);
      setToast("Clipboard access is unavailable");
    }
  };

  const stats = useMemo(() => ({
    lines: currentFile?.value.split("\n").length ?? 0,
    chars: currentFile?.value.length ?? 0,
  }), [currentFile?.value]);

  const treeEntries = useMemo(() => {
    const folderPaths = new Set(activeProject?.folders ?? []);
    files.forEach((file) => {
      const parts = file.name.split("/");
      parts.slice(0, -1).forEach((_, index) => folderPaths.add(parts.slice(0, index + 1).join("/")));
    });
    const folders = [...folderPaths].sort().map((path) => ({ kind: "folder" as const, path }));
    const nestedFiles = files.map((file) => ({ kind: "file" as const, path: file.name }));
    return [...folders, ...nestedFiles].sort((a, b) => a.path.localeCompare(b.path) || (a.kind === "folder" ? -1 : 1));
  }, [activeProject?.folders, files]);

  const isEntryVisible = (path: string) => {
    const parts = path.split("/");
    return !parts.slice(0, -1).some((_, index) => collapsedFolders.includes(parts.slice(0, index + 1).join("/")));
  };

  return (
    <main className="app-shell" data-theme={theme}>
      <header className="topbar">
        <a className="brand" href="#" aria-label="COMPILED home">
          <span className="brand-mark"><Code2 size={17} strokeWidth={2.5} /></span>
          <span>COMPILED</span>
        </a>
        <div className="top-divider" />
        <label className="project-switcher" aria-label="Current project">
          <span className="project-indicator" />
          <select value={activeProject?.id ?? ""} onChange={(event) => {
            const project = projects.find((item) => item.id === event.target.value);
            if (!project) return;
            setActiveProjectId(project.id);
            setActiveFile(project.files[0]?.name ?? "");
            setActiveFolder("");
          }}>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
          <ChevronDown size={14} />
        </label>
        <button className="icon-button project-add" title="New project" onClick={addProject}><Plus size={16} /></button>
        <div className="topbar-spacer" />
        <button className="top-action hide-small" onClick={() => setSearchOpen((open) => !open)}>
          <Search size={15} /><span>Find</span><kbd><Command size={10} /> K</kbd>
        </button>
        <span className={`connection ${connection === "connected" ? "connected" : ""}`}>
          <span className="connection-dot" />{connection === "connected" ? "Backend connected" : connection === "checking" ? "Connecting…" : "Backend offline"}
        </span>
        <button className="icon-button theme-toggle" title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </header>

      {searchOpen && (
        <div className="search-popover">
          <Search size={16} /><input autoFocus placeholder="Search files in this project…" onKeyDown={(event) => {
            if (event.key === "Escape") setSearchOpen(false);
            if (event.key === "Enter") {
              const found = files.find((file) => file.name.toLowerCase().includes(event.currentTarget.value.toLowerCase()));
              if (found) setActiveFile(found.name);
              else setToast("No matching file");
              setSearchOpen(false);
            }
          }} /><kbd>ESC</kbd>
        </div>
      )}

      <div
        className="workspace"
        style={{
          gridTemplateColumns: codePanelOpen
            ? `48px ${sidebarWidth}px 10px minmax(320px, 1fr) 10px ${rightWidth}px`
            : `48px 52px minmax(320px, 1fr) 10px ${rightWidth}px`,
        }}
      >
        <aside className="activity-rail" aria-label="Workspace tools">
          <button className="rail-button selected" title="Explorer"><Folder size={18} /></button>
          <button className="rail-button" title="Search files" onClick={() => setSearchOpen((open) => !open)}><Search size={18} /></button>
          <button className="rail-button mobile-rail-action" title="New file" onClick={addFile}><FilePlus2 size={17} /></button>
          <button className="rail-button mobile-rail-action" title="New folder" onClick={addFolder}><FolderPlus size={17} /></button>
          <button className="rail-button" title="Compiler stages" onClick={() => setPanel("tokens")}><Braces size={18} /></button>
          <div className="rail-spacer" />
          <button className="rail-button" title="Settings"><Settings2 size={18} /></button>
          <button className="rail-button" title="Keyboard shortcuts" onClick={() => setHelpOpen(true)}><CircleHelp size={18} /></button>
        </aside>

        <aside className={`file-sidebar ${codePanelOpen ? "" : "sidebar-collapsed"}`}>
          <div className="sidebar-heading">
            <span>{codePanelOpen ? "Explorer" : ""}</span>
            <div className="sidebar-tools">
              <button className="icon-button tiny" title="New folder" onClick={addFolder}><FolderPlus size={15} /></button>
              <button className="icon-button tiny" title="New file" onClick={addFile}><FilePlus2 size={15} /></button>
            </div>
          </div>
          <button className="project-tree" onClick={() => { setCodePanelOpen((open) => !open); setActiveFolder(""); }}>
            {codePanelOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<Folder size={15} className="folder-icon" />{codePanelOpen && <span>{activeProject?.name.toUpperCase()}</span>}
          </button>
          {codePanelOpen && (
            <div className="file-list">
              {treeEntries.filter((entry) => isEntryVisible(entry.path)).map((entry) => (
                entry.kind === "folder" ? (
                  <button key={`folder-${entry.path}`} className={`folder-row ${activeFolder === entry.path ? "folder-selected" : ""}`} style={{ paddingLeft: `${8 + (entry.path.split("/").length - 1) * 13}px` }} title={entry.path} onClick={() => {
                    setActiveFolder(entry.path);
                    setCollapsedFolders((collapsed) => collapsed.includes(entry.path) ? collapsed.filter((folder) => folder !== entry.path) : [...collapsed, entry.path]);
                  }}>
                    {collapsedFolders.includes(entry.path) ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                    <Folder size={14} className="folder-icon" /><span>{entry.path.split("/").pop()}</span>
                  </button>
                ) : (
                  <button key={`file-${entry.path}`} className={`file-row ${entry.path === activeFile ? "active" : ""}`} style={{ paddingLeft: `${23 + (entry.path.split("/").length - 1) * 13}px` }} title={entry.path} onClick={() => {
                    setActiveFile(entry.path);
                    setActiveFolder(entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : "");
                  }}>
                    <FileCode2 size={15} className={entry.path.endsWith(".cpp") ? "cpp-icon" : "readme-icon"} />
                    <span>{entry.path.split("/").pop()}</span>
                    {entry.path === activeFile && <span className="unsaved-dot" />}
                  </button>
                )
              ))}
              {files.length === 0 && <span className="empty-folder">This project is empty.</span>}
              <button className="add-file-row" onClick={addFile}><Plus size={14} /> New file here</button>
            </div>
          )}
          <div className="sidebar-footer">
            {codePanelOpen ? (
              <div className="project-card">
                <div className="project-card-icon"><Folder size={16} /></div>
                <div><strong>Local project</strong><span>Saved in this browser.</span></div>
              </div>
            ) : (
              <button className="icon-button tiny" title="Expand explorer" onClick={() => setCodePanelOpen(true)}><ChevronRight size={15} /></button>
            )}
          </div>
        </aside>

        {codePanelOpen ? (
          <div className="resize-handle" role="separator" aria-label="Resize explorer" title="Drag to resize" onMouseDown={startResize("sidebar")}>
            <ChevronLeft size={10} /><GripVertical size={12} /><ChevronRight size={10} />
          </div>
        ) : <div className="resize-handle disabled" aria-hidden="true" />}

        <section className="editor-column">
          <div className="editor-toolbar">
            <div className="breadcrumbs"><span>{activeProject?.name}</span><ChevronRight size={13} /><span className="breadcrumb-current">{currentFile?.name}</span></div>
            <div className="toolbar-actions">
              <span className="language-chip"><span className="language-dot" />{isCpp ? "C++" : "Text"}</span>
              <button className="icon-button" title="Rename file" onClick={renameActiveFile}><Pencil size={15} /></button>
              <button className="icon-button" title="Copy source" onClick={copySource}><Copy size={15} /></button>
              <button className="icon-button" title="Download source" onClick={downloadFile}><Download size={15} /></button>
              <button className="icon-button danger-hover" title="Delete file" onClick={deleteActiveFile}><Trash2 size={15} /></button>
            </div>
          </div>
          <div className="editor-tabs">
            {files.map((file) => (
              <button key={file.name} className={`editor-tab ${file.name === activeFile ? "tab-active" : ""}`} title={file.name} onClick={() => setActiveFile(file.name)}>
                <FileCode2 size={14} className={file.name.endsWith(".cpp") ? "cpp-icon" : "readme-icon"} />{file.name.split("/").pop()}
                {file.name === activeFile && <span className="tab-unsaved" />}
              </button>
            ))}
            <button className="icon-button tab-add" title="New file" onClick={addFile}><Plus size={15} /></button>
          </div>
          <div className="editor-wrap">
            <Editor
              height="100%"
              language={isCpp ? "cpp" : currentFile?.name.endsWith(".md") ? "markdown" : "plaintext"}
              theme={theme === "dark" ? "vs-dark" : "light"}
              value={currentFile?.value ?? ""}
              onChange={updateSource}
              options={{
                minimap: { enabled: false },
                fontFamily: "'JetBrains Mono', 'Cascadia Code', Consolas, monospace",
                fontSize: 13,
                lineHeight: 23,
                padding: { top: 20, bottom: 20 },
                scrollBeyondLastLine: false,
                wordWrap: "on",
                renderLineHighlight: "gutter",
                smoothScrolling: true,
                cursorBlinking: "smooth",
                automaticLayout: true,
                tabSize: 4,
                readOnly: false,
              }}
            />
          </div>
          <div className="editor-status">
            <span><span className="status-ready" />Ready</span>
            <span>{isCpp ? "C++" : "Markdown"}</span>
            <span>UTF-8</span>
            <span>Spaces: 4</span>
            <span className="status-spacer" />
            <span>Ln {stats.lines}, Col 1</span>
          </div>
        </section>

        <div className="resize-handle" role="separator" aria-label="Resize panels" title="Drag to resize" onMouseDown={startResize("right")}>
          <ChevronLeft size={10} /><GripVertical size={12} /><ChevronRight size={10} />
        </div>

        <section className="right-column">
          <div className="runbar">
            <div className="runbar-label"><Zap size={15} /><span>Run configuration</span></div>
            <div className="run-controls">
              <label className="stage-select">
                <select value={stage} onChange={(event) => setStage(event.target.value as CompilerStage)} aria-label="Compiler stage">
                  <option value="run">Compile &amp; run</option>
                  <option value="compile">Compile (all stages)</option>
                  <option value="tokenize">Tokenize</option>
                  <option value="parse">Parse</option>
                  <option value="optimize">Optimize</option>
                  <option value="semantic">Semantic analysis</option>
                  <option value="ir">Intermediate code</option>
                  <option value="assembly">Assembly</option>
                </select>
                <ChevronDown size={13} />
              </label>
              <button className="run-button" onClick={() => void runCompiler()} disabled={busy || session?.status === "running" || !isCpp}>
                {busy || session?.status === "running" ? <RotateCcw className="spin" size={15} /> : <Play size={14} fill="currentColor" />}
                {busy || session?.status === "running" ? "Running" : "Run"}
              </button>
            </div>
          </div>

          <div className="stdin-panel">
            <label className="stdin-label" htmlFor="stdin-input">
              <Terminal size={13} /><span>Program input</span>
              <small>{session?.status === "running" ? "press Enter to send to cin" : "stdin for cin"}</small>
            </label>
            <textarea
              id="stdin-input"
              className="stdin-input"
              value={stdinText}
              onChange={(event) => setStdinText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && session?.status === "running") {
                  event.preventDefault();
                  void sendSessionInput(true);
                }
              }}
              placeholder={session?.status === "running" ? "Type input and press Enter…" : "Run a program, then type when it prompts…"}
              spellCheck={false}
              rows={2}
            />
            <div className="stdin-actions">
              <button
                className="stdin-send"
                onClick={() => void sendSessionInput(true)}
                disabled={session?.status !== "running" || inputBusy}
              >
                <Play size={11} fill="currentColor" />Send input<kbd>↵</kbd>
              </button>
              <button
                className="stdin-eof"
                onClick={() => void sendSessionInput(false, true)}
                disabled={session?.status !== "running" || inputBusy}
              >
                Send EOF
              </button>
            </div>
          </div>

          <div className="results-panel">
            <div className="panel-tabs">
              {panels.map(({ id, label, icon: Icon }) => (
                <button key={id} className={`panel-tab ${panel === id ? "panel-tab-active" : ""}`} onClick={() => setPanel(id)}>
                  <Icon size={14} /><span>{label}</span>
                  {id === "tokens" && result?.tokens && <span className="count-badge">{result.tokens.length}</span>}
                  {id === "problems" && (result?.diagnostics?.length || runError) ? <span className="count-badge problem-count">{(result?.diagnostics?.length ?? 0) + (runError ? 1 : 0)}</span> : null}
                </button>
              ))}
            </div>
            <div className="panel-content">
              {panel === "output" && (
                <div className="output-view">
                  {!busy && !runError && result?.diagnostics?.map((diagnostic, index) => (
                    <div className="diagnostic" key={index}><Activity size={14} /><span>{diagnostic}</span></div>
                  ))}
                  {busy ? (
                    <div className="empty-state"><div className="loader-ring" /><strong>Running {stage === "run" ? "your program" : `the ${stage} stage`}</strong><span>Waiting for compiler results…</span></div>
                  ) : runError ? (
                    <div className="error-card"><div className="error-title"><X size={15} />Could not reach the compiler service</div><p>{runError}</p><span>Start it with <code>python -m compiler.server --open</code>, then run again.</span></div>
                  ) : session ? (
                    <>
                      <pre ref={sessionOutputRef} className="console-output live-output">
                        {sessionEvents.length ? sessionEvents.map((event) => (
                          <span key={event.id} className={`console-${event.stream}`}>
                            {event.stream === "stdin" ? `> ${event.text}` : event.stream === "system" ? `[${event.text}]` : event.text}
                          </span>
                        )) : "Program started. Waiting for output…"}
                      </pre>
                      {result?.stages?.length ? (
                        <div className="pipeline-stages">
                          {result.stages.map((item) => (
                            <span key={item.name} className={`pipeline-stage ${item.ok ? "pipeline-stage-ok" : "pipeline-stage-failed"}`} title={item.detail || ""}>
                              {item.ok ? <Check size={11} /> : <X size={11} />}{item.name}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {session.status === "running" ? (
                        <div className="run-status"><span className="live-dot" />Program running · send input below when prompted</div>
                      ) : (
                        <>
                          <div className="run-complete"><Check size={13} />Run complete <span>·</span>exit {sessionReturncode ?? "unknown"}</div>
                          {sessionReturncode !== 0 && sessionReturncode !== undefined && <div className="run-notice">Program exited with a non-zero status.</div>}
                        </>
                      )}
                      {connection === "offline" && <div className="run-notice">Compiler not connected. Source was not compiled.</div>}
                    </>
                  ) : result ? (
                    <>
                      <pre className="console-output">{result.output || "No output."}</pre>
                      {result.stages?.length ? (
                        <div className="pipeline-stages">
                          {result.stages.map((item) => (
                            <span key={item.name} className={`pipeline-stage ${item.ok ? "pipeline-stage-ok" : "pipeline-stage-failed"}`} title={item.detail || ""}>
                              {item.ok ? <Check size={11} /> : <X size={11} />}{item.name}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {result.execution?.ok && <div className="run-complete"><Check size={13} />Run complete <span>·</span>{stats.lines} lines</div>}
                      {connection === "offline" && <div className="run-notice">Compiler not connected. Source was not compiled.</div>}
                    </>
                  ) : (
                    <div className="empty-state">
                      <div className="empty-icon"><Terminal size={20} /></div>
                      <strong>Ready when you are</strong>
                      <span>Run your program to see output here.</span>
                      <button className="empty-run" onClick={() => void runCompiler()}><Play size={13} fill="currentColor" />Run program</button>
                    </div>
                  )}
                </div>
              )}
              {panel === "problems" && (
                <div className="data-view problems-view">
                  {runError && <div className="problem-row"><X size={15} /><div><strong>Compiler connection error</strong><span>{runError}</span></div></div>}
                  {result?.diagnostic_details?.length
                    ? result.diagnostic_details.map((diagnostic, index) => (
                      <div className={`problem-row problem-${diagnostic.severity}`} key={`${diagnostic.stage}-${diagnostic.line}-${index}`}>
                        <Activity size={15} />
                        <div>
                          <strong><span className="problem-stage">{diagnostic.stage}</span>{diagnostic.severity === "warning" ? "Warning" : diagnostic.severity === "info" ? "Info" : "Error"}{diagnostic.line ? ` at line ${diagnostic.line}${diagnostic.col ? `, column ${diagnostic.col}` : ""}` : ""}</strong>
                          <span>{diagnostic.message}</span>
                        </div>
                      </div>
                    ))
                    : result?.diagnostics?.map((diagnostic, index) => <div className="problem-row" key={index}><Activity size={15} /><div><strong>Compiler diagnostic</strong><span>{diagnostic}</span></div></div>)}
                  {!runError && !result?.diagnostics?.length && (connection === "offline"
                    ? <div className="connection-notice"><CircleHelp size={15} /><div><strong>Compiler not connected</strong><span>Start <code>python -m compiler.server --open</code> to report compiler errors and warnings here.</span></div></div>
                    : <div className="empty-state compact"><div className="empty-icon"><Check size={19} /></div><strong>{result ? "No problems reported" : "No problems yet"}</strong><span>Run a compiler stage to inspect diagnostics.</span></div>)}
                </div>
              )}
              {panel === "tokens" && (
                <div className="data-view">
                  {result?.tokens?.length ? (
                    <table className="token-table"><thead><tr><th>Kind</th><th>Lexeme</th><th>Position</th></tr></thead><tbody>
                      {result.tokens.map((token, index) => <tr key={`${token.line}-${token.col}-${index}`}><td><span className={`token-kind token-${token.kind.toLowerCase()}`}>{token.kind}</span></td><td className="token-lexeme">{token.text}</td><td className="token-position">{token.line}:{token.col}</td></tr>)}
                    </tbody></table>
                  ) : <div className="empty-state compact"><div className="empty-icon"><Braces size={19} /></div><strong>No tokens yet</strong><span>Run the tokenizer stage to inspect lexemes.</span><button className="empty-run" onClick={() => runStage("tokenize")}><Play size={13} fill="currentColor" />Run tokenizer</button></div>}
                </div>
              )}
              {panel === "ast" && (
                <div className="data-view ast-view">
                  {result?.ast ? <pre className="console-output">{result.ast}</pre> : <div className="empty-state compact"><div className="empty-icon"><Activity size={19} /></div><strong>No syntax tree yet</strong><span>Run the parser stage to see the AST.</span><button className="empty-run" onClick={() => runStage("parse")}><Play size={13} fill="currentColor" />Run parser</button></div>}
                </div>
              )}
              {panel === "symbols" && (
                <div className="data-view symbols-view">
                  {result?.semantic?.ok || result?.symbols ? (
                    <div className="symbol-sections">
                      <section>
                        <h4>Symbol table</h4>
                        <table className="symbol-table"><thead><tr><th>Name</th><th>Kind</th><th>Type</th><th>Position</th></tr></thead><tbody>
                          {(result.symbols?.globals ?? result.semantic?.globals ?? []).map((symbol) => (
                            <tr key={`global-${symbol.name}-${symbol.line}`}><td>{symbol.name}</td><td>global</td><td className="token-lexeme">{symbol.type}</td><td className="token-position">{symbol.line}:{symbol.col}</td></tr>
                          ))}
                          {(result.symbols?.functions ?? result.semantic?.functions ?? []).map((fn) => (
                            <tr key={`fn-${fn.name}`}><td>{fn.name}</td><td>function{fn.defined === false ? " (declaration)" : ""}</td><td className="token-lexeme">{(fn.return_type ?? "?")}({(fn.params ?? []).map((param) => `${param.type}${param.name ? ` ${param.name}` : ""}`).join(", ")})</td><td className="token-position">{fn.line}:{fn.col}</td></tr>
                          ))}
                        </tbody></table>
                      </section>
                      <section>
                        <h4>Scopes <span className="muted">({(result.symbols?.scopes ?? []).length})</span></h4>
                        <div className="scope-list">
                          {(result.symbols?.scopes ?? []).map((scope) => (
                            <div className="scope-row" key={scope.id}>
                              <strong>{scope.kind}</strong><span>{scope.name}</span>
                              <em>{scope.symbols.length} symbol(s)</em>
                            </div>
                          ))}
                        </div>
                      </section>
                      {result.semantic?.warnings?.length ? <section><h4>Warnings</h4>{result.semantic.warnings.map((item, index) => <div className="diagnostic" key={index}><Activity size={13} /><span>{item.message}</span></div>)}</section> : null}
                    </div>
                  ) : <div className="empty-state compact"><div className="empty-icon"><Database size={19} /></div><strong>No semantic results yet</strong><span>Run semantic analysis to inspect symbols, functions and scopes.</span><button className="empty-run" onClick={() => runStage("semantic")}><Play size={13} fill="currentColor" />Run semantic analysis</button></div>}
                </div>
              )}
              {panel === "ir" && (
                <div className="data-view ir-view">
                  {result?.ir ? (
                    <>
                      <pre className="console-output">{result.ir}</pre>
                      {result.optimized_source ? <details className="optimized-source"><summary>Optimized C++ source</summary><pre className="console-output">{result.optimized_source}</pre></details> : null}
                    </>
                  ) : <div className="empty-state compact"><div className="empty-icon"><Workflow size={19} /></div><strong>No intermediate code yet</strong><span>Run the IR stage to inspect three-address code.</span><button className="empty-run" onClick={() => runStage("ir")}><Play size={13} fill="currentColor" />Generate IR</button></div>}
                </div>
              )}
              {panel === "assembly" && (
                <div className="data-view assembly-view">
                  {result?.assembly ? <pre className="console-output">{result.assembly}</pre> : <div className="empty-state compact"><div className="empty-icon"><Cpu size={19} /></div><strong>No assembly code yet</strong><span>Generated x86-64 assembly will appear here.</span><button className="empty-run" onClick={() => runStage("assembly")}><Play size={13} fill="currentColor" />Generate assembly</button></div>}
                </div>
              )}
            </div>
          </div>

          <div className="backend-card">
            <div className="backend-icon">{connection === "connected" ? <Check size={16} /> : <Sparkles size={16} />}</div>
            <div className="backend-copy">
              <strong>{connection === "connected" ? "Compiler service connected" : connection === "checking" ? "Looking for compiler service…" : "Compiler service offline"}</strong>
              <span>{connection === "connected" ? `${apiUrl} · ${result?.toolchain?.version ?? "x86-64"}` : `Start python -m compiler.server --open (expected at ${apiUrl}).`}</span>
            </div>
          </div>
          <footer className="panel-footer">
            <span><span className="footer-dot" />{connection === "connected" ? "Connected" : "Saved locally"}</span>
            <span>{stats.chars.toLocaleString()} characters</span>
          </footer>
        </section>
      </div>

      {toast && <div className="toast"><Check size={15} />{toast}</div>}
      {entryDialog && (
        <div className="modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setEntryDialog(null);
        }}>
          <form className="shortcuts-dialog entry-dialog" role="dialog" aria-modal="true" aria-labelledby="entry-title" onSubmit={submitEntry}>
            <div className="dialog-heading">
              <div><strong id="entry-title">
                {entryDialog.kind === "project" ? "Create project" : entryDialog.kind === "folder" ? "Create folder" : entryDialog.kind === "rename" ? "Rename file" : "Create file"}
              </strong><span>Changes are saved in this browser workspace.</span></div>
              <button type="button" className="icon-button" title="Close dialog" onClick={() => setEntryDialog(null)}><X size={16} /></button>
            </div>
            <label className="entry-label" htmlFor="entry-name">{entryDialog.kind === "project" ? "Project name" : entryDialog.kind === "folder" ? "Folder path" : "File path"}</label>
            <input id="entry-name" className="entry-input" autoFocus value={entryDialog.value} onChange={(event) => setEntryDialog({ ...entryDialog, value: event.target.value })} />
            {entryDialog.kind !== "project" && <span className="entry-hint">Use a project-relative path, such as <code>src/main.cpp</code>.</span>}
            <div className="entry-actions"><button type="button" className="entry-cancel" onClick={() => setEntryDialog(null)}>Cancel</button><button type="submit" className="entry-submit">{entryDialog.kind === "rename" ? "Rename" : "Create"}</button></div>
          </form>
        </div>
      )}
      {helpOpen && (
        <div className="modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setHelpOpen(false);
        }}>
          <section className="shortcuts-dialog" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title">
            <div className="dialog-heading"><div><strong id="shortcuts-title">Keyboard shortcuts</strong><span>Move around COMPILED a little faster.</span></div><button className="icon-button" title="Close shortcuts" onClick={() => setHelpOpen(false)}><X size={16} /></button></div>
            <div className="shortcut-row"><span>Run selected compiler stage</span><kbd>Ctrl</kbd><b>+</b><kbd>Enter</kbd></div>
            <div className="shortcut-row"><span>Find a project file</span><kbd>Ctrl</kbd><b>+</b><kbd>K</kbd></div>
            <div className="shortcut-row"><span>Save to this browser</span><kbd>Ctrl</kbd><b>+</b><kbd>S</kbd></div>
            <div className="shortcut-row"><span>Editor commands</span><span className="shortcut-note">Use Monaco’s built-in shortcuts</span></div>
            <p className="shortcut-footnote">On Mac, use ⌘ instead of Ctrl. Browser save stores your projects locally; it does not push them to GitHub.</p>
          </section>
        </div>
      )}
    </main>
  );
}

export default App;
