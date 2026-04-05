"""Project scanner — rule-based analysis, no LLM needed."""

import os
import re
from pathlib import Path

# Files we look for (relative to project root)
_KEY_FILES = [
    # Python
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "requirements-dev.txt", "requirements_dev.txt",
    "Pipfile", "Pipfile.lock", "poetry.lock", "pdm.lock", "uv.lock",
    "conda.yml", "environment.yml", "environment.yaml",
    ".python-version",
    # Node
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lockb", ".nvmrc", ".node-version",
    # Rust
    "Cargo.toml", "Cargo.lock",
    # Go
    "go.mod", "go.sum",
    # Java / Kotlin
    "pom.xml", "build.gradle", "build.gradle.kts",
    # Ruby
    "Gemfile", "Gemfile.lock",
    # Docker
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml",
    # Nix
    "flake.nix", "shell.nix", "default.nix",
    # General
    "Makefile", "justfile", "Taskfile.yml",
    ".env.example", ".env.template", ".env.sample",
    ".tool-versions",  # asdf
    "README.md", "README.rst", "README.txt",
]


def scan_project(root: Path) -> dict:
    """Scan a project directory and return structured analysis."""
    root = root.resolve()
    result = {
        "root": str(root),
        "found_files": _find_key_files(root),
        "languages": _detect_languages(root),
        "package_manager": _detect_package_manager(root),
        "python": _detect_python(root),
        "node": _detect_node(root),
        "docker": _detect_docker(root),
        "env_template": _parse_env_template(root),
        "setup_scripts": _find_setup_scripts(root),
        "gpu_hints": _detect_gpu_hints(root),
    }
    return result


def _find_key_files(root: Path) -> list[str]:
    """Return which key files exist."""
    found = []
    for name in _KEY_FILES:
        if (root / name).exists():
            found.append(name)
    return found


def _detect_languages(root: Path) -> list[str]:
    """Detect primary languages from config files."""
    langs = []
    files = set(os.listdir(root))

    if _any_exists(root, "pyproject.toml", "setup.py", "requirements.txt",
                   "Pipfile", ".python-version"):
        langs.append("python")
    if _any_exists(root, "package.json", ".nvmrc", ".node-version"):
        langs.append("node")
    if _any_exists(root, "Cargo.toml"):
        langs.append("rust")
    if _any_exists(root, "go.mod"):
        langs.append("go")
    if _any_exists(root, "pom.xml", "build.gradle", "build.gradle.kts"):
        langs.append("java")
    if _any_exists(root, "Gemfile"):
        langs.append("ruby")

    return langs or ["unknown"]


def _detect_package_manager(root: Path) -> dict:
    """Detect package managers and their lock files."""
    managers = {}

    # Python
    if (root / "poetry.lock").exists():
        managers["poetry"] = True
    if (root / "pdm.lock").exists():
        managers["pdm"] = True
    if (root / "uv.lock").exists():
        managers["uv"] = True
    if (root / "Pipfile.lock").exists():
        managers["pipenv"] = True
    if _any_exists(root, "requirements.txt", "requirements-dev.txt"):
        managers["pip"] = True
    if _any_exists(root, "conda.yml", "environment.yml", "environment.yaml"):
        managers["conda"] = True

    # Node
    if (root / "pnpm-lock.yaml").exists():
        managers["pnpm"] = True
    elif (root / "yarn.lock").exists():
        managers["yarn"] = True
    elif (root / "bun.lockb").exists():
        managers["bun"] = True
    elif (root / "package-lock.json").exists():
        managers["npm"] = True
    elif (root / "package.json").exists():
        managers["npm"] = True  # default for node

    # Others
    if (root / "Cargo.toml").exists():
        managers["cargo"] = True
    if (root / "go.mod").exists():
        managers["go"] = True
    if (root / "Gemfile").exists():
        managers["bundler"] = True

    return managers


def _detect_python(root: Path) -> dict | None:
    """Extract Python version requirements and build info."""
    info: dict = {}

    # .python-version
    pv = root / ".python-version"
    if pv.exists():
        info["version_file"] = pv.name
        info["version"] = _read_first_line(pv)

    # pyproject.toml
    pp = root / "pyproject.toml"
    if pp.exists():
        text = _safe_read(pp, limit=10000)
        # requires-python
        m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
        if m:
            info["requires_python"] = m.group(1)
        # build backend
        m = re.search(r'build-backend\s*=\s*"([^"]+)"', text)
        if m:
            info["build_backend"] = m.group(1)
        # project dependencies (just count)
        deps_section = re.search(r'\[project\].*?dependencies\s*=\s*\[(.*?)\]',
                                 text, re.DOTALL)
        if deps_section:
            dep_lines = [l.strip().strip('"').strip("'")
                         for l in deps_section.group(1).split("\n")
                         if l.strip() and not l.strip().startswith("#")]
            info["dep_count"] = len(dep_lines)

    # .tool-versions (asdf)
    tv = root / ".tool-versions"
    if tv.exists():
        for line in _safe_read(tv).splitlines():
            if line.startswith("python"):
                parts = line.split()
                if len(parts) >= 2:
                    info["version"] = parts[1]

    if not info:
        return None
    return info


