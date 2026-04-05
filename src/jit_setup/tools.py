"""Agent tool definitions and execution — the LLM's hands."""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .scanner import scan_project

# ── Tool Definitions (OpenAI function-calling format) ────────────────────────

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "scan_project",
            "description": "Scan the project structure, dependencies, config files. Returns a structured analysis.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file's content (relative to project root). Max 50KB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory (respects .gitignore). Returns names with type indicators.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path, default '.'", "default": "."},
                    "depth": {"type": "integer", "description": "Max depth, default 2", "default": 2},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tool",
            "description": "Check if a tool is installed locally and get its version.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": "Tool name: python, python3, node, npm, pnpm, yarn, docker, "
                                       "cargo, go, make, git, conda, uv, poetry, pdm, pipenv, "
                                       "nvcc, brew, apt, fnm, nvm, volta, rustup, java, ruby, bun",
                    },
                },
                "required": ["tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_platform",
            "description": "Get OS, architecture, Python version, and machine info.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command in the project directory. "
                           "Safe commands (pip install, npm install, etc.) run automatically. "
                           "System-level commands require user confirmation. "
                           "Dangerous commands (rm -rf /, etc.) are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds, default 120", "default": 120},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_env",
            "description": "Write or update a .env file. Only writes to .env, .envrc, or similar config files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "object",
                        "description": "Key-value pairs to write, e.g. {\"WANDB_API_KEY\": \"sk-xxx\"}",
                    },
                    "file": {"type": "string", "description": "Target file, default '.env'", "default": ".env"},
                },
                "required": ["entries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_venv",
            "description": "Create a Python virtual environment. Supports venv, conda, uv.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["venv", "conda", "uv"],
                        "description": "Isolation method",
                    },
                    "python_version": {
                        "type": "string",
                        "description": "Python version, e.g. '3.11'. Only used for conda.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Env name. Default '.venv' for venv/uv, project dir name for conda.",
                    },
                },
                "required": ["method"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_deps",
            "description": "Install project dependencies using the appropriate package manager.",
            "parameters": {
                "type": "object",
                "properties": {
                    "manager": {
                        "type": "string",
                        "enum": ["pip", "poetry", "pdm", "uv", "pipenv", "conda",
                                 "npm", "pnpm", "yarn", "bun", "cargo", "go", "bundler"],
                        "description": "Package manager to use",
                    },
                    "extras": {
                        "type": "string",
                        "description": "Extra install args, e.g. '-e .[dev]' for pip, '--dev' for poetry",
                    },
                },
                "required": ["manager"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_setup",
            "description": "Run a verification command to check if the environment is working.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to verify, e.g. 'python -c \"import torch; print(torch.cuda.is_available())\"'",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

# ── Security Policy ──────────────────────────────────────────────────────────

# Commands that run automatically inside project dir
_SAFE_PREFIXES = [
    "python --version", "python3 --version", "node --version",
    "pip --version", "npm --version", "cargo --version", "go version",
    "pip install", "pip3 install",
    "python -m venv", "python3 -m venv",
    "python -m pip", "python3 -m pip",
    "npm install", "npm ci", "npm run",
    "pnpm install", "pnpm run",
    "yarn install", "yarn run",
    "bun install", "bun run",
    "poetry install", "poetry run",
    "pdm install", "pdm run",
    "uv pip install", "uv venv", "uv sync",
    "pipenv install", "pipenv run",
    "cargo build", "cargo test", "cargo run",
    "go mod download", "go build", "go test",
    "bundle install",
    "make ", "just ",
    "docker compose up", "docker compose build",
    "docker-compose up", "docker-compose build",
    "git status", "git log", "git diff",
    "cat ", "head ", "tail ", "wc ", "ls ",
    "source ", ". ",
    "pytest", "python -m pytest",
]

# Always blocked
_BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+[/~]",
    r"rm\s+-rf\s+\.\.",
    r"sudo\s+rm",
    r"mkfs\.",
    r"dd\s+if=",
    r">\s*/dev/",
    r"chmod\s+777\s+/",
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"pip\s+install(?!.*(-e\s+\.|--editable|-r\s+req|--requirement))",  # bare pip install outside venv
]

# Commands that touch outside project dir → need confirmation
_SYSTEM_PREFIXES = [
    "brew ", "apt ", "apt-get ", "dnf ", "yum ", "pacman ",
    "sudo ", "npm install -g", "pip install",  # pip without venv
    "conda create", "conda install",
    "nvm install", "fnm install", "volta install",
    "rustup ",
]


def _is_blocked(cmd: str) -> str | None:
    """Return reason if command is blocked, else None."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, cmd):
            return f"Blocked by safety policy: matches '{pattern}'"
    return None


def _is_safe_in_project(cmd: str) -> bool:
    """Check if command is safe to auto-execute in project dir."""
    cmd_stripped = cmd.strip()
    return any(cmd_stripped.startswith(p) for p in _SAFE_PREFIXES)


def _is_system_level(cmd: str) -> bool:
    """Check if command affects system-level state."""
    cmd_stripped = cmd.strip()
    return any(cmd_stripped.startswith(p) for p in _SYSTEM_PREFIXES)


def _in_venv(project_dir: Path) -> bool:
    """Check if we're running inside a venv rooted in the project dir."""
    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv and Path(venv).parent == project_dir:
        return True
    # Also check if .venv/bin/python exists and is active
    venv_python = project_dir / ".venv" / "bin" / "python"
    return venv_python.exists()


def _run_cmd(cmd: str, cwd: Path, timeout: int = 120, env: dict = None) -> dict:
    """Execute a command and return structured result."""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)

    # If project has a .venv, prepend it to PATH
    venv_bin = cwd / ".venv" / "bin"
    if venv_bin.exists():
        merged_env["PATH"] = f"{venv_bin}:{merged_env.get('PATH', '')}"
        merged_env["VIRTUAL_ENV"] = str(cwd / ".venv")

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
        )
        result = {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-3000:] if len(proc.stdout) > 3000 else proc.stdout,
            "stderr": proc.stderr[-2000:] if len(proc.stderr) > 2000 else proc.stderr,
        }
        if proc.returncode == 0:
            result["success"] = True
        else:
            result["success"] = False
        return result
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)[:500]}


