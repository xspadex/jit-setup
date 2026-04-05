"""Main conversation loop — the heart of `jit`."""

import json
import os
import sys
from pathlib import Path

from .config import load_config, get_device_id
from .llm import call_llm, get_llm_config, to_openai_messages, ToolCall, RateLimitError
from .tools import TOOLS_OPENAI, exec_tool, show_full_output
from .ui import (
    Spinner, MarkdownStream, print_banner, show_user_bubble, TOOL_VERBS,
    set_locale,
    C_BOLD, C_DIM, C_RED, C_YELLOW, C_GREEN, C_RESET, REPL_PROMPT,
)

# ── System Prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are jit, an AI environment setup assistant. Your job is to get this \
project's development environment fully configured on the user's machine.

{lang_instruction}

Workflow:
1. Call scan_project AND get_platform together to understand the project and machine.
2. Present a clear summary of what you found: languages, dependencies, \
config files, what's needed.
3. For Python projects, use prompt_choice to let the user pick isolation method \
— suggest one based on project signals:
   - poetry.lock → poetry
   - pdm.lock → pdm
   - uv.lock → uv
   - environment.yml → conda
   - GPU/CUDA hints → conda (better CUDA management)
   - otherwise → uv if installed, else venv
4. Create the virtual environment, install dependencies.
5. Handle .env variables — ask the user for secrets one at a time, validate \
when possible.
6. Run a final verification (build/test) to confirm everything works.
7. IMPORTANT — When setup is complete:
   a. Output a brief "Ready" summary: one-line status + available commands.
   b. Then IMMEDIATELY call prompt_choice with the available project actions \
as options (e.g. "Run dev server: bun run dev", "Run tests: bun run test", \
"Exit"). ALWAYS include "Exit" as the last option. \
This lets the user pick what to do next without typing.

