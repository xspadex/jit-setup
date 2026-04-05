"""Main conversation loop — the heart of `jit`."""

import json
import sys
from pathlib import Path

from .config import load_config, get_device_id
from .llm import call_llm, get_llm_config, to_openai_messages, ToolCall, RateLimitError
from .tools import TOOLS_OPENAI, exec_tool, show_full_output
from .ui import (
    Spinner, print_banner, show_user_bubble, TOOL_VERBS,
    C_BOLD, C_DIM, C_RED, C_YELLOW, C_GREEN, C_RESET, REPL_PROMPT,
)

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are jit, an AI environment setup assistant. Your job is to get this \
project's development environment fully configured on the user's machine \
through friendly conversation.

Workflow:
1. Call scan_project AND get_platform together to understand the project and machine.
2. Present a clear summary of what you found: languages, dependencies, \
config files, what's needed.
3. For Python projects, ask the user which isolation method they prefer \
(venv, conda, uv) — suggest one based on project signals:
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

Rules:
- Be concise. One step at a time. Don't over-explain.
- Always verify after each installation step.
- If a command fails, read the error, diagnose, and suggest a fix — don't just retry.
- NEVER install packages into the system Python — always use a venv/conda env.
- For system-level installs (brew, apt, etc.), the run_command tool will ask \
the user for confirmation — just call it normally.
- Respond in the same language the user uses.
- IMPORTANT: When you need user input, use the prompt_choice tool to present \
numbered options. NEVER ask open-ended questions. The user should only need to \
press a number or Enter, not type sentences. Example: instead of asking \
"Which isolation method do you prefer?", call prompt_choice with options.
- When all steps are done, output a short "ready to go" block with the exact \
commands to activate the env and start working, then stop.
"""

# ── Main Loop ────────────────────────────────────────────────────────────────


def run(project_dir: Path, auto_confirm: bool = False):
    """Interactive setup loop."""

    print_banner()

    # Get LLM config + device ID
    user_config = load_config()
    llm_cfg = get_llm_config(user_config)
    device_id = get_device_id()

    if llm_cfg.get("is_community"):
        print(f"{C_DIM}  Using free community API (30 requests/day){C_RESET}")
    else:
        print(f"{C_DIM}  Using custom LLM: {llm_cfg.get('model', 'default')}{C_RESET}")
    print(f"{C_DIM}  Project: {project_dir}{C_RESET}\n")

    # Prime the conversation
    messages: list = []
    messages.append({
        "role": "user",
        "content": f"Hi! Please analyze this project at {project_dir.name}/ "
                   f"and help me set up the development environment.",
    })

    def _call(msgs, stream_cb=None):
        cfg = get_llm_config(user_config)
        return call_llm(
            msgs, SYSTEM_PROMPT,
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

    # ── Token tracking ──────────────────────────────────────────────────────
    total_in = 0
    total_out = 0

    # ── Conversation loop ────────────────────────────────────────────────────
    while True:
        # ── LLM turn ────────────────────────────────────────────────────────
        for _round in range(10):   # max tool-use rounds per LLM turn
            spin = Spinner("Thinking\u2026").start()
            saw_text = [False]

            def _stream_cb(chunk, _s=spin, _f=saw_text):
                if not _f[0]:
                    _s.finish("jit")
                    _f[0] = True
                sys.stdout.write(chunk)
                sys.stdout.flush()

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

            if not saw_text[0]:
                spin.finish("jit")
            if text:
                print()   # newline after streamed text

            # Track tokens
            u_in = usage.get("input_tokens", 0)
            u_out = usage.get("output_tokens", 0)
            total_in += u_in
            total_out += u_out
            if (u_in or u_out) and not tool_calls:
                # Show token usage after final text response (not mid-tool-loop)
                def _fmt(n):
                    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
                print(f"{C_DIM}  tokens: {_fmt(u_in)}↑ {_fmt(u_out)}↓ · total: {_fmt(total_in)}↑ {_fmt(total_out)}↓{C_RESET}")

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

            # ── Execute tools ────────────────────────────────────────────────
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
            # Replace last assistant msg (already added) and append tool results
            messages[-1] = oai[0]
            messages.extend(oai[1:])

        # ── User input ───────────────────────────────────────────────────────
        try:
            user_input = input(REPL_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}Setup exited.{C_RESET}")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            print(f"{C_DIM}Setup exited.{C_RESET}")
            break
        if user_input == "/skip":
            messages.append({"role": "user",
                             "content": "Skip this step and move on to the next one."})
            show_user_bubble("skip")
            continue
        if user_input in ("/output", "/o"):
            show_full_output()
            continue

        # Display user input as bubble
        show_user_bubble(user_input)

        messages.append({"role": "user", "content": user_input})