# ── Tool Execution ───────────────────────────────────────────────────────────

def exec_tool(name: str, args: dict, project_dir: Path,
              auto_confirm: bool = False) -> str:
    """Dispatch a tool call and return a JSON string result."""

    if name == "scan_project":
        result = scan_project(project_dir)
        return json.dumps(result, ensure_ascii=False, default=str)

    if name == "read_file":
        rel = args.get("path", "")
        target = (project_dir / rel).resolve()
        # Security: must be under project dir
        if not str(target).startswith(str(project_dir)):
            return json.dumps({"error": "Path is outside project directory"})
        if not target.exists():
            return json.dumps({"error": f"File not found: {rel}"})
        if not target.is_file():
            return json.dumps({"error": f"Not a file: {rel}"})
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50000:
                content = content[:50000] + "\n... [truncated at 50KB]"
            return json.dumps({"path": rel, "content": content}, ensure_ascii=False)
        except OSError as e:
            return json.dumps({"error": str(e)})

    if name == "list_files":
        rel = args.get("path", ".")
        depth = args.get("depth", 2)
        target = (project_dir / rel).resolve()
        if not str(target).startswith(str(project_dir)):
            return json.dumps({"error": "Path is outside project directory"})
        if not target.is_dir():
            return json.dumps({"error": f"Not a directory: {rel}"})
        entries = _list_dir(target, project_dir, max_depth=depth)
        return json.dumps({"path": rel, "entries": entries[:200]}, ensure_ascii=False)

    if name == "check_tool":
        tool = args.get("tool", "")
        return json.dumps(_check_tool(tool), ensure_ascii=False)

    if name == "get_platform":
        return json.dumps({
            "os": platform.system(),
            "os_version": platform.release(),
            "arch": platform.machine(),
            "python": sys.version.split()[0],
            "hostname": platform.node(),
            "shell": os.environ.get("SHELL", "unknown"),
            "home": str(Path.home()),
        })

    if name == "run_command":
        cmd = args.get("command", "")
        timeout = args.get("timeout", 120)
        return _exec_run_command(cmd, project_dir, timeout, auto_confirm)

    if name == "write_env":
        entries = args.get("entries", {})
        filename = args.get("file", ".env")
        return _exec_write_env(entries, filename, project_dir)

    if name == "create_venv":
        method = args.get("method", "venv")
        py_ver = args.get("python_version", "")
        env_name = args.get("name", "")
        return _exec_create_venv(method, py_ver, env_name, project_dir)

    if name == "install_deps":
        manager = args.get("manager", "pip")
        extras = args.get("extras", "")
        return _exec_install_deps(manager, extras, project_dir)

    if name == "verify_setup":
        cmd = args.get("command", "")
        result = _run_cmd(cmd, project_dir, timeout=60)
        result["verification"] = True
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"Unknown tool: {name}"})


