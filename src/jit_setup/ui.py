"""UI components — Spinner, colors, REPL prompt, display helpers."""

import re
import sys
import os
import shutil
import threading

# ── ANSI Colors ──────────────────────────────────────────────────────────────

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"

_USER_BG = "\033[48;5;241m"   # grey background
_USER_FG = "\033[38;5;255m"   # near-white text
_ERASE_LINE = "\r\033[K"

REPL_PROMPT = "\u203a "       # › (matches Claude Code style)

# ── Banner ───────────────────────────────────────────────────────────────────

_JIT_ART = [
    r"     _ _ _   ",
    r"    (_|_) |_ ",
    r"    | | | __|",
    r"    | | | |_ ",
    r"   _/ |_|\__|",
    r"  |__/       ",
]


def print_banner():
    print()
    for line in _JIT_ART:
        print(f"{C_CYAN}{C_BOLD}{line}{C_RESET}")
    print()
    print(f"{C_BOLD}jit{C_RESET} — AI-powered environment setup  "
          f"{C_DIM}Ctrl+C to exit{C_RESET}")
    print(f"{C_DIM}{'─' * _terminal_width()}{C_RESET}", flush=True)
    print()


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


# ── Spinner ──────────────────────────────────────────────────────────────────

class Spinner:
    """Pulsing dot spinner with configurable color."""

    DOT = "\u23fa"   # ⏺

    def __init__(self, label: str, color: str = C_GREEN):
        self._label = label
        self._color = color
        self._phase = 0
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def update(self, label: str):
        self._label = label

    def _dot(self) -> str:
        if self._phase % 2 == 0:
            return f"{self._color}{self.DOT}{C_RESET}"
        return f"\033[2m{self._color}{self.DOT}{C_RESET}"

    def _run(self):
        sys.stdout.write(f"{_ERASE_LINE}{self._dot()} {self._label}")
        sys.stdout.flush()
        while not self._stop_evt.wait(0.4):
            self._phase += 1
            sys.stdout.write(f"{_ERASE_LINE}{self._dot()} {self._label}")
            sys.stdout.flush()

    def _join(self):
        self._stop_evt.set()
        self._thread.join(timeout=0.5)
        sys.stdout.write(_ERASE_LINE)
        sys.stdout.flush()

    def finish(self, label: str = None):
        """Stop with green dot (success)."""
        self._join()
        print(f"{C_GREEN}{self.DOT}{C_RESET} {label or self._label}")
        sys.stdout.flush()

    def fail(self, label: str = None):
        """Stop with red dot (error)."""
        self._join()
        print(f"{C_RED}{self.DOT}{C_RESET} {label or self._label}")
        sys.stdout.flush()

    def erase(self):
        """Stop and clear the line."""
        self._join()


# ── Display Helpers ──────────────────────────────────────────────────────────

def show_user_bubble(text: str):
    """Render user input as a grey bubble (overwrites the raw prompt line)."""
    sys.stdout.write(
        f"\033[1A\r\033[K"
        f"{_USER_BG}{_USER_FG}  {text}  \033[K{C_RESET}\n"
    )
    sys.stdout.flush()


def print_tree(label: str, items: list[tuple[str, str]]):
    """Print a tree like:
    检测到：Python 3.11 + PyTorch
    ├─ pyproject.toml    (dependencies)
    └─ Dockerfile        (CUDA 12.1)
    """
    print(f"\n  {C_BOLD}{label}{C_RESET}")
    for i, (name, desc) in enumerate(items):
        connector = "\u2514\u2500" if i == len(items) - 1 else "\u251c\u2500"
        print(f"  {C_DIM}{connector}{C_RESET} {name:<20s} {C_DIM}({desc}){C_RESET}")
    print()


# ── Markdown → ANSI Renderer ────────────────────────────────────────────────

def _render_md_line(line: str) -> str:
    """Convert a single markdown line to ANSI-styled text."""
    # Headings: ## text → bold
    m = re.match(r'^(#{1,3})\s+(.*)', line)
    if m:
        return f"{C_BOLD}{m.group(2)}{C_RESET}"

    # Horizontal rule
    if re.match(r'^---+\s*$', line):
        return f"{C_DIM}{'─' * min(40, _terminal_width())}{C_RESET}"

    # Inline formatting within the line
    line = _render_md_inline(line)
    return line


def _render_md_inline(text: str) -> str:
    """Handle inline markdown: **bold**, `code`."""
    # **bold** or __bold__
    text = re.sub(r'\*\*(.+?)\*\*', rf'{C_BOLD}\1{C_RESET}', text)
    text = re.sub(r'__(.+?)__', rf'{C_BOLD}\1{C_RESET}', text)
    # `code`
    text = re.sub(r'`([^`]+)`', rf'{C_CYAN}\1{C_RESET}', text)
    return text


class MarkdownStream:
    """Buffers streaming LLM chunks, renders markdown line-by-line."""

    def __init__(self):
        self._buf = ""
        self._in_code_block = False

    def feed(self, chunk: str):
        """Process a chunk of streamed text."""
        self._buf += chunk

        # Process complete lines
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit_line(line)

    def flush(self):
        """Flush remaining buffer."""
        if self._buf:
            self._emit_line(self._buf)
            self._buf = ""

    def _emit_line(self, line: str):
        # Code blocks: pass through with dim styling
        if line.startswith("```"):
            self._in_code_block = not self._in_code_block
            if self._in_code_block:
                sys.stdout.write(f"{C_DIM}  ┌──\n")
            else:
                sys.stdout.write(f"  └──{C_RESET}\n")
            sys.stdout.flush()
            return

        if self._in_code_block:
            sys.stdout.write(f"{C_DIM}  │ {line}{C_RESET}\n")
        else:
            rendered = _render_md_line(line)
            sys.stdout.write(rendered + "\n")

        sys.stdout.flush()


# Tool verb labels for spinner messages
_TOOL_VERBS_EN = {
    "scan_project":   "Scanning project",
    "read_file":      "Reading file",
    "list_files":     "Listing files",
    "check_tool":     "Checking tool",
    "get_platform":   "Checking platform",
    "run_command":    "Running command",
    "write_env":      "Writing .env",
    "create_venv":    "Creating virtual environment",
    "install_deps":   "Installing dependencies",
    "verify_setup":   "Verifying setup",
    "prompt_choice":  "Waiting for choice",
}

_TOOL_VERBS_ZH = {
    "scan_project":   "扫描项目",
    "read_file":      "读取文件",
    "list_files":     "列出文件",
    "check_tool":     "检查工具",
    "get_platform":   "检查平台",
    "run_command":    "执行命令",
    "write_env":      "写入 .env",
    "create_venv":    "创建虚拟环境",
    "install_deps":   "安装依赖",
    "verify_setup":   "验证环境",
    "prompt_choice":  "等待选择",
}

TOOL_VERBS = _TOOL_VERBS_EN  # default, overridden by set_locale()


def set_locale(lang: str):
    """Set UI locale. Call once at startup."""
    global TOOL_VERBS
    if lang == "zh":
        TOOL_VERBS = _TOOL_VERBS_ZH
    else:
        TOOL_VERBS = _TOOL_VERBS_EN
