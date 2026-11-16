#!/usr/bin/env python3
"""Verify the staged helper identity/runtime without audio devices or transport."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import time


def read_exact(stream, length, deadline):
    result = bytearray()
    while len(result) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise TimeoutError("Voice helper response timed out")
        block = os.read(stream.fileno(), length - len(result))
        if not block:
            raise RuntimeError("Voice helper closed its response pipe")
        result.extend(block)
    return bytes(result)


def exchange(process, request, expected):
    data = json.dumps(request).encode()
    process.stdin.write(len(data).to_bytes(4, "big") + data)
    process.stdin.flush()
    deadline = time.monotonic() + 15
    size = int.from_bytes(read_exact(process.stdout, 4, deadline), "big")
    if not 0 < size <= 1024 * 1024:
        raise ValueError("Invalid voice helper frame size")
    reply = json.loads(read_exact(process.stdout, size, deadline))
    if reply.get("type") != expected:
        raise RuntimeError(f"Expected {expected}, received {reply.get('type')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    helper = args.package.resolve() / "codex-resources/voice/bin/codex-voice-host"
    env = dict(os.environ)
    env.update({
        "GST_PLUGIN_PATH": "", "GST_PLUGIN_PATH_1_0": "",
        "GST_PLUGIN_SYSTEM_PATH": "", "GST_PLUGIN_SYSTEM_PATH_1_0": "",
        "GST_REGISTRY": "/dev/null", "GST_REGISTRY_UPDATE": "no",
        "GST_REGISTRY_FORK": "no",
    })
    process = subprocess.Popen([str(helper)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, bufsize=0)
    try:
        exchange(process, {"type": "hello", "protocol": 1,
                           "buildCommit": args.source_commit}, "ready")
        exchange(process, {"type": "initializeRuntime"}, "runtimeReady")
        exchange(process, {"type": "close"}, "closed")
        process.stdin.close()
        if process.wait(timeout=5) != 0:
            raise RuntimeError("Voice helper did not exit successfully")
        print("Matching source identity, private runtime initialization and clean close verified; no audio or transport opened.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
