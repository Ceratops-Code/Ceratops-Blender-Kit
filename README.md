# Ceratops-Blender-MCP and Ceratops-Blender-Kit

Ceratops-Blender-MCP is a local, goal-oriented MCP server for versioned Blender
character and shot production. Ceratops-Blender-Kit is its companion workflow
skill. The server exposes fixed production transitions, not arbitrary Python or
an alternative low-level Blender console.

This v1 is a working local control plane and Blender CLI integration. It creates
simple production-ready starting structures, versioned `.blend` files, review
renders, frame sequences, and ZIP packages. Artistic refinement remains a human
and agent workflow; the low-level Blender MCP can still be used separately when
an approved version needs bespoke editing.

## Skills

| Skill | Purpose |
| --- | --- |
| `ceratops-blender-kit` | Orchestrate versioned Blender character and shot production, including exact-source selection, review gates, approvals, and packaging. |

## What works

- Read-only project and asset inspection, exact version comparison, and
  character validation.
- Immutable character stages: reference import, blockout, mesh, retopology,
  look development, groom, rig, face rig, and review renders.
- Immutable shot stages: layout, assembly from exact character versions,
  camera, lighting, blocking animation, lip sync, secondary motion, preview,
  and final frame sequences.
- Explicit appearance, groom, rig, facial-expression, and animation review
  gates. Promotion records approval without changing version bytes.
- Versioned asset and episode ZIP packages.
- Persistent jobs with stable IDs derived from `project + request_id`, plus
  status, cancellation, and same-ID resume.
- Archival without permanent production-version deletion.

All write tools require a caller-supplied lowercase `request_id`. Repeating the
same request while its retained job record exists returns the same job ID;
reusing that ID with different inputs fails.

## Run locally

Requirements:

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Blender available as `blender` on `PATH`, or an absolute binary path in
  `CERATOPS_BLENDER_EXECUTABLE`

```powershell
uv sync --project mcp-servers/ceratops_blender_mcp --extra dev
$env:CERATOPS_BLENDER_EXECUTABLE = '<absolute path to blender.exe>'
uv run --directory mcp-servers/ceratops_blender_mcp ceratops-blender-mcp
```

The entry point uses MCP stdio. The deployable server source, lock, and
`mcp-server.json` live in `mcp-servers/ceratops_blender_mcp`; the Ceratops MCP
manager installs that declared source. A development host can launch
`uv run --directory <repository>/mcp-servers/ceratops_blender_mcp ceratops-blender-mcp`
and pass the Blender executable through its environment when it is not on `PATH`.
`uv.lock` is the development lock; the manager consumes `pylock.toml`, exported
from it with `uv export --project mcp-servers/ceratops_blender_mcp --locked
--no-emit-project --format pylock.toml --output-file
mcp-servers/ceratops_blender_mcp/pylock.toml`.

The server uses the maintained MCP Python SDK v2 (`mcp>=2,<3`) and its
`MCPServer` API. The package name and Python module use lowercase ecosystem
forms; the server identity presented to users is Ceratops-Blender-MCP.

## Tool contract

Read tools never initialize a project or alter production state:

- `inspect_project`, `list_assets`, `inspect_asset`
- `compare_asset_versions`, `validate_character`
- `get_job_status`

Version and lifecycle writes:

- `import_character_reference`, `promote_version`, `archive_version`
- `create_character`, `create_character_mesh`, `retopologize_character`
- `create_uv_and_materials`, `groom_character`, `rig_character`
- `build_face_rig`, `render_character_review`
- `create_shot`, `assemble_shot`, `setup_camera`, `light_shot`
- `animate_shot`, `sync_lips`, `add_secondary_motion`
- `render_shot_preview`, `render_shot_final`
- `package_asset`, `package_episode`
- `cancel_job`, `resume_job`

Every Blender-producing operation writes a new `vNNNN` directory. Callers must
pass exact source versions; the server does not silently choose “latest.”
Downstream gated tools accept only the exact version approved at the required
gate. `render_shot_final`, for example, requires the selected preview version to
have passed the animation gate.

## Production data ownership and retention

The source repository owns code, tests, documentation, and the skill. Each
caller-selected Blender project owns its runtime data:

```text
<project>/.ceratops-blender/
  project.json
  state.lock
  characters/<id>/versions/vNNNN/
  characters/<id>/events/
  shots/<id>/versions/vNNNN/
  shots/<id>/events/
  deliveries/<id>/versions/vNNNN/
  jobs/job_<stable-id>.json
```

A version request is written first, output bytes are written into that reserved
directory, and `record.json` is written last. Completed version records and
artifacts are immutable. Failures retain `failure.json` and never reuse their
version number. Promotion and archive records are append-only events.

Projects permit 25 active versions per entity by default. Creation stops at the
limit until the caller archives an older version; archived production data is
retained because v1 offers no permanent-delete tool. Job history is operational
data: active jobs are always retained and the 100 most recently updated terminal
job records are kept. Atomic control writes remove their temporary sibling on
success or failure. Blender subprocess output is held only in bounded memory and
is not persisted as an unbounded log.

## Development and repository lifecycle

The repository's single lifecycle contract is `sdlc/sdlc.yml`. It declares the
real validation and test entry points created by current Ceratops compatibility
tooling. Run them through the repository lifecycle operation runner, or use the
narrow developer commands while editing:

```powershell
uv run --locked scripts/validate-repository.py
uv run --locked scripts/run-tests.py
```

Tests use a recording Blender runtime so versioning, gating, packaging, and MCP
contracts are exercised without pretending that a local Blender installation
was rendered in CI. The nested
`mcp-servers/ceratops_blender_mcp/tests/test_blender_integration.py` adds a real
Blender smoke case and runs only when `CERATOPS_BLENDER_EXECUTABLE` is set.

## Current boundaries

- GitHub source publication and Ceratops-managed local deployment are supported;
  no PyPI release or hosted Blender service is provided.
- The unfinished exact-artifact lifecycle described in the Ceratops refactor
  plan is not implemented here. There are no fabricated build receipts,
  artifact declarations, delivery actions, or deployment claims.
- V1 does not permanently delete production versions, invoke arbitrary Blender
  Python, synthesize high-end character art, run a render farm, or coordinate
  distributed Blender workers.
- Cancellation is cooperative around the owned local Blender subprocess. A
  machine-level crash can leave Blender work that the operating system must end;
  the future Ceratops worktree process-group design is not claimed here.

See [`docs/DESIGN.md`](docs/DESIGN.md) for component boundaries and failure
behavior, and [`skills/ceratops-blender-kit/SKILL.md`](skills/ceratops-blender-kit/SKILL.md)
for the user-facing production workflow.
