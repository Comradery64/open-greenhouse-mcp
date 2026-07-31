"""Entry point for the uv-runtime bundle.

Run as a script by `uv run`, so sys.path[0] is the bundle's src/ directory and the
vendored greenhouse_mcp package next to this file is importable without an install
step or a PYTHONPATH.
"""
from greenhouse_mcp.server import main

if __name__ == "__main__":
    main()
