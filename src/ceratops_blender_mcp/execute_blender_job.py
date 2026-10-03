"""Execute the bundled allowlisted Blender operations inside Blender Python.

This module is launched only by :mod:`ceratops_blender_mcp.blender_runtime`.
Requests contain structured parameters and fixed operation names; they cannot
carry Python source or select another script. The worker writes outputs directly
inside the caller-reserved immutable version directory.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
from mathutils import Vector  # type: ignore[import-not-found]

ALLOWED_OPERATIONS = {
    "create_character",
    "create_character_mesh",
    "retopologize_character",
    "create_uv_and_materials",
    "groom_character",
    "rig_character",
    "build_face_rig",
    "render_character_review",
    "create_shot",
    "assemble_shot",
    "setup_camera",
    "light_shot",
    "animate_shot",
    "sync_lips",
    "add_secondary_motion",
    "render_shot_preview",
    "render_shot_final",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])


def _mesh_objects() -> list[Any]:
    return sorted(
        (item for item in bpy.context.scene.objects if item.type == "MESH"),
        key=lambda item: item.name,
    )


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.curves, bpy.data.armatures, bpy.data.materials):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def _add_part(
    name: str,
    primitive: str,
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> Any:
    if primitive == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=location)
    elif primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(location=location)
    else:
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def _create_character(parameters: dict[str, Any]) -> None:
    _clear_scene()
    height = float(parameters.get("height_m", 1.75))
    factor = height / 1.75
    body_type = str(parameters.get("body_type", "neutral"))
    width = {"slender": 0.82, "neutral": 1.0, "heroic": 1.18}[body_type]
    style = str(parameters.get("style", "stylized"))
    head_scale = 1.12 if style == "stylized" else 0.95
    _add_part(
        "CBK_Head",
        "sphere",
        (0.0, 0.0, 1.55 * factor),
        (0.18 * head_scale, 0.16 * head_scale, 0.22 * head_scale),
    )
    _add_part(
        "CBK_Torso",
        "cube",
        (0.0, 0.0, 1.05 * factor),
        (0.28 * width, 0.16 * width, 0.42),
    )
    _add_part(
        "CBK_Hips",
        "sphere",
        (0.0, 0.0, 0.70 * factor),
        (0.25 * width, 0.16 * width, 0.20),
    )
    for side, x in (("L", -0.38), ("R", 0.38)):
        _add_part(f"CBK_Arm_{side}", "cylinder", (x, 0.0, 1.05 * factor), (0.08, 0.08, 0.38))
        _add_part(f"CBK_Leg_{side}", "cylinder", (x * 0.45, 0.0, 0.34 * factor), (0.10, 0.10, 0.38))


def _create_character_mesh(parameters: dict[str, Any]) -> None:
    levels = int(parameters.get("subdivision_levels", 1))
    for obj in _mesh_objects():
        modifier = obj.modifiers.new("CBK_Subdivision", "SUBSURF")
        modifier.levels = max(0, min(levels, 2))
        modifier.render_levels = modifier.levels


def _retopologize(parameters: dict[str, Any]) -> None:
    target = max(500, int(parameters.get("target_faces", 12000)))
    meshes = _mesh_objects()
    current = sum(len(obj.data.polygons) for obj in meshes) or target
    ratio = max(0.05, min(1.0, target / current))
    for obj in meshes:
        modifier = obj.modifiers.new("CBK_Retopology", "DECIMATE")
        modifier.ratio = ratio


def _create_uv_materials(parameters: dict[str, Any]) -> None:
    color = parameters.get("base_color", [0.45, 0.18, 0.12, 1.0])
    roughness = float(parameters.get("roughness", 0.5))
    material = bpy.data.materials.get("CBK_Character") or bpy.data.materials.new("CBK_Character")
    material.diffuse_color = tuple(float(item) for item in color)
    material.roughness = roughness
    for obj in _mesh_objects():
        if not obj.data.materials:
            obj.data.materials.append(material)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0))
        bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(False)


def _groom(parameters: dict[str, Any]) -> None:
    strand_count = max(4, min(int(parameters.get("strand_count", 24)), 200))
    style = str(parameters.get("style", "short"))
    length = {"short": 0.18, "bob": 0.34, "mohawk": 0.28}[style]
    curve_data = bpy.data.curves.new("CBK_Groom", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.bevel_depth = 0.004
    for index in range(strand_count):
        angle = (2.0 * math.pi * index) / strand_count
        spline = curve_data.splines.new("BEZIER")
        spline.bezier_points.add(2)
        for point_index, point in enumerate(spline.bezier_points):
            radius = 0.14 - point_index * 0.025
            lateral = 0.45 if style == "mohawk" else 1.0
            point.co = (
                math.cos(angle) * radius * lateral,
                math.sin(angle) * radius,
                1.68 - point_index * (length / 2.0),
            )
            point.handle_left_type = "AUTO"
            point.handle_right_type = "AUTO"
    groom = bpy.data.objects.new("CBK_Groom", curve_data)
    bpy.context.collection.objects.link(groom)


def _rig() -> None:
    armature = bpy.data.armatures.new("CBK_Rig")
    rig = bpy.data.objects.new("CBK_Rig", armature)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bones = [
        ("root", (0, 0, 0), (0, 0, 0.7), None),
        ("spine", (0, 0, 0.7), (0, 0, 1.35), "root"),
        ("head", (0, 0, 1.35), (0, 0, 1.75), "spine"),
        ("arm.L", (0, 0, 1.3), (-0.65, 0, 1.15), "spine"),
        ("arm.R", (0, 0, 1.3), (0.65, 0, 1.15), "spine"),
    ]
    created: dict[str, Any] = {}
    for name, head, tail, parent in bones:
        bone = armature.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        if parent:
            bone.parent = created[parent]
        created[name] = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    for obj in _mesh_objects():
        modifier = obj.modifiers.new("CBK_Armature", "ARMATURE")
        modifier.object = rig
        obj.parent = rig


def _face_rig() -> None:
    meshes = _mesh_objects()
    if not meshes:
        raise RuntimeError("face rig requires a character mesh")
    head = next((item for item in meshes if "head" in item.name.lower()), meshes[0])
    if head.data.shape_keys is None:
        head.shape_key_add(name="Basis")
    for name in ("Smile", "Frown", "Blink_L", "Blink_R", "Mouth_Open"):
        if name not in head.data.shape_keys.key_blocks:
            head.shape_key_add(name=name)


def _create_shot(parameters: dict[str, Any]) -> None:
    _clear_scene()
    scene = bpy.context.scene
    scene.frame_start = int(parameters.get("frame_start", 1))
    scene.frame_end = int(parameters.get("frame_end", 120))
    scene.render.fps = int(parameters.get("fps", 24))


def _assemble(parameters: dict[str, Any]) -> None:
    asset_files = parameters.get("asset_files", [])
    for asset_path in asset_files:
        path = str(asset_path)
        with bpy.data.libraries.load(path, link=False) as (source, target):
            target.objects = [name for name in source.objects if name.startswith("CBK_")]
        for obj in target.objects:
            if obj is not None:
                bpy.context.collection.objects.link(obj)


def _setup_camera(parameters: dict[str, Any]) -> None:
    camera_data = bpy.data.cameras.get("CBK_Camera") or bpy.data.cameras.new("CBK_Camera")
    camera = bpy.data.objects.get("CBK_Camera") or bpy.data.objects.new("CBK_Camera", camera_data)
    if camera.name not in bpy.context.collection.objects:
        bpy.context.collection.objects.link(camera)
    camera.location = tuple(parameters.get("position", [4.0, -6.0, 3.0]))
    camera.data.lens = float(parameters.get("lens_mm", 50.0))
    target = Vector(parameters.get("target", [0.0, 0.0, 1.0]))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def _light(parameters: dict[str, Any]) -> None:
    strength = float(parameters.get("intensity", 1000.0))
    for name, location, energy in (
        ("CBK_Key", (4.0, -4.0, 5.0), strength),
        ("CBK_Fill", (-4.0, -2.0, 3.0), strength * 0.4),
        ("CBK_Rim", (0.0, 4.0, 4.0), strength * 0.7),
    ):
        data = bpy.data.lights.get(name) or bpy.data.lights.new(name, "AREA")
        data.energy = energy
        obj = bpy.data.objects.get(name) or bpy.data.objects.new(name, data)
        if obj.name not in bpy.context.collection.objects:
            bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (0.0, 0.0, 0.0)


def _animate(parameters: dict[str, Any]) -> None:
    scene = bpy.context.scene
    moving = next(
        (obj for obj in bpy.context.scene.objects if obj.type in {"ARMATURE", "MESH"}),
        None,
    )
    if moving is None:
        raise RuntimeError("animation requires an assembled object")
    interpolation = str(parameters.get("interpolation", "BEZIER"))
    edit_preferences = bpy.context.preferences.edit
    if hasattr(edit_preferences, "keyframe_new_interpolation_type"):
        edit_preferences.keyframe_new_interpolation_type = interpolation
    start = scene.frame_start
    end = scene.frame_end
    moving.location.x = -0.5
    moving.keyframe_insert(data_path="location", frame=start)
    moving.location.x = 0.5
    moving.keyframe_insert(data_path="location", frame=end)


def _sync_lips(parameters: dict[str, Any]) -> None:
    cues = parameters.get("cues", [])
    face = next(
        (
            obj
            for obj in _mesh_objects()
            if obj.data.shape_keys and "Mouth_Open" in obj.data.shape_keys.key_blocks
        ),
        None,
    )
    if face is None:
        raise RuntimeError("lip sync requires a Mouth_Open face shape key")
    key = face.data.shape_keys.key_blocks["Mouth_Open"]
    for cue in cues:
        frame = int(cue["frame"])
        key.value = float(cue.get("value", 1.0))
        key.keyframe_insert(data_path="value", frame=frame)


def _secondary_motion(parameters: dict[str, Any]) -> None:
    strength = float(parameters.get("strength", 0.25))
    scene = bpy.context.scene
    moving = next(
        (obj for obj in scene.objects if obj.animation_data and obj.animation_data.action),
        None,
    )
    if moving is None:
        raise RuntimeError("secondary motion requires primary object animation")
    middle = scene.frame_start + (scene.frame_end - scene.frame_start) // 2
    moving.rotation_euler.y = 0.0
    moving.keyframe_insert(data_path="rotation_euler", frame=scene.frame_start)
    moving.rotation_euler.y = strength
    moving.keyframe_insert(data_path="rotation_euler", frame=middle)
    moving.rotation_euler.y = 0.0
    moving.keyframe_insert(data_path="rotation_euler", frame=scene.frame_end)


def _select_render_engine(scene: Any) -> None:
    """Select the maintained Eevee identifier across supported Blender releases."""

    engines = {item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items}
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if candidate in engines:
            scene.render.engine = candidate
            return
    raise RuntimeError("this Blender build does not expose an Eevee render engine")


def _render(parameters: dict[str, Any], output_root: Path, *, animation: bool) -> list[Path]:
    scene = bpy.context.scene
    output_root.mkdir(parents=True, exist_ok=True)
    _select_render_engine(scene)
    scene.render.resolution_x = int(parameters.get("resolution_x", 1280))
    scene.render.resolution_y = int(parameters.get("resolution_y", 720))
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    if animation:
        scene.frame_start = int(parameters.get("frame_start", scene.frame_start))
        scene.frame_end = int(parameters.get("frame_end", scene.frame_end))
        scene.render.filepath = str(output_root / "frame_")
        bpy.ops.render.render(animation=True)
        return sorted(output_root.glob("frame_*.png"))
    scene.render.filepath = str(output_root / "review.png")
    bpy.ops.render.render(write_still=True)
    return [output_root / "review.png"]


def _render_character_review(parameters: dict[str, Any], output_root: Path) -> list[Path]:
    _light({"intensity": 800.0})
    scene = bpy.context.scene
    output_root.mkdir(parents=True, exist_ok=True)
    _select_render_engine(scene)
    scene.render.resolution_x = int(parameters.get("resolution_x", 960))
    scene.render.resolution_y = int(parameters.get("resolution_y", 960))
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    angle_count = max(1, min(int(parameters.get("angle_count", 1)), 8))
    artifacts: list[Path] = []
    for index in range(angle_count):
        angle = (2.0 * math.pi * index) / angle_count
        _setup_camera(
            {
                "lens_mm": 55.0,
                "position": [math.cos(angle) * 4.0, math.sin(angle) * 4.0, 2.0],
                "target": [0.0, 0.0, 1.0],
            }
        )
        output = output_root / f"review_{index + 1:02d}.png"
        scene.render.filepath = str(output)
        bpy.ops.render.render(write_still=True)
        artifacts.append(output)
    return artifacts


def _execute(request: dict[str, Any]) -> list[Path]:
    operation = str(request["operation"])
    if operation not in ALLOWED_OPERATIONS:
        raise RuntimeError(f"unsupported Blender operation: {operation}")
    parameters = dict(request.get("parameters", {}))
    output_root = Path(request["output_root"]).resolve()
    if operation == "create_character":
        _create_character(parameters)
    elif operation == "create_character_mesh":
        _create_character_mesh(parameters)
    elif operation == "retopologize_character":
        _retopologize(parameters)
    elif operation == "create_uv_and_materials":
        _create_uv_materials(parameters)
    elif operation == "groom_character":
        _groom(parameters)
    elif operation == "rig_character":
        _rig()
    elif operation == "build_face_rig":
        _face_rig()
    elif operation == "create_shot":
        _create_shot(parameters)
    elif operation == "assemble_shot":
        _assemble(parameters)
    elif operation == "setup_camera":
        _setup_camera(parameters)
    elif operation == "light_shot":
        _light(parameters)
    elif operation == "animate_shot":
        _animate(parameters)
    elif operation == "sync_lips":
        _sync_lips(parameters)
    elif operation == "add_secondary_motion":
        _secondary_motion(parameters)

    artifacts: list[Path] = []
    if operation == "render_character_review":
        artifacts.extend(_render_character_review(parameters, output_root))
    elif operation in {"render_shot_preview", "render_shot_final"}:
        artifacts.extend(_render(parameters, output_root, animation=True))
    output_blend = Path(request["output_blend"]).resolve()
    output_blend.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    return [output_blend, *artifacts]


def main() -> None:
    args = _arguments()
    result_path = Path(args.result).resolve()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        artifacts = _execute(request)
        result = {
            "schema": "ceratops-blender-worker-result.v1",
            "status": "completed",
            "artifacts": [str(path) for path in artifacts],
        }
    except Exception as exc:  # Blender must return a structured boundary failure.
        result = {
            "schema": "ceratops-blender-worker-result.v1",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=8),
        }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