# ── Individual Tool Implementations ──────────────────────────────────────────

def _check_tool(tool: str) -> dict:
    """Check if a tool is installed and return its version."""
    version_cmds = {
        "python":  ["python3", "--version"],
        "python3": ["python3", "--version"],
        "node":    ["node", "--version"],
        "npm":     ["npm", "--version"],
        "pnpm":    ["pnpm", "--version"],
        "yarn":    ["yarn", "--version"],
        "bun":     ["bun", "--version"],
        "docker":  ["docker", "--version"],
        "cargo":   ["cargo", "--version"],
        "rustup":  ["rustup", "--version"],
        "go":      ["go", "version"],
        "make":    ["make", "--version"],
        "git":     ["git", "--version"],
        "conda":   ["conda", "--version"],
        "uv":      ["uv", "--version"],
        "poetry":  ["poetry", "--version"],
        "pdm":     ["pdm", "--version"],
        "pipenv":  ["pipenv", "--version"],
        "nvcc":    ["nvcc", "--version"],
        "brew":    ["brew", "--version"],
        "fnm":     ["fnm", "--version"],
        "volta":   ["volta", "--version"],
        "java":    ["java", "-version"],
        "ruby":    ["ruby", "--version"],
    }

    cmd = version_cmds.get(tool)
    if not cmd:
        # Fallback: try which
        path = shutil.which(tool)
        return {"tool": tool, "installed": path is not None, "path": path}

    path = shutil.which(cmd[0])
    if not path:
        return {"tool": tool, "installed": False}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = (proc.stdout + proc.stderr).strip()
        # Extract version number
        version = output.split("\n")[0]
        return {"tool": tool, "installed": True, "version": version, "path": path}
    except (subprocess.TimeoutExpired, OSError):
        return {"tool": tool, "installed": True, "path": path, "version": "unknown"}


def _exec_run_command(cmd: str, project_dir: Path, timeout: int,
                      auto_confirm: bool) -> str:
    """Execute a command with security checks."""
    # Check blocked list
    blocked = _is_blocked(cmd)
    if blocked:
        return json.dumps({"success": False, "error": blocked})

    # Safe in-project commands: auto-execute
    if _is_safe_in_project(cmd):
        result = _run_cmd(cmd, project_dir, timeout)
        return json.dumps(result, ensure_ascii=False)

    # System-level: ask user (unless --yes)
    if _is_system_level(cmd) and not auto_confirm:
        print(f"\n  \033[33m\u26a0 System command:\033[0m {cmd}")
        try:
            answer = input("  Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return json.dumps({"success": False, "skipped": True,
                               "reason": "User cancelled"})
        if answer != "y":
            return json.dumps({"success": False, "skipped": True,
                               "reason": "User declined"})

    result = _run_cmd(cmd, project_dir, timeout)
    return json.dumps(result, ensure_ascii=False)


def _exec_write_env(entries: dict, filename: str, project_dir: Path) -> str:
    """Write entries to a .env-like file."""
    # Security: only allow env-like files
    allowed = {".env", ".envrc", ".env.local", ".env.development", ".env.test"}
    if filename not in allowed:
        return json.dumps({"error": f"Cannot write to {filename}, only {allowed}"})

    target = project_dir / filename
    existing: dict[str, str] = {}

    # Read existing file
    if target.exists():
        for line in target.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)', line)
            if m:
                existing[m.group(1)] = m.group(2)

    # Merge
    existing.update(entries)

    # Write
    lines = []
    for key, val in existing.items():
        # Quote if contains spaces
        if " " in str(val) and not (str(val).startswith('"') and str(val).endswith('"')):
            val = f'"{val}"'
        lines.append(f"{key}={val}")

    target.write_text("\n".join(lines) + "\n")

    # Add to .gitignore if not already there
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        gi_text = gitignore.read_text()
        if filename not in gi_text:
            with open(gitignore, "a") as f:
                f.write(f"\n{filename}\n")
    else:
        gitignore.write_text(f"{filename}\n")

    return json.dumps({
        "success": True,
        "file": filename,
        "keys_written": list(entries.keys()),
        "gitignore_updated": True,
    })