Rules:
- Be concise. One step at a time. Don't over-explain.
- Keep your thinking brief — at most one short sentence before calling tools. \
The user only cares about results, not your reasoning process.
- Always verify after each installation step.
- If a command fails, read the error, diagnose, and suggest a fix — don't just retry.
- NEVER install packages into the system Python — always use a venv/conda env.
- For system-level installs (brew, apt, etc.), the run_command tool will ask \
the user for confirmation — just call it normally.
- IMPORTANT: When you need user input, use the prompt_choice tool to present \
numbered options. NEVER ask open-ended questions. The user should only need to \
press a number or Enter, not type sentences.
"""

_ERASE_LINE = "\r\033[K"


def _detect_language() -> str:
    """Detect user language from system locale."""
    import locale
    lang = os.environ.get("LANG", "") or os.environ.get("LANGUAGE", "")
    if not lang:
        lang = locale.getdefaultlocale()[0] or ""
    lang = lang.lower()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith("ja"):
        return "ja"
    if lang.startswith("ko"):
        return "ko"
    return "en"


def _build_system_prompt() -> str:
    """Build system prompt with language instruction."""
    lang = _detect_language()
    lang_map = {
        "zh": "IMPORTANT: Respond in Chinese (中文). All output, summaries, and prompts must be in Chinese.",
        "ja": "IMPORTANT: Respond in Japanese (日本語).",
        "ko": "IMPORTANT: Respond in Korean (한국어).",
        "en": "Respond in English by default. If the user writes in another language, match their language.",
    }
    instruction = lang_map.get(lang, lang_map["en"])
    return _SYSTEM_PROMPT_TEMPLATE.format(lang_instruction=instruction)


# ── Session Cache ────────────────────────────────────────────────────────────

def _session_path(project_dir: Path) -> Path:
    return project_dir / ".jit" / "session.json"


def _save_session(project_dir: Path, messages: list, meta: dict):
    """Save conversation state to .jit/session.json."""
    path = _session_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"messages": messages, "meta": meta}
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def _load_session(project_dir: Path) -> tuple[list, dict] | None:
    """Load existing session. Returns (messages, meta) or None."""
    path = _session_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        meta = data.get("meta", {})
        if msgs:
            return msgs, meta
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _clear_session(project_dir: Path):
    path = _session_path(project_dir)
    if path.exists():
        path.unlink()


# ── Main Loop ────────────────────────────────────────────────────────────────


def run(project_dir: Path, auto_confirm: bool = False):
    """Interactive setup loop."""

    print_banner()

    # Get LLM config + device ID
    user_config = load_config()
    llm_cfg = get_llm_config(user_config)
    device_id = get_device_id()

    # Build system prompt (with auto-detected language)
    lang = _detect_language()
    set_locale(lang)
    system_prompt = _build_system_prompt()
    _zh = lang == "zh"

    if llm_cfg.get("is_community"):
        print(f"{C_DIM}  {'使用免费社区 API（每天 30 次）' if _zh else 'Using free community API (30 requests/day)'}{C_RESET}")
    else:
        print(f"{C_DIM}  {'使用自定义 LLM: ' if _zh else 'Using custom LLM: '}{llm_cfg.get('model', 'default')}{C_RESET}")
    print(f"{C_DIM}  {'项目: ' if _zh else 'Project: '}{project_dir}{C_RESET}\n")

    # Check for existing session
    messages: list = []
    session = _load_session(project_dir)
    if session:
        messages, meta = session
        total_in = meta.get("total_in", 0)
        total_out = meta.get("total_out", 0)
        if _zh:
            print(f"{C_DIM}  恢复上次会话（{len(messages)} 条消息）{C_RESET}")
            print(f"{C_DIM}  输入 /reset 重新开始{C_RESET}\n")
        else:
            print(f"{C_DIM}  Resuming previous session ({len(messages)} messages){C_RESET}")
            print(f"{C_DIM}  Type /reset to start over{C_RESET}\n")
        # Jump straight to user input — don't re-run LLM
        resumed = True
    else:
        total_in = 0
        total_out = 0
        resumed = False
        # Prime the conversation
        messages.append({
            "role": "user",
            "content": f"Hi! Please analyze this project at {project_dir.name}/ "
                       f"and help me set up the development environment.",
        })

    def _call(msgs, stream_cb=None):
        cfg = get_llm_config(user_config)
        return call_llm(
            msgs, system_prompt,
            tools=TOOLS_OPENAI,
            max_tokens=2048,
            model=cfg["model"],
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            chat_endpoint=cfg["chat_endpoint"],
            stream_callback=stream_cb,
            device_id=device_id,
            is_community=cfg.get("is_community", False),
        )

    def _fmt(n):
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    # ── Conversation loop ────────────────────────────────────────────────────
    while True:
        if not resumed:
            # ── LLM turn ────────────────────────────────────────────────────
            for _round in range(10):   # max tool-use rounds per LLM turn
                spin = Spinner("Thinking\u2026").start()
                text_buf = []  # collect raw text

                def _stream_cb(chunk, _s=spin, _b=text_buf):
                    _b.append(chunk)
                    # Update spinner with live thinking preview
                    preview = "".join(_b).replace("\n", " ").strip()
                    if preview:
                        display = preview[-60:] if len(preview) > 60 else preview
                        _s.update(display)

                try:
                    text, tool_calls, usage = _call(messages, stream_cb=_stream_cb)
                except RateLimitError as e:
                    spin.fail("Rate limit")
                    print(f"\n{C_YELLOW}  {e}{C_RESET}")
                    return
                except Exception as e:
                    spin.fail("Error")
                    print(f"\n{C_RED}  {e}{C_RESET}")
                    return

                # Track tokens
                u_in = usage.get("input_tokens", 0)
                u_out = usage.get("output_tokens", 0)
                total_in += u_in
                total_out += u_out

                if text and tool_calls:
                    # Mid-loop thinking — erase spinner, done
                    spin.erase()
                elif text and not tool_calls:
                    # Final response — render with markdown
                    spin.erase()
                    md = MarkdownStream()
                    md.feed(text)
                    md.flush()
                    # Token stats
                    print(f"{C_DIM}  tokens: {_fmt(u_in)}↑ {_fmt(u_out)}↓ · total: {_fmt(total_in)}↑ {_fmt(total_out)}↓{C_RESET}")
                else:
                    spin.erase()

                # Build assistant message
                assistant_content: list = []
                if text:
                    assistant_content.append({"type": "text", "text": text})
                for tc in tool_calls:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    })
                messages.append({
                    "role": "assistant",
                    "content": assistant_content or text or "",
                })

                if not tool_calls:
                    break   # no tools — show response, wait for user

                # ── Execute tools ────────────────────────────────────────────
                # Erase the dim thinking preview before tool spinners
                sys.stdout.write(_ERASE_LINE)

                tool_result_blocks = []
                for tc in tool_calls:
                    verb = TOOL_VERBS.get(tc.name, tc.name)
                    ts = Spinner(f"{verb}\u2026", color=C_YELLOW).start()
                    result = exec_tool(tc.name, tc.input, project_dir, auto_confirm)
                    # Show success/fail
                    try:
                        r = json.loads(result)
                        if r.get("success") is False or r.get("error"):
                            ts.fail(f"{verb}")
                        else:
                            ts.finish(f"{verb}")
                    except (json.JSONDecodeError, AttributeError):
                        ts.finish(f"{verb}")

                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": result,
                    })

                # Convert to OpenAI format and append
                oai = to_openai_messages([
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user", "content": tool_result_blocks},
                ])
                messages[-1] = oai[0]
                messages.extend(oai[1:])

            # Save session after each LLM turn
            _save_session(project_dir, messages,
                          {"total_in": total_in, "total_out": total_out})

        resumed = False   # only skip LLM on first loop if resumed

        # ── User input ───────────────────────────────────────────────────────
        try:
            user_input = input(REPL_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}{'已退出。' if _zh else 'Setup exited.'}{C_RESET}")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            print(f"{C_DIM}{'已退出。' if _zh else 'Setup exited.'}{C_RESET}")
            break
        if user_input == "/skip":
            messages.append({"role": "user",
                             "content": "Skip this step and move on to the next one."})
            show_user_bubble("skip")
            continue
        if user_input in ("/output", "/o"):
            show_full_output()
            continue
        if user_input == "/reset":
            _clear_session(project_dir)
            if _zh:
                print(f"{C_DIM}会话已清除，重新运行 jit 开始。{C_RESET}")
            else:
                print(f"{C_DIM}Session cleared. Restart jit to begin fresh.{C_RESET}")
            break

        # Display user input as bubble
        show_user_bubble(user_input)

        messages.append({"role": "user", "content": user_input})
