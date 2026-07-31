# Cross-platform Claude Desktop extension (uv runtime)

Inputs for the `.mcpb` built by the `bundle-uv` job in
`.github/workflows/release-mcpb.yml`. One artifact covers **macOS (Intel and Apple
Silicon), Windows, and Linux**, because it ships no interpreter and no compiled
wheels — the host provisions Python and resolves dependencies with uv at install
time (`server.type = "uv"`, MCPB v0.4+).

About 190KB. One artifact is what matters here: the Claude admin console
distributes a single `.mcpb` per extension, so a bundle that vendors a
platform-specific interpreter cannot serve an office with Macs, Windows, and Linux
machines in it.

## Files

- `manifest.json` — `server.type = "uv"`; the host runs
  `uv run --directory <bundle> src/main.py`. `version` is stamped from the git tag.
- `main.py` — entry point, copied to `src/main.py`. Run as a script, so `src/` is
  `sys.path[0]` and the `greenhouse_mcp` package beside it imports with no install
  step and no `PYTHONPATH`.
- `.mcpbignore` — keeps the resolved `.venv` and caches out of the artifact.

`pyproject.toml` is **generated at build time** from the repository's
`pyproject.toml`, so the dependency sets cannot drift. It is declared under the
real distribution name `open-greenhouse-mcp` so `importlib.metadata` resolves the
version — the startup banner and every diagnostics record report it, and it reads
`dev` otherwise.

`uv lock` is run during the build and the lockfile ships in the bundle, so a
Windows user installs the same versions that CI tested. The build fails if the
lockfile contains no `win32` resolution, since that would mean it is not actually
cross-platform.

## Trade-offs

The `uv` runtime type is marked **experimental** in the MCPB spec, and dependency
resolution needs network access on first run — a machine with restricted egress or
a TLS-intercepting proxy may fail to install. The `uv.lock` shipped in the bundle
pins versions and artifact hashes, so what installs is what CI tested; the
dependency is on PyPI being reachable, not on it being trustworthy.

The manifest's command is `uv`, which the host is expected to provide. Verify on
one machine of each OS before an organisation-wide rollout, and start with Windows
— a Mac may well have `uv` installed already for unrelated reasons and pass for
the wrong reason.

## Building it locally

```sh
mkdir -p uvbundle/src
cp extension-uv/manifest.json uvbundle/manifest.json
cp extension-uv/.mcpbignore   uvbundle/.mcpbignore
cp extension-uv/main.py       uvbundle/src/main.py
cp -R src/greenhouse_mcp      uvbundle/src/greenhouse_mcp
# then generate uvbundle/pyproject.toml as the workflow's
# "Generate the bundle project" step does, and:
uv lock --directory uvbundle
cd uvbundle && mcpb validate manifest.json && mcpb pack . greenhouse-mcp-uv.mcpb
```
