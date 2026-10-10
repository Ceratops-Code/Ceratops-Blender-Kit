---
name: ceratops-blender-kit
description: Orchestrate versioned character and shot production with Ceratops-Blender-MCP, including exact-source selection, long-job handling, review renders, approvals, and packaging. Use for goal-oriented local Blender production workflows, not arbitrary Blender Python or low-level scene commands.
---

# Ceratops-Blender-Kit

## Goal

Turn a character, shot, or episode goal into a reviewable sequence of exact
versions through Ceratops-Blender-MCP. Preserve every accepted version, keep
reads separate from writes, and stop at the appearance, groom, rig,
facial-expression, and animation review gates for the user's decision.

## Core rules

- Use Ceratops-Blender-MCP for the workflow tools in this skill. Use the existing
  low-level Blender MCP only for bespoke edits the user separately requests; do
  not send arbitrary Python through this server.
- Start with `inspect_project` and `list_assets`. Reads must not initialize or
  repair a project.
- Supply the desired operation, exact source version, and output parameters.
  A fresh call checks completed production outputs and reuses a matching output.
- Pass exact `vNNNN` source versions. Never infer or substitute “latest.”
- Use a returned worker ID only with `get_job_status` to observe that worker's
  liveness, result, or error. It is never input to a production write.
- Never continue from a failed or incomplete version. Never archive a promoted
  version until another version has replaced every gate that points to it.
- Do not call `render_shot_final` before animation approval, or package work
  that the user has not accepted for its intended delivery.

## Character workflow

1. Inspect the project and existing character ID.
2. If references exist, call `import_character_reference`; pass its exact version
   to `create_character`. Otherwise create the character without a reference
   version.
3. Wait for each job, then pass its returned version through
   `create_character_mesh`, `retopologize_character`, and
   `create_uv_and_materials`.
4. Call `render_character_review` on the look-development version. Present the
   produced review artifact and ask whether appearance is approved. Only after
   approval call `promote_version` with gate `appearance` and the review version.
5. Call `groom_character` from that promoted version, render a new character
   review, and ask for groom approval. Promote the review version at gate
   `groom` only after approval.
6. Call `rig_character` from the groom-promoted version. Run
   `validate_character`, render a review, and ask for rig approval. Promote the
   review version at gate `rig` only after approval.
7. Call `build_face_rig` from the rig-promoted version. Render expressions for
   review and ask for facial-expression approval. Promote the review version at
   gate `facial_expression` only after approval.
8. Run `validate_character` on the selected delivery version. Compare versions
   when a review needs a concrete change summary. Call `package_asset` only for
   the exact accepted version.

If a review fails, choose the intended source version and changed output parameters.
Do not alter or relabel the rejected version; archive it only when the user wants
it removed from the active set.

## Shot workflow

1. Call `create_shot` with explicit frame bounds and FPS; wait for completion.
2. Select exact promoted character versions and call `assemble_shot` with the
   complete `asset_versions` map. Do not silently replace a selected character.
3. Pass each exact result through `setup_camera`, `light_shot`, and
   `animate_shot`. Add `sync_lips` only with explicit frame/value cues. Add
   `add_secondary_motion` only after primary motion exists.
4. Call `render_shot_preview` for the exact animation source. Present the frames
   and ask whether animation is approved. Only after approval call
   `promote_version` with gate `animation` and the preview version.
5. Call `render_shot_final` from that exact promoted preview version. Wait for
   completion and inspect the returned final-render version.
6. Use `package_asset` for one exact shot or `package_episode` with a complete
   map of shot IDs to exact final versions.

## Job failures

- Report the exact failed or interrupted step and bounded error, and correct
  its cause within the active execution request. Production operations always
  start through their normal entry point using current project and artifact
  state; no old worker or saved execution progress is consumed.
- A missing Blender binary requires setting `CERATOPS_BLENDER_EXECUTABLE`; do not
  install Blender or change machine-wide configuration without a separate
  request.

## Output

Report the exact character, shot, or delivery versions created; the review gates
approved or still pending; and any failed, cancelled, or interrupted job that
still needs action. Do not claim artistic approval from a successful render or
check result—the reviewer supplies approval, and `promote_version` records
it.