def _detect_node(root: Path) -> dict | None:
    """Extract Node.js version and package info."""
    info: dict = {}

    # .nvmrc / .node-version
    for name in (".nvmrc", ".node-version"):
        f = root / name
        if f.exists():
            info["version_file"] = name
            info["version"] = _read_first_line(f)
            break

    # package.json
    pj = root / "package.json"
    if pj.exists():
        import json
        try:
            data = json.loads(_safe_read(pj))
            engines = data.get("engines", {})
            if "node" in engines:
                info["engines_node"] = engines["node"]
            info["has_scripts"] = bool(data.get("scripts"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            info["dep_count"] = len(deps) + len(dev_deps)
        except (json.JSONDecodeError, KeyError):
            pass

    # .tool-versions
    tv = root / ".tool-versions"
    if tv.exists():
        for line in _safe_read(tv).splitlines():
            if line.startswith("nodejs") or line.startswith("node"):
                parts = line.split()
                if len(parts) >= 2:
                    info["version"] = parts[1]

    if not info:
        return None
    return info


def _detect_docker(root: Path) -> dict | None:
    """Check for Docker/Compose files and extract base image."""
    info: dict = {}

    df = root / "Dockerfile"
    if df.exists():
        info["dockerfile"] = True
        text = _safe_read(df, limit=5000)
        m = re.search(r'^FROM\s+(\S+)', text, re.MULTILINE)
        if m:
            info["base_image"] = m.group(1)

    for name in ("docker-compose.yml", "docker-compose.yaml",
                 "compose.yml", "compose.yaml"):
        if (root / name).exists():
            info["compose"] = name
            break

    if not info:
        return None
    return info


def _parse_env_template(root: Path) -> list[str] | None:
    """Parse .env.example and return variable names."""
    for name in (".env.example", ".env.template", ".env.sample"):
        f = root / name
        if f.exists():
            text = _safe_read(f)
            vars_found = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=', line)
                if m:
                    vars_found.append(m.group(1))
            return vars_found if vars_found else None
    return None


def _find_setup_scripts(root: Path) -> list[str]:
    """Find available setup/build/test targets."""
    scripts = []

    # Makefile targets
    mf = root / "Makefile"
    if mf.exists():
        text = _safe_read(mf, limit=10000)
        for m in re.finditer(r'^([a-zA-Z_][\w-]*):', text, re.MULTILINE):
            target = m.group(1)
            if target in ("setup", "install", "build", "test", "dev",
                          "init", "bootstrap", "start", "lint", "check"):
                scripts.append(f"make {target}")

    # package.json scripts
    pj = root / "package.json"
    if pj.exists():
        import json
        try:
            data = json.loads(_safe_read(pj))
            for key in ("setup", "install", "build", "test", "dev",
                        "start", "lint", "prepare", "postinstall"):
                if key in data.get("scripts", {}):
                    scripts.append(f"npm run {key}")
        except (json.JSONDecodeError, KeyError):
            pass

    # justfile
    jf = root / "justfile"
    if jf.exists():
        text = _safe_read(jf, limit=10000)
        for m in re.finditer(r'^([a-zA-Z_][\w-]*):', text, re.MULTILINE):
            target = m.group(1)
            if target in ("setup", "install", "build", "test", "dev"):
                scripts.append(f"just {target}")

    return scripts


def _detect_gpu_hints(root: Path) -> dict | None:
    """Look for CUDA/GPU indicators."""
    hints: dict = {}

    # requirements.txt
    for name in ("requirements.txt", "requirements-dev.txt"):
        f = root / name
        if f.exists():
            text = _safe_read(f, limit=10000).lower()
            if "torch" in text or "pytorch" in text:
                hints["pytorch"] = True
            if "tensorflow" in text:
                hints["tensorflow"] = True
            if "cuda" in text or "cu11" in text or "cu12" in text:
                hints["cuda_in_reqs"] = True
            if "jax" in text:
                hints["jax"] = True

    # pyproject.toml
    pp = root / "pyproject.toml"
    if pp.exists():
        text = _safe_read(pp, limit=10000).lower()
        if "torch" in text:
            hints["pytorch"] = True
        if "tensorflow" in text:
            hints["tensorflow"] = True

    # Dockerfile
    df = root / "Dockerfile"
    if df.exists():
        text = _safe_read(df, limit=5000).lower()
        if "cuda" in text or "nvidia" in text:
            hints["cuda_in_docker"] = True

    if not hints:
        return None
    return hints


# ── Helpers ──────────────────────────────────────────────────────────────────

def _any_exists(root: Path, *names: str) -> bool:
    return any((root / n).exists() for n in names)


def _safe_read(path: Path, limit: int = 20000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit]
    except OSError:
        return ""


def _read_first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
