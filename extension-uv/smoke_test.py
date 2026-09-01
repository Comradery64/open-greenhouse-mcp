"""Boot the packed bundle and drive a real MCP handshake over stdio.

Packing can succeed and still produce something that will not start on a user's
machine, so this exercises the manifest's actual command rather than importing the
module.

Holds stdin open until the responses have been read. Piping a here-doc into the
server instead closes stdin as soon as the last byte is written, and the process can
exit before flushing its reply — which passes or fails depending on machine speed.

Usage: python extension-uv/smoke_test.py <bundle-dir>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

TIMEOUT_SECONDS = 300

# Tools the shipped skills invoke by name. If a rename lands upstream, fail here
# rather than in front of a recruiter.
REQUIRED_TOOLS = {
    "screen_candidate",
    "fetch_new_applications",
    "scan_pipeline_resumes",
    "search_pipeline_candidates",
    "advance_application",
    "reject_application",
    "add_note_to_candidate",
    "list_jobs",
}

# The bundle pins the curated profile. A much larger set means the pin was lost and
# recruiters would be handed the full tool surface, destructive operations included.
MAX_CURATED_TOOLS = 60

REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
]


def main(bundle_dir: str) -> int:
    env = {
        **os.environ,
        "GREENHOUSE_CLIENT_ID": "smoke-test-not-a-real-client-id",
        "GREENHOUSE_CLIENT_SECRET": "smoke-test-not-a-real-secret",
        "GREENHOUSE_DIAGNOSTICS": "off",
    }
    # This key is v1-only now; leaving it set would not give Harvest access and
    # would make the smoke test look more capable than the bundle is.
    env.pop("GREENHOUSE_API_KEY", None)
    # Deliberately no GREENHOUSE_TOOL_PROFILE: the server must land on its own safe
    # default, which is what an install with nothing configured would get.
    env.pop("GREENHOUSE_TOOL_PROFILE", None)

    proc = subprocess.Popen(
        ["uv", "run", "--directory", bundle_dir, "src/main.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )

    responses: dict[int, dict] = {}
    try:
        assert proc.stdin and proc.stdout
        for request in REQUESTS:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()

        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline and 1 not in responses:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if "id" in message:
                responses[message["id"]] = message
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

    if 0 not in responses:
        return fail("no response to initialize — the server did not start")
    print("initialize ->", responses[0]["result"]["serverInfo"])

    if 1 not in responses:
        return fail("no response to tools/list")
    tools = {t["name"] for t in responses[1]["result"]["tools"]}
    print(f"registered {len(tools)} tools")

    if not tools:
        return fail("no tools registered")

    missing = REQUIRED_TOOLS - tools
    if missing:
        return fail(f"skills depend on tools that are not registered: {sorted(missing)}")
    print("skill-required tools present")

    if len(tools) > MAX_CURATED_TOOLS:
        return fail(f"expected the curated profile, got {len(tools)} tools — is it pinned?")

    destructive = sorted(
        t for t in tools if t.startswith(("delete_", "remove_", "anonymize_", "merge_"))
    )
    if destructive:
        return fail(f"curated profile exposes destructive tools: {destructive}")
    print("no destructive tools exposed")

    return 0


def fail(message: str) -> int:
    print(f"::error::{message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: smoke_test.py <bundle-dir>")
    sys.exit(main(sys.argv[1]))
