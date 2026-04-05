"""CLI entry point — `jit` command."""

import argparse
import sys
from pathlib import Path


def _clone_repo(url: str) -> Path:
    """Clone a git repo (shallow) and return its path."""
    import subprocess
    import re

    name = url.rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"\.git$", "", name)
    target = Path.cwd() / name

    if target.exists():
        print(f"  Directory {name}/ already exists, using it.")
        return target

    print(f"  Cloning {url} ...")
    subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True)
    return target


def main():
    parser = argparse.ArgumentParser(
        prog="jit",
        description="AI-powered project environment setup.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-confirm safe system-level operations",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )

    sub = parser.add_subparsers(dest="command")

    # jit clone <url> [--yes]
    clone_p = sub.add_parser("clone", help="Clone a repo and set up its environment")
    clone_p.add_argument("url", help="Git URL (GitHub, GitLab, etc.)")
    clone_p.add_argument("--yes", "-y", action="store_true",
                         help="Auto-confirm safe system-level operations")

    # jit [path]  (default subcommand — no keyword needed)
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Project path (default: current directory)",
    )

    args = parser.parse_args()

    if args.command == "clone":
        try:
            project_dir = _clone_repo(args.url)
        except Exception as e:
            print(f"Error cloning: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        target = args.target or "."
        # Support bare URLs without the clone subcommand (backward compat)
        if target.startswith(("http://", "https://", "git@")):
            try:
                project_dir = _clone_repo(target)
            except Exception as e:
                print(f"Error cloning: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            project_dir = Path(target).resolve()

    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    from .loop import run
    run(project_dir, auto_confirm=args.yes)


def _get_version() -> str:
    try:
        from . import __version__
        return __version__
    except ImportError:
        return "0.1.0"


if __name__ == "__main__":
    main()
