# Ceratops-Blender-MCP design

## Scope

Ceratops-Blender-MCP turns recognizable production goals into fixed, versioned
Blender operations. It complements rather than replaces the existing low-level
Blender MCP. The low-level server remains the right surface for bespoke,
approved scene editing; this server owns workflow state, exact-version routing,
review gates, long-job identity, and deterministic production entry points.

The companion Ceratops-Blender-Kit skill owns orchestration guidance and when to
ask for a human review. It does not own storage or duplicate server behavior.

## Components

```text
mcp-servers/ceratops_blender_mcp/
  mcp-server.json              manager readiness-module declaration
  pyproject.toml               package identity and runtime dependencies
  src/ceratops_blender_mcp/
    __main__.py                readiness and stdio switches
    server.py                  named, typed MCP tools
      -> service.py            transition rules and review gates
         -> storage.py         immutable versions and append-only events
         -> jobs.py            current-worker status and finite monitoring records
         -> blender_runtime.py owned Blender subprocess boundary
            -> execute_blender_job.py  fixed bpy operations inside Blender
```

`server.py` is the public adapter. `service.py` rejects unknown operations and
unknown parameters before any job is created. `storage.py` is authoritative for
project paths, version allocation, exact-source reads, event state, and hashes.
`jobs.py` owns operational status and retention. `blender_runtime.py` is the only
normal-Python subprocess launcher; its worker accepts structured data and an
allowlisted operation name, never Python source.

## Read and write separation

Inspection, listing, comparison, job status, and character content checks are reads.
They do not initialize `.ceratops-blender` or repair state. Every state-changing
tool is named as a production or lifecycle action and takes ordinary inputs.

The server never resolves an implicit latest source version. Each dependent
operation receives a `vNNNN` source. This makes retries and review decisions
refer to stable bytes and prevents an ambient newer version from entering a
shot or package.

## Version transaction

1. Hold the project production lock, excluding live writers while inspecting output.
2. Discard abandoned partial directories, then reuse an exact matching completed
   operation, parent and parameter set if it remains active.
3. Otherwise allocate a `vNNNN` directory and write the ordinary production facts.
4. Run the fixed Blender or packaging producer into that directory.
5. Record artifact sizes and content digests, then write `record.json` last.

Completed versions are immutable and never reused for different work. An
unaccepted partial is disposable and its number may be reused after cleanup.
Canonical completed artifacts and lifecycle state are the desired-state facts;
failed job payloads and progress are never execution inputs.

## Current-worker monitoring

Each new operation gets a fresh UUID worker ID. Only a matching still-active
operation shares that worker. The in-memory thread pool owns input payloads.
Monitoring JSON contains status, times, result and bounded error text, with no
saved operation payload or continuation entry point.

After a failed or interrupted operation, use the same normal production action
with ordinary inputs. The service inspects actual completed output and starts
unmet production from the beginning. An interrupted old worker remains terminal.
There is no public job cancellation or continuation action.

Active workers are not pruned; terminal monitoring history is capped at 100
records, including abandoned workers found on first project access. Each worker's
`.worker.lock` lives beside its monitoring record, covers queueing and execution,
and is removed when the worker ends. Lock ownership protects live workers in
other server instances; abandoned lock files are removed during pruning.
Production versions and approval events have separate business ownership.

## Review gates

Promotion records a reviewer, notes, gate, and exact version. The server checks
that the version lineage contains both the production stage and its review
artifact:

| Gate | Entity | Required lineage |
| --- | --- | --- |
| appearance | character | UV/materials plus character review render |
| groom | character | groom plus character review render |
| rig | character | rig plus character review render |
| facial_expression | character | face rig plus character review render |
| animation | shot | animation plus shot preview render |

Groom, rig, face-rig, and final-render transitions require the selected source
to equal the version promoted at their preceding gate. The skill pauses for the
human decision; the server makes skipping the gate an invalid state.

## Blender boundary

Blender is launched in background mode with an optional exact source `.blend`,
the bundled worker path, and paths to structured request/result records. The
worker implements a deliberately small v1 vocabulary: primitive character
blockout, non-destructive mesh modifiers, UV/material setup, curve groom,
armature and shape keys, shot assembly, camera/light setup, keyframed blocking,
lip cues, secondary noise, and PNG renders.

The worker cannot select another script or execute a caller-supplied code string.
All returned artifact paths must resolve inside the reserved version directory.
The existing low-level Blender MCP stays independent, avoiding two competing
owners for arbitrary scene commands.

## Predictable invalid states

The producer prevents these states before work or completion:

- malformed IDs and versions that could escape project directories;
- unknown operation parameters;
- missing exact sources or missing `.blend` evidence;
- use of a source that has not passed its required review gate;
- implicit selection of latest assets during shot assembly or packaging;
- promotion without the required stage and review lineage;
- archival of a currently promoted version;
- completion records that name missing or external artifacts;
- more than the configured finite active-version limit.

Corrupt or missing artifact bytes make `validate_character` fail against the
recorded size and SHA-256 hash. V1 reports the problem; it does not repair or
replace accepted bytes.

## Ownership and retention

Repository source owns implementation, tests, documentation, and skill text.
The selected production project owns `.ceratops-blender`. Each character, shot,
and delivery owns its version directories and lifecycle events. Jobs own only
their bounded operational records.

Active versions default to a finite limit of 25 per entity. The server refuses
new versions at the limit until the user archives one. Archived production bytes
are intentionally retained because no permanent deletion exists in v1. Control
write scratch files are deleted by the writer in a `finally` path. There is no
runtime log file; the job record retains a bounded error summary.

## Repository lifecycle boundary

`sdlc/sdlc.yml` is the sole repository lifecycle declaration. Current Ceratops
compatibility supplies repository validation and test actions, declares the MCP
server source and manifest under `mcp-servers/ceratops_blender_mcp`, and routes
installation to `ceratops-mcp-server-lifecycle`. The repository still declares
no PyPI publication or hosted-service deployment.
