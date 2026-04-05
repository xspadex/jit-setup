"""UI components — Spinner, colors, REPL prompt, display helpers."""

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
          f"{C_DIM}/skip · /o output · /quit{C_RESET}")
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


# Tool verb labels for spinner messages
TOOL_VERBS = {
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
