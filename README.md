<p align="center">
  <pre align="center">
     _ _ _
    (_|_) |_
    | | | __|
    | | | |_
   _/ |_|\__|
  |__/
  </pre>
</p>

<h3 align="center"><code>jit clone</code> — a just-in-time clone that's ready to run.</h3>

<p align="center">
  Like <code>git clone</code>, but your dev environment is already set up when it's done.<br>
  An AI agent that reads your project, figures out what's needed, and gets everything running.
</p>

<p align="center">
  <a href="https://pypi.org/project/jit-setup/"><img src="https://img.shields.io/pypi/v/jit-setup" alt="PyPI"></a>
  <a href="https://github.com/xspadex/jit-setup/blob/main/LICENSE"><img src="https://img.shields.io/github/license/xspadex/jit-setup" alt="License"></a>
  <a href="https://pypi.org/project/jit-setup/"><img src="https://img.shields.io/pypi/pyversions/jit-setup" alt="Python"></a>
</p>

<p align="center">
  <a href="./README_CN.md">中文文档</a>
</p>

---

## The Problem

You find a repo. Then you spend 20 minutes reading the README, figuring out which Python version to use, creating a venv, installing dependencies, hunting down `.env` variables, and debugging why `torch` won't import. Multiply that across every new project, every new teammate's machine.

## The Fix

```bash
pip install jit-setup
```

```bash
jit clone https://github.com/user/repo
```

One command. It clones the repo, scans the project, talks to you about what it finds, and sets everything up — virtual environment, dependencies, env vars, the works. Like `git clone`, but the environment is ready when it's done. **jit** = **just in time** — you clone it, it's ready.

## How It Works

```
jit clone <url>                  # clone + set up in one shot
jit                              # set up current directory
jit /path/to/project             # set up a specific project
jit --yes                        # auto-confirm system-level ops
```

Under the hood:

1. **Scan** — reads your project structure: languages, package managers, config files, Dockerfiles, GPU hints
2. **Plan** — figures out what to install, asks your preference (venv vs conda vs uv, npm vs pnpm, etc.)
3. **Execute** — creates environments, installs deps, writes `.env` files, runs setup scripts
4. **Verify** — runs a final check to make sure everything actually works

Supports **Python**, **Node.js**, **Rust**, **Go**, **Ruby**, **Docker**, and more.

## What Makes It Different

- **Zero config** — no YAML, no plugins, no project-specific setup files to maintain
- **Conversational** — it asks before doing anything destructive, explains what it finds
- **Safe by design** — commands are sandboxed to the project directory; system-level ops require confirmation; dangerous commands are blocked entirely
- **Free to use** — ships with a community API (30 requests/day), no API key needed to get started
- **Zero dependencies** — pure Python stdlib, installs in seconds

## Bring Your Own LLM

The free community API works out of the box. For unlimited use or a different model, configure `~/.jitx/config.json`:

```json
{
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxx",
    "model": "gpt-4o"
  }
}
```

Works with any OpenAI-compatible API (OpenAI, SiliconFlow, Ollama, vLLM, etc.).

## Security

`jit` takes a defense-in-depth approach:

- **Project-scoped** — file reads and command execution are restricted to the project directory
- **Three-tier command policy** — safe commands auto-run, system commands ask for confirmation, dangerous commands are blocked
- **No data leaves your machine** except LLM API calls (the conversation about your project structure)
- **HMAC-signed requests** to the community API to prevent abuse

## Requirements

- Python 3.9+
- That's it

## License

Apache-2.0
