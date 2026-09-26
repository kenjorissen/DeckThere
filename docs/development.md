# Development

## Source layout

```text
setup.sh, doctor.sh, uninstall.sh   User-facing commands
src/                               Runtime shell/Python, QML, layout catalog
packaging/                         systemd, Konsole configuration, artwork
tools/                             Installer helpers and asset generators
tests/                             Isolated regression tests
docs/                              User and technical documentation
```

Run development commands from the repository root. Runtime Python modules are in
`src/`; use `PYTHONPATH=src` for ad-hoc local imports, never to bypass the installed
backend's isolated Python environment. The installed app does not need the
checkout; see [installed paths](reference.md#installed-paths-and-privileges).

## Checks

On a Linux development machine, use Python 3, Bash, GNU make, ShellCheck, and
[uv](https://docs.astral.sh/uv/). No application build step is required. These are
development requirements, not extra packages to install on the Deck.

```bash
make fmt             # Ruff and shfmt fixes
make lint            # Ruff, formatting, ShellCheck, Bash syntax
make test            # Test modules in up to four isolated processes
make check           # Lint + tests; also the default make target
make test-lifecycle  # Example focused module
make test-qt         # GUI tests only; fails if the Qt runtime cannot load
```

Every `tests/test_NAME.py` has a `make test-NAME` target. Use the relevant checks
during edits. Override concurrency/tools as needed:

```bash
make check PYTHON=python3.13 TEST_JOBS=2
make test TEST_JOBS=1
```

For GUI coverage, provide matching **PySide6-Essentials and shiboken6 6.11.2** in
your development environment and use `QT_QPA_PLATFORM=offscreen`. With Qt available,
run one `make check` pass. If a no-Qt pass already succeeded, add only `make test-qt`,
not a second full suite. Core checks explicitly skip GUI tests without Qt; the
strict `test-qt` target does not silently skip a missing runtime.

GitHub Actions runs no-Qt checks and pinned-Qt interface tests as parallel jobs
with Python 3.11. Any module failure fails the check. Ruff and shfmt-py versions
are pinned in `Makefile`; configuration is in `ruff.toml`. Review formatter
changes when updating pins. Python, make, and ShellCheck use system versions.
These tool pins do not select the VirtualHere server version.

Tests use temporary files and fake services/hardware: do not make them prompt,
launch real Steam, call privileged services, or access USB/backlight devices.
Keep the real lease-expiry, hold-to-quit, and bounded cleanup timing checks.
For runtime/hardware changes, separately verify launch, client connection, exit,
brightness restoration, and forced-launcher cleanup on the Deck. Automated tests
do not prove host IME behavior or compatibility with every SteamOS build.

## Generated assets

### Keyboard catalog

The checked-in catalog is `src/deckthere_layouts.json`. See
[data and maintenance](keyboard-layouts.md#data-and-maintenance) for source data,
cache-based regeneration, and review requirements. Runtime layout selection never
downloads data.

### Steam artwork

Editable SVGs and ready-to-install PNGs live in `packaging/artwork/`. Regenerate
with PySide6, including QtSvg, available in the development environment:

```bash
python3 tools/build-artwork.py
```

Inspect the icon, portrait/landscape tiles, hero, and transparent logo after edits.
Only PNGs are installed on the Deck; no renderer or extra download is required
there. The artwork is original project content under the repository's MIT license.
