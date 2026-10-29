"""Bounded evidence reader for Event cover runtime calls; never executes Lua."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .model import AnimationStep, SceneDiagnostic, ScenePart


@dataclass(frozen=True, slots=True)
class AnimationOverride:
    target: str  # FindChild name, $mounted_root, or * for a component enumeration.
    animation: str
    loop: bool
    evidence: str
    sequence: tuple[AnimationStep, ...] = ()


@dataclass(frozen=True, slots=True)
class EventRuntime:
    purpose: str = "unknown"
    evidence: tuple[str, ...] = ()
    overrides: tuple[AnimationOverride, ...] = ()
    sequence_prefabs: tuple[str, ...] = ()
    background_renderer: str = "unknown"
    fade_in_seconds: float = 0.0
    hold_seconds: float = 0.0
    fade_out_seconds: float = 0.0


_ANIMATION = re.compile(
    r'\bSkeletonAnimationUtil\.SetAnimation\(\s*(\w+(?:\[\w+\])?)\s*,\s*0\s*,\s*'
    r'([\'"])(.*?)\2\s*,\s*(true|false)\b')
_MOUNT = re.compile(
    r'\b(\w+)\s*=\s*UIUtil\.SetEffectToUI\(\s*configData\.bg_main\s*,')
_CHILD = re.compile(
    r'\blocal\s+(\w+)\s*=\s*LuaUtil\.FindChild\(\s*(\w+)\s*,\s*'
    r'([\'"])(.*?)\3\s*,\s*true\s*\)')
_LONG_BRACKET = re.compile(r"\[(=*)\[")


def _mask_noncode(text: str, *, strings: bool = True) -> str:
    """Mask comments and optionally strings without changing source offsets."""
    result = list(text)
    index = 0
    while index < len(text):
        start = index
        comment = text.startswith("--", index)
        if comment:
            index += 2
        long_string = _LONG_BRACKET.match(text, index)
        if long_string:
            closing = "]" + long_string.group(1) + "]"
            end = text.find(closing, long_string.end())
            index = len(text) if end < 0 else end + len(closing)
        elif comment:
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
        elif text[index] in "'\"":
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
        else:
            index += 1
            continue
        if comment or strings:
            for offset in range(start, min(index, len(text))):
                if result[offset] not in "\r\n":
                    result[offset] = " "
    return "".join(result)


def _code_matches(pattern: str | re.Pattern, text: str):
    code = _mask_noncode(text)
    for match in re.finditer(pattern, text):
        if code[match.start():match.start() + 1].strip():
            yield match


def _code_search(pattern: str | re.Pattern, text: str):
    return next(_code_matches(pattern, text), None)


def _supported_conditions(text: str, offset: int, variable: str, instance: str | None) -> bool:
    """Accept only the audited enclosing guards, balancing other Lua blocks."""
    blocks: list[bool] = []
    code = _mask_noncode(text[:offset])
    tokens = re.finditer(
        r'\b(if|elseif)\b(.*?)\bthen\b|\b(?:for|while)\b.*?\bdo\b'
        r'|\b(function|repeat|do|end|until|else)\b', code, re.DOTALL)
    for token in tokens:
        keyword = token.group(1) or token.group(3)
        if keyword in {"if", "elseif"}:
            condition = re.sub(r'\s+', ' ', token.group(2)).strip()
            guards = {"configData", f"{variable} and uis"}
            if instance:
                guards.update({instance, f"{instance} and not {instance}:IsNull()"})
            supported = condition in guards
            if keyword == "elseif":
                if blocks:
                    blocks[-1] = False
            else:
                blocks.append(supported)
        elif keyword == "else":
            if blocks:
                blocks[-1] = False
        elif keyword in {"end", "until"}:
            if blocks:
                blocks.pop()
        elif keyword == "function":
            # A function body is not evidence that it executes. Only the
            # literal SetAnimation completion callback is supported here.
            preceding = tuple(_code_matches(_ANIMATION, text[:token.start()]))
            call = preceding[-1] if preceding else None
            blocks.append(bool(call and call.group(1) == variable and re.fullmatch(
                r'\s*,\s*0\s*,\s*', text[call.end():token.start()],
            )))
        else:
            blocks.append(True)
    return all(blocks)


def _source(path: Path) -> str:
    try:
        return _mask_noncode(path.read_text(encoding="utf-8-sig", errors="replace"), strings=False)
    except OSError:
        return ""


def _evidence(path: Path, text: str, offset: int) -> str:
    return f"{path}:{text.count(chr(10), 0, offset) + 1}"


def _load_bg(text: str, window: str) -> tuple[str, int]:
    start = _code_search(r'(?m)^function\s+' + re.escape(window) + r'\.LoadBg\(\s*\)', text)
    if not start:
        return "", 0
    end = _code_search(r'(?m)^(?:local\s+)?function\s+', text[start.end():])
    return text[start.end():start.end() + end.start()] if end else text[start.end():], start.end()


def _overrides(text: str, path: Path, targets: dict[str, str | tuple[str, ...]], offset: int = 0,
               full_text: str | None = None, *, instance: str | None = None
               ) -> tuple[AnimationOverride, ...]:
    # The supported scripts either set one animation directly or set in with a
    # completion callback that selects idle. Preserve the completion chain;
    # independent calls or branches are not an animation queue.
    grouped: dict[str, list[re.Match]] = {}
    for call in _code_matches(_ANIMATION, text):
        if call.group(1) in targets:
            grouped.setdefault(call.group(1), []).append(call)
    result = []
    for variable, calls in grouped.items():
        if not all(_supported_conditions(text, call.start(), variable, instance) for call in calls):
            continue
        if len(calls) > 1:
            if len(calls) != 2 or calls[0].group(4) != "false":
                continue
            # Recognize the actual nested completion callback, not two branch
            # alternatives that merely happen to name the same Spine child.
            conditions = [re.escape(variable) + r'\s+and\s+uis']
            if instance:
                escaped_instance = re.escape(instance)
                conditions.extend((escaped_instance,
                                   escaped_instance + r'\s+and\s+not\s+'
                                   + escaped_instance + r':IsNull\(\)'))
            guard = r'(?:if\s+(?:' + '|'.join(conditions) + r')\s+then\s*)?'
            if not re.fullmatch(r'\s*,\s*0\s*,\s*function\(\)\s*' + guard,
                                text[calls[0].end():calls[1].start()]):
                continue
        sequence = tuple(AnimationStep(
            call.group(3), call.group(4) == "true",
            _evidence(path, full_text or text, offset + call.start()),
        ) for call in calls)
        first = sequence[0]
        names = targets[variable]
        for name in (names if isinstance(names, tuple) else (names,)):
            result.append(AnimationOverride(
                name, first.animation, first.loop, first.evidence, sequence,
            ))
    return tuple(result)


def _component_loop(text: str, instance: str):
    """Find the exact component enumeration bound to this mounted object."""
    return _code_search(
        r'local\s+(\w+)\s*=\s*' + re.escape(instance) + r':GetComponentsInChildren\('
        r'typeof\(CS\.Spine\.Unity\.SkeletonAnimation\)\)\s*'
        r'for\s+i\s*=\s*1,\s*\1\.Length\s+do\s*'
        r'local\s+(\w+)\s*=\s*\1\[i\s*-\s*1\]\.gameObject', text)


def _named_child_lists(text: str, targets: dict[str, str]) -> dict[str, tuple[str, ...]]:
    """Bind a literal child list immediately traversed by the audited Lua loop."""
    aliases = {}
    pattern = (
        r'local\s+(\w+)\s*=\s*\{([^}]+)\}\s*'
        r'for\s+(\w+)\s*=\s*1,\s*#\1\s+do\s*'
        r'(?=SkeletonAnimationUtil\.SetAnimation\(\s*\1\[\3\]\s*,)'
    )
    for match in _code_matches(pattern, text):
        table, members, index = match.groups()
        if not re.fullmatch(r'\s*\w+(?:\s*,\s*\w+)*\s*,?\s*', members):
            continue
        names = tuple(name.strip() for name in members.strip().rstrip(',').split(','))
        if not all(name in targets for name in names):
            continue
        # Reassignments/mutations are outside this bounded syntax; retain the
        # Prefab state rather than attributing another object to a named child.
        if len(tuple(_code_matches(r'\b' + re.escape(table) + r'\s*=', text))) != 1:
            continue
        if _code_search(r'\b' + re.escape(table) + r'\[[^]]+\]\s*=', text):
            continue
        aliases[f'{table}[{index}]'] = tuple(targets[name] for name in names)
    return aliases


def _entrance_fade(text: str, instance: str) -> tuple[float, int | None]:
    """Read the local 0→1 tween that updates this entrance object's alpha."""
    escaped = re.escape(instance)
    candidates = []
    pattern = (r'\bLeanTween\.value\(\s*0\s*,\s*1\s*,\s*(\d+(?:\.\d*)?|\.\d+)\s*\)'
               r':setOnUpdate\(function\((\w+)\)(.*?)\bend\s*\)')
    for match in _code_matches(re.compile(pattern, re.DOTALL), text):
        duration = float(match.group(1))
        if not math.isfinite(duration) or duration <= 0:
            continue
        if not _supported_conditions(text, match.start(), instance, instance):
            continue
        initial = _code_search(r'\bSkeletonAnimationUtil\.SetAlpha\(\s*' + escaped + r'\s*,\s*0\s*\)',
                               text[:match.start()])
        if not initial or not _supported_conditions(text, initial.start(), instance, instance):
            continue
        value = re.escape(match.group(2))
        body = match.group(3)
        # Restrict the update to the audited flat callback body. Unknown
        # conditional/helper callbacks do not establish a scene-wide fade.
        if re.search(r'\b(?:if|function|for|while|repeat)\b', _mask_noncode(body)):
            continue
        if not all(_code_search(call, body) for call in (
            r'\bLuaUtil\.SetAlpha\(\s*' + escaped + r'\s*,\s*' + value + r'\s*,\s*true\s*\)',
            r'\bSkeletonAnimationUtil\.SetAlpha\(\s*' + escaped + r'\s*,\s*' + value + r'\s*\)',
        )):
            continue
        candidates.append((duration, match.start()))
    return candidates[0] if len(candidates) == 1 else (0.0, None)


