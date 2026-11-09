"""Evidenced click callback grammars; unknown Lua is retained with diagnostics."""

from dataclasses import replace
from pathlib import Path
import hashlib
import re

from .model import AnimationStep, SceneDiagnostic

_OVERLAY = """local OnClick = function(skeletonAnimation, animationName, gesture)
local go = skeletonAnimation.gameObject
local track = 1
local current = skeletonAnimation.state:GetCurrent(track)
if nil == current or current.IsComplete then
SkeletonAnimationUtil.SetAnimation(go, track, animationName, false)
end
end
return {OnClick = OnClick}"""
_ACTION = """local CLICK_INTERVAL = 0
local OnClick = function(skeletonAnimation, animationName, gesture)
local go = skeletonAnimation.gameObject
local track = 0
local current = skeletonAnimation.state:GetCurrent(track)
if nil == current or current.Animation.Name ~= animationName or current.TrackTime >= CLICK_INTERVAL then
SkeletonAnimationUtil.SetAnimation(go, track, animationName, false, function()
SkeletonAnimationUtil.SetAnimation(go, track, "idle", true)
end)
end
end
return {OnClick = OnClick}"""
_ACTION_NAMES = {f"touch_cardspine_{name}_3" for name in ("10098", "10102", "10103", "10111", "10119", "10120")}


def _normalized(text):
    from .lua_config import _mask_lua_comments

    return re.sub(r"\s+", "", _mask_lua_comments(text))


def parse_callback(event, text, evidence):
    normalized = _normalized(text)
    overlay = event.callback == "touch_cardspine_10016_3" and normalized == _normalized(_OVERLAY)
    action = event.callback in _ACTION_NAMES and normalized == _normalized(_ACTION)
    if not (overlay or action) or event.gesture.casefold() != "click" or not event.animation:
        return event, (
            SceneDiagnostic(
                "unsupported_interaction_callback",
                f"Unverified click callback {event.callback}",
                source=event.target,
                details=(("callback", event.callback), ("evidence", evidence)),
            ),
        )
    source = (evidence, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest())
    sequence = (AnimationStep(event.animation, False, evidence),)
    if action:
        sequence += (AnimationStep("idle", True, evidence),)
    gate = (
        "nil == current or current.IsComplete"
        if overlay
        else "nil == current or current.Animation.Name ~= animationName or current.TrackTime >= 0"
    )
    return replace(event, track=1 if overlay else 0, sequence=sequence, gate=gate, evidence=source), ()


def lua_directory(root):
    source = root.evidence.split(";", 1)[0].rsplit(":", 1)[0]
    directory = Path(source).parent
    return directory if directory.name == str(root.version_timestamp) else None


def source_fingerprints(root):
    directory = lua_directory(root)
    if directory is None or not directory.is_dir():
        return ()
    return tuple(
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(directory.glob("touch_*.lua"))
    )


def apply_callbacks(events, root):
    directory = lua_directory(root)
    result = []
    notes = []
    for event in events:
        if not event.callback:
            result.append(event)
            continue
        # A serialized script name must remain one filename in the same version.
        if not re.fullmatch(r"[A-Za-z0-9_]+", event.callback) or directory is None:
            text = ""
            evidence = root.evidence
        else:
            path = directory / (event.callback + ".lua")
            evidence = str(path) + ":1"
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                text = ""
        parsed, diagnostics = parse_callback(event, text, evidence)
        result.append(parsed)
        notes.extend(diagnostics)
    return tuple(result), tuple(notes)
