#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Sequence
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path


API_HOME = Path(os.environ.get("CODEX_API_HOME", str(Path.home() / ".codex-api"))).expanduser()


def codex_bin() -> str:
    executable = os.environ.get("CODEX_API_BINARY") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Could not find the Codex executable.")
    return executable


def api_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(API_HOME)
    return env


def command(args: list[str], overrides: Sequence[str] = ()) -> list[str]:
    return [codex_bin(), "-c", 'cli_auth_credentials_store="file"', *overrides, *args]


def split_config_prefix(args: list[str]) -> tuple[list[str], list[str]]:
    """Keep leading native overrides separate from this helper's own commands."""
    prefix = []
    while args:
        if args[0] in ("-c", "--config"):
            if len(args) < 2:
                raise RuntimeError(f"{args[0]} requires a configuration value.")
            prefix.extend(args[:2])
            args = args[2:]
        elif args[0].startswith("--config=") or (
            args[0].startswith("-c") and len(args[0]) > 2
        ):
            prefix.append(args[0])
            args = args[1:]
        else:
            break
    return prefix, args


def ensure_home() -> None:
    API_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = API_HOME / "config.toml"
    if not config.exists():
        with config.open("x", encoding="utf-8") as output:
            output.write(
                'model = "gpt-5.5"\n'
                'model_reasoning_effort = "xhigh"\n'
                'approval_policy = "never"\n'
                'sandbox_mode = "danger-full-access"\n'
                'suppress_unstable_features_warning = true\n'
            )


def login(args: list[str], overrides: Sequence[str] = ()) -> int:
    ensure_home()
    if args:
        return subprocess.call(command(["login", *args], overrides), env=api_env())
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        if not sys.stdin.isatty():
            print("Run codex api login interactively, or pipe the key to codex api login --with-api-key.", file=sys.stderr)
            return 1
        key = getpass.getpass("OpenAI API key: ").strip()
    if not key:
        print("No API key provided.", file=sys.stderr)
        return 1
    return subprocess.run(
        command(["login", "--with-api-key"], overrides), input=key, text=True, env=api_env()
    ).returncode


def status(overrides: Sequence[str] = ()) -> int:
    ensure_home()
    print(f"API Codex home: {API_HOME}", flush=True)
    return subprocess.call(command(["login", "status"], overrides), env=api_env())


def run_codex(args: list[str], overrides: Sequence[str] = ()) -> int:
    ensure_home()
    if not (API_HOME / "auth.json").exists():
        if not os.environ.get("OPENAI_API_KEY"):
            print("No API credential found. Run: codex api login", file=sys.stderr)
            return 1
        if login([], overrides) != 0:
            return 1
    if not args and not overrides and os.environ.get("CODEX_API_MODEL"):
        args = ["-m", os.environ["CODEX_API_MODEL"]]
    invocation = command(args, overrides)
    os.execvpe(invocation[0], invocation, api_env())
    return 1


def main() -> int:
    overrides, args = split_config_prefix(sys.argv[1:])
    if args in (["-h"], ["--help"], ["help"]):
        print("Usage: codex api [login|logout|status|CODEX_ARGS...]")
        print()
        print("Open Codex with the separate API-key profile at ~/.codex-api.")
        print("  codex api login                    Save an API key to this profile")
        print("  codex api status                   Check this profile's login")
        print("  codex api                          Open the terminal interface")
        print('  codex api exec "explain this repo"  Run a command using API auth')
        return 0
    if args[:1] == ["login"]:
        return login(args[1:], overrides)
    if args == ["status"]:
        return status(overrides)
    if args == ["logout"]:
        ensure_home()
        return subprocess.call(command(["logout"], overrides), env=api_env())
    if args in (["--version"], ["-V"]):
        return subprocess.call(command(args, overrides), env=api_env())
    return run_codex(args, overrides)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"codex api: {error}", file=sys.stderr)
        raise SystemExit(1)