def _entrance_transition(text: str, fade_offset: int | None) -> tuple[float, float, tuple[int, ...]]:
    """Read only the audited fade-complete → hold → next-stage/fade-out chain.

    This describes game window transitions; it does not shorten the underlying
    Prefab clips when an individual entrance stage is exported.
    """
    if fade_offset is None:
        return 0.0, 0.0, ()
    number = r'(\d+(?:\.\d*)?|\.\d+)'
    pattern = (
        r'LeanTween\.value\(\s*0\s*,\s*1\s*,\s*[\d.]+\s*\)'
        r':setOnUpdate\(function\(\w+\).*?\bend\s*\)\s*'
        r':setOnComplete\(function\(\)\s*RemoveTween\(tweenId\)\s*'
        r'tweenId\s*=\s*(LeanTween\.delayedCall\(\s*' + number + r'\s*,\s*function\(\)\s*'
        r'PlayDailyEntranceAnim\(num\s*\+\s*1,\s*endNum\)\s*RemoveTween\(tweenId\)\s*'
        r'if\s+num\s*<\s*endNum\s+then\s*'
        r'(?:post_blur:SetActive\(false\)\s*)?'
        r'tweenId\s*=\s*(LeanTween\.value\(\s*1\s*,\s*0\s*,\s*' + number + r'\s*\)'
        r':setOnUpdate\(function\((\w+)\)(.*?)\bend\s*\)'
        r':setOnComplete\(function\(\)\s*RemoveTween\(tweenId\)\s*holder:Dispose\(\)\s*end\s*\))'
        r')'
    )
    match = re.match(pattern, text[fade_offset:], re.DOTALL)
    if not match:
        return 0.0, 0.0, ()
    hold, fade_out = float(match.group(2)), float(match.group(4))
    if not all(math.isfinite(value) and value > 0 for value in (hold, fade_out)):
        return 0.0, 0.0, ()
    value, body = re.escape(match.group(5)), match.group(6)
    if re.search(r'\b(?:if|function|for|while|repeat)\b', _mask_noncode(body)):
        return 0.0, 0.0, ()
    if not all(_code_search(call, body) for call in (
        r'\bLuaUtil\.SetAlpha\(\s*o\s*,\s*' + value + r'\s*,\s*true\s*\)',
        r'\bSkeletonAnimationUtil\.SetAlpha\(\s*o\s*,\s*' + value + r'\s*\)',
    )):
        return 0.0, 0.0, ()
    return hold, fade_out, (fade_offset + match.start(1), fade_offset + match.start(3))


