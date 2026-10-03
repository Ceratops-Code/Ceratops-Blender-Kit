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
MCP host
  -> server.py                 named, typed MCP tools
     -> service.py             transition rules and review gates
        -> storage.py          immutable versions and append-only events
        -> jobs.py             stable, resumable bounded job records
        -> blender_runtime.py  owned Blender subprocess boundary
           -> execute_blender_job.py  fixed bpy operations inside Blender
```

`server.py` is the public adapter. `service.py` rejects unknown operations and
unknown parameters before any job is created. `storage.py` is authoritative for
project paths, version allocation, exact-source reads, event state, and hashes.
`jobs.py` owns operational status and retention. `blender_runtime.py` is the only
normal-Python subprocess launcher; its worker accepts structured data and an
allowlisted operation name, never Python source.

## Read and write separation

Inspection, listing, comparison, job status, and character validation are reads.
They do not initialize `.ceratops-blender` or repair state. Every state-changing
tool is named as a production or lifecycle action and requires a request ID.

The server never resolves an implicit latest source version. Each dependent
operation receives a `vNNNN` source. This makes retries and review decisions
refer to stable bytes and prevents an ambient newer version from entering a
shot or package.

## Version transaction

1. Acquire the project file lock and reserve a never-used `vNNNN` directory.
2. Write `request.json` with the exact operation, parent, request ID, and public
   parameters.
3. Run the fixed Blender or packaging producer directly into that directory.
4. Hash every declared output.
5. Write `record.json` last. Its presence is the completion boundary.

A failed producer writes `failure.json`; that version number is never reused.
Completed artifacts and `record.json` are not overwritten. Promotion and
archival are separate immutable event files, so lifecycle state never changes
the version's bytes.

The project file lock serializes control-plane writes within cooperating server
processes. It is not advertised as the future Ceratops OS process-group lock and
does not make that unfinished infrastructure step real.

## Jobs and recovery

The job ID is UUIDv5 over the initialized project ID and caller request ID. The
job record binds that ID to a hash of the operation and payload. A duplicate
request with the same payload returns the retained record; a conflicting payload
fails before work starts.

Queued and running jobs execute in a bounded local thread pool. Cancellation is
cooperative and the Blender runtime terminates its owned process. Failed,
cancelled, or interrupted jobs may resume under the same ID; the next attempt
creates a new production version rather than rewriting the failed one. A job
observed as `running` after an MCP server restart becomes `interrupted` because
the new process has no owned future for it.

Active jobs are never pruned. Terminal job history is capped at 100 records by
default. This bounds producer-owned operational history; production versions and
approval events have separate ownership and are not job-log cleanup targets.

## Review gates

Promotion records a reviewer, notes, gate, and exact version. The server verifies
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
- duplicate request IDs with different payloads;
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
compatibility supplies repository validation and test actions. This repository
does not declare placeholder build, deploy, publish, or exact-artifact actions.
Those become legitimate only after a real producer and consumer contract exists;
the referenced Ceratops future plan does not itself authorize or implement them.