def _exec_create_venv(method: str, python_version: str, name: str,
                      project_dir: Path) -> str:
    """Create a Python virtual environment."""
    if method == "venv":
        venv_path = project_dir / (name or ".venv")
        if venv_path.exists():
            return json.dumps({"success": True, "method": "venv",
                               "path": str(venv_path),
                               "message": "Already exists, reusing"})
        result = _run_cmd(f"python3 -m venv {venv_path}", project_dir)
        if result.get("success"):
            result["path"] = str(venv_path)
            result["activate"] = f"source {venv_path}/bin/activate"
        return json.dumps(result, ensure_ascii=False)

    elif method == "uv":
        venv_path = project_dir / (name or ".venv")
        if venv_path.exists():
            return json.dumps({"success": True, "method": "uv",
                               "path": str(venv_path),
                               "message": "Already exists, reusing"})
        cmd = f"uv venv {venv_path}"
        if python_version:
            cmd += f" --python {python_version}"
        result = _run_cmd(cmd, project_dir)
        if result.get("success"):
            result["path"] = str(venv_path)
            result["activate"] = f"source {venv_path}/bin/activate"
        return json.dumps(result, ensure_ascii=False)

    elif method == "conda":
        env_name = name or project_dir.name
        # Check if env already exists
        check = _run_cmd(f"conda env list --json", project_dir, timeout=10)
        if check.get("success") and env_name in check.get("stdout", ""):
            return json.dumps({"success": True, "method": "conda",
                               "name": env_name,
                               "message": "Already exists, reusing",
                               "activate": f"conda activate {env_name}"})
        cmd = f"conda create -n {env_name} -y"
        if python_version:
            cmd += f" python={python_version}"
        else:
            cmd += " python"
        result = _run_cmd(cmd, project_dir, timeout=300)
        if result.get("success"):
            result["name"] = env_name
            result["activate"] = f"conda activate {env_name}"
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"Unknown venv method: {method}"})


def _exec_install_deps(manager: str, extras: str, project_dir: Path) -> str:
    """Install dependencies using the specified package manager."""
    cmds = {
        "pip":     "pip install" + (f" {extras}" if extras else " -r requirements.txt"),
        "poetry":  "poetry install" + (f" {extras}" if extras else ""),
        "pdm":     "pdm install" + (f" {extras}" if extras else ""),
        "uv":      "uv pip install" + (f" {extras}" if extras else " -r requirements.txt"),
        "pipenv":  "pipenv install" + (f" {extras}" if extras else ""),
        "conda":   "conda install -y --file requirements.txt" + (f" {extras}" if extras else ""),
        "npm":     "npm install" + (f" {extras}" if extras else ""),
        "pnpm":    "pnpm install" + (f" {extras}" if extras else ""),
        "yarn":    "yarn install" + (f" {extras}" if extras else ""),
        "bun":     "bun install" + (f" {extras}" if extras else ""),
        "cargo":   "cargo build" + (f" {extras}" if extras else ""),
        "go":      "go mod download" + (f" && {extras}" if extras else ""),
        "bundler": "bundle install" + (f" {extras}" if extras else ""),
    }

    cmd = cmds.get(manager)
    if not cmd:
        return json.dumps({"error": f"Unknown package manager: {manager}"})

    result = _run_cmd(cmd, project_dir, timeout=300)
    result["manager"] = manager
    result["command"] = cmd
    return json.dumps(result, ensure_ascii=False)


def _list_dir(target: Path, project_root: Path, prefix: str = "",
              max_depth: int = 2, current_depth: int = 0) -> list[str]:
    """List directory entries with depth limit."""
    if current_depth >= max_depth:
        return []

    entries = []
    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return [f"{prefix}[permission denied]"]

    # Skip hidden dirs, node_modules, .git, __pycache__, .venv, etc.
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
                 ".eggs", "*.egg-info", "target"}

    for item in items[:100]:  # cap at 100 per dir
        if item.name.startswith(".") and item.name not in (".env.example", ".env.template",
                                                            ".nvmrc", ".node-version",
                                                            ".python-version", ".tool-versions",
                                                            ".gitignore"):
            continue
        if item.is_dir() and item.name in skip_dirs:
            continue

        indicator = "/" if item.is_dir() else ""
        entries.append(f"{prefix}{item.name}{indicator}")

        if item.is_dir() and current_depth + 1 < max_depth:
            entries.extend(_list_dir(item, project_root, prefix=prefix + "  ",
                                     max_depth=max_depth,
                                     current_depth=current_depth + 1))

    return entries