def load_event_runtime(folder: Path, show_id: str | None, prefab: str) -> EventRuntime:
    """Recognize the audited LoadBg and entrance-loop syntax in this version."""
    if not show_id:
        return EventRuntime()
    window = f"Activity{show_id if show_id != '1' else ''}DungeonWindow"
    dungeon_path = folder / f"{window}.lua"
    dungeon = _source(dungeon_path)
    load_bg, offset = _load_bg(dungeon, window)
    mount = _code_search(_MOUNT, load_bg)
    if mount:
        instance = mount.group(1)
        targets = {match.group(1): match.group(4) for match in _code_matches(_CHILD, load_bg)
                   if match.group(2) == instance}
        targets[instance] = "$mounted_root"
        targets.update(_named_child_lists(load_bg, targets))
        component_loop = _component_loop(load_bg, instance)
        if component_loop:
            targets[component_loop.group(2)] = "*"
        return EventRuntime(
            "mounted_background", (_evidence(dungeon_path, dungeon, offset + mount.start()),),
            _overrides(load_bg, dungeon_path, targets, offset, dungeon, instance=instance),
            background_renderer="prefab",
        )

    entrance_path = folder / f"Activity{show_id}EntranceAnimWindow.lua"
    entrance = _source(entrance_path)
    table = _code_search(r'\blocal\s+PATH_LOOKUP\s*=\s*\{([^}]+)\}', entrance)
    if not table or not load_bg:
        return EventRuntime()
    entries = re.findall(r'\[\s*(\d+)\s*\]\s*=\s*([\'"])(.*?)\2', table.group(1))
    paths = tuple(path.replace('\\', '/') for _, _, path in sorted(entries, key=lambda row: int(row[0])))
    instantiated = _code_search(
        r'local\s+path\s*=\s*PATH_LOOKUP\[num\]\s*'
        r'local\s+o\s*=\s*ResourceManager\.Instantiate\(path\)', entrance)
    if not instantiated or not _code_search(r'UIUtil\.SetObjectToUI\(o,\s*holder\)', entrance):
        return EventRuntime()
    evidence = (_evidence(dungeon_path, dungeon, offset),
                _evidence(entrance_path, entrance, table.start()))
    # Ignore unrelated methods using the same local variable spelling. Audited
    # decompiled functions close at column zero; other layouts stay unknown.
    play_start = _code_search(r'(?m)^local function PlayDailyEntranceAnim\(num,\s*endNum\)', entrance)
    play_end = _code_search(r'(?m)^end\b', entrance[play_start.end():]) if play_start else None
    if not play_start or not play_end:
        return EventRuntime()
    play_offset = play_start.end()
    play = entrance[play_offset:play_offset + play_end.start()]
    renderer = "unknown"
    manager_path = folder / "ActivityDungeonMgr.lua"
    manager = _source(manager_path)
    map_call = _code_search(
        r'\b(?:if|elseif)\s+' + re.escape(show_id)
        + r'\s*==\s*(?:data\.show_id|showId)\s+then\s*'
        r'AbyssExploreMgr\.Enter\(EXPLORE_MAP_ID\.\w+', manager)
    if map_call:
        renderer = "exploration_map_unsupported"
        evidence += (_evidence(manager_path, manager, map_call.start()),)
    component_loop = _component_loop(play, "o")
    overrides = _overrides(play, entrance_path, {component_loop.group(2): "*"},
                           play_offset, entrance, instance="o") if component_loop else ()
    fade, fade_offset = _entrance_fade(play, "o")
    hold, fade_out, transition_offsets = _entrance_transition(play, fade_offset)
    if fade_offset is not None:
        evidence += (_evidence(entrance_path, entrance, play_offset + fade_offset),)
    evidence += tuple(_evidence(entrance_path, entrance, play_offset + position) for position in transition_offsets)
    if len(paths) == 1 and paths[0].casefold() == prefab.casefold():
        # All descendants are targeted only when the exact component enumeration
        # and its gameObject alias occur in the instantiated entrance object.
        return EventRuntime("entrance_only", evidence, overrides, paths, renderer, fade, hold, fade_out)
    stem = prefab[:-7].casefold() + "_" if prefab.casefold().endswith(".prefab") else ""
    if (len(paths) > 1 and stem and all(path.casefold().startswith(stem) for path in paths)
            and _code_search(r'PlayDailyEntranceAnim\(num\s*\+\s*1,\s*endNum\)', play)
            and _code_search(r'holder:Dispose\(\)', play)):
        return EventRuntime("sequential_entrance", evidence, overrides, paths, renderer, fade, hold, fade_out)
    return EventRuntime()


def runtime_diagnostics(runtime: EventRuntime) -> tuple[SceneDiagnostic, ...]:
    details = (("purpose", runtime.purpose), ("evidence", ";".join(runtime.evidence)),
               ("background_renderer", runtime.background_renderer),
               ("fade_in_seconds", str(runtime.fade_in_seconds)))
    if runtime.purpose == "unknown":
        return (SceneDiagnostic(
            "event_runtime_unknown", "Event runtime purpose is unknown; retaining Prefab animation metadata",
            severity="warning", details=details),)
    return (SceneDiagnostic(
        "event_runtime_purpose", f"Event cover purpose: {runtime.purpose}",
        severity="warning", details=details),)


def apply_event_runtime(parts: tuple[ScenePart, ...], runtime: EventRuntime
                        ) -> tuple[tuple[ScenePart, ...], tuple[SceneDiagnostic, ...]]:
    diagnostics = list(runtime_diagnostics(runtime))
    if runtime.overrides and any(p.sprite is not None for p in parts) and not any(p.spine is not None for p in parts):
        diagnostics.append(SceneDiagnostic(
            "event_runtime_spine_not_applicable", "Lua Spine calls do not apply to this Prefab without Spine parts",
            severity="warning", details=(("purpose", runtime.purpose),)))
        return parts, tuple(diagnostics)
    if runtime.purpose in {"mounted_background", "entrance_only", "entrance_stage"} and not runtime.overrides:
        diagnostics.append(SceneDiagnostic(
            "event_runtime_animation_unknown", "Runtime animation is not evidenced; retaining Prefab metadata",
            severity="warning", details=(("purpose", runtime.purpose),)))
    result = list(parts)
    for override in runtime.overrides:
        matches = [i for i, part in enumerate(result) if part.spine is not None and (
            override.target == "*"
            or (override.target == "$mounted_root" and part.hierarchy_path == part.game_object_name)
            or (override.target != "$mounted_root" and part.game_object_name == override.target))]
        # FindChild ambiguity is retained rather than choosing a same-named sibling.
        if not matches or (override.target != "*" and len(matches) != 1):
            diagnostics.append(SceneDiagnostic(
                "event_runtime_target_unmatched", "Lua animation target is absent or ambiguous in this Prefab",
                severity="warning", details=(("target", override.target), ("evidence", override.evidence))))
            continue
        for index in matches:
            part = result[index]
            result[index] = replace(part, spine=replace(
                part.spine, default_animation=override.animation, loop=override.loop,
                animation_sequence=override.sequence))
            diagnostics.append(SceneDiagnostic(
                "event_runtime_animation", "Animation and loop selected by same-version Lua runtime",
                severity="warning", part_id=part.part_id, source=part.object_ref,
                details=(("target", override.target), ("animation", override.animation),
                         ("loop", str(override.loop).lower()), ("evidence", override.evidence))))
    return tuple(result), tuple(diagnostics)
