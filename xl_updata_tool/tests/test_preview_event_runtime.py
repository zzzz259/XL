from __future__ import annotations

import pytest

from app.features.preview.portrait_config import builder
from app.features.preview.portrait_config.asset_index import AssetIndex, IndexedAsset
from app.features.preview.portrait_config.lua_config import load_eventcover_roots
from app.features.preview.portrait_config.event_runtime import load_event_runtime
from app.features.preview.portrait_config.model import AssetRef
from app.features.preview.portrait_config.unity_scene import UnitySceneParseResult, UnityScenePart


VERSION = 402
PREFIX = "Assets/Art/Models/UI_spine/prefab/Eventcovers/"


def _root(tmp_path, event_id, show_id, dungeon="", entrance="", manager=""):
    folder = tmp_path / "lua" / str(VERSION)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "BaseActivityStageConfig.lua").write_text(
        f'BaseActivityStageConfig = {{ [1] = {{ id = 1, show_id = {show_id}, '
        f'bg_main = "{PREFIX}eventcovers_{event_id}.prefab" }} }}', encoding="utf-8")
    for name, content in ((f"Activity{show_id if show_id != 1 else ''}DungeonWindow.lua", dungeon),
                          (f"Activity{show_id}EntranceAnimWindow.lua", entrance),
                          ("ActivityDungeonMgr.lua", manager)):
        (folder / name).write_text(content, encoding="utf-8")
    return load_eventcover_roots(tmp_path / "lua", VERSION)[0]


def _mounted(show_id=3, animation="idle", child="eventcovers_0003_2"):
    return f'''function Activity{show_id if show_id != 1 else ''}DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, uis.Main.BackGround.BackGroundHolder)
  local spine = LuaUtil.FindChild(effect, "{child}", true)
  SkeletonAnimationUtil.SetAnimation(spine, 0, "in", false, 0, function()
    if spine and uis then
      SkeletonAnimationUtil.SetAnimation(spine, 0, "{animation}", true)
    end
  end)
end
'''


def _entrance(paths, loop=False):
    entries = ",\n".join(f'[{i}] = "{PREFIX}{path}.prefab"' for i, path in enumerate(paths, 1))
    return '''local PATH_LOOKUP = {\n''' + entries + '''\n}
local function PlayDailyEntranceAnim(num, endNum)
  local path = PATH_LOOKUP[num]
  local o = ResourceManager.Instantiate(path)
  UIUtil.SetObjectToUI(o, holder)
  local cmps = o:GetComponentsInChildren(typeof(CS.Spine.Unity.SkeletonAnimation))
  for i = 1, cmps.Length do
    local go = cmps[i - 1].gameObject
    SkeletonAnimationUtil.SetAnimation(go, 0, "in", false, 0, function()
      if o and not o:IsNull() then
        SkeletonAnimationUtil.SetAnimation(go, 0, "idle", ''' + str(loop).lower() + ''', 0, function()
          UIMgr:CloseToWindow(WinResConfig.Activity35DungeonWindow.name)
        end)
      end
    end)
  end
  LeanTween.delayedCall(2, function()
    PlayDailyEntranceAnim(num + 1, endNum)
    holder:Dispose()
  end)
end
'''


def _build(root, tmp_path, monkeypatch, children=("eventcovers_0003_2", "untouched"), *, root_level=False):
    bundle_hash = "a" * 32
    bundle = tmp_path / bundle_hash
    bundle.write_bytes(b"fixture bundle")
    assets = tuple(IndexedAsset(
        name=root.prefab_name if i == 1 else f"resource-{i}", container="Assets/fixture",
        bundle_hash=bundle_hash, path_id=i, dependencies=(), version_timestamp=VERSION,
        bundle_path=bundle, bundle_present=True, evidence="fixture",
    ) for i in range(1, 30))
    parts = tuple(UnityScenePart(
        game_object_ref=AssetRef(bundle_hash, i + 10), component_ref=AssetRef(bundle_hash, i),
        game_object_name=name, component_type="SkeletonAnimation",
        hierarchy_path=name if root_level and i == 2 else f"root/{name}",
        transform_chain=(), world_position=(0., 0., 0.), enabled=True, sorting_layer_value=0,
        sorting_order=i, renderer_depth=0., skeleton_ref=AssetRef(bundle_hash, 20),
        skeleton_json_ref=AssetRef(bundle_hash, 21), atlas_refs=(AssetRef(bundle_hash, 22),),
        animation="in", loop=True,
    ) for i, name in enumerate(children, 2))
    parsed = UnitySceneParseResult(bundle_hash, (), (), parts, (), (), version_timestamp=VERSION)
    monkeypatch.setattr(builder, "parse_unity_scene", lambda *_: parsed)
    return builder.build_portrait_scenes(VERSION, [root], AssetIndex(VERSION, assets))[0]


def test_mounted_lua_overrides_only_evidenced_child(tmp_path, monkeypatch):
    root = _root(tmp_path, "0003", 3, _mounted())
    scene = _build(root, tmp_path, monkeypatch)
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [
        ("in", False), ("in", True)]
    assert dict(root.metadata)["runtime_purpose"] == "mounted_background"
    diagnostic = next(d for d in scene.diagnostics if d.code == "event_runtime_animation")
    assert diagnostic.severity == "warning"
    assert "Activity3DungeonWindow.lua:" in dict(diagnostic.details)["evidence"]


def _named_child_list(members="spine, bg", guard="spineTb[i] and uis"):
    return '''function Activity7DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
  local spine = LuaUtil.FindChild(effect, "eventcovers_0007", true)
  local bg = LuaUtil.FindChild(effect, "eventcovers_0007_BG_1", true)
  local spineTb = {''' + members + '''}
  for i = 1, #spineTb do
    SkeletonAnimationUtil.SetAnimation(spineTb[i], 0, "in", false, 0, function()
      if ''' + guard + ''' then
        SkeletonAnimationUtil.SetAnimation(spineTb[i], 0, "idle", true)
      end
    end)
  end
end
'''


def test_named_child_list_applies_runtime_animation_to_exact_children(tmp_path, monkeypatch):
    root = _root(tmp_path, "0007", 7, _named_child_list())
    scene = _build(root, tmp_path, monkeypatch,
                   ("eventcovers_0007", "eventcovers_0007_BG_1", "untouched"))
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [
        ("in", False), ("in", False), ("in", True)]
    notes = [d for d in scene.diagnostics if d.code == "event_runtime_animation"]
    assert len(notes) == 2
    assert all("Activity7DungeonWindow.lua:7" in dict(note.details)["evidence"] for note in notes)


@pytest.mark.parametrize("members,guard", [
    ("spine, unknown", "spineTb[i] and uis"),
    ("spine, bg", "alternate"),
])
def test_uncertain_named_child_list_does_not_override_prefab(tmp_path, monkeypatch, members, guard):
    root = _root(tmp_path, "0007", 7, _named_child_list(members, guard))
    scene = _build(root, tmp_path, monkeypatch, ("eventcovers_0007", "eventcovers_0007_BG_1"))
    assert all(p.spine.default_animation == "in" for p in scene.parts)
    assert any(d.code == "event_runtime_animation_unknown" for d in scene.diagnostics)


@pytest.mark.parametrize("use_list", [False, True])
def test_mounted_object_target_applies_only_to_root_spine(tmp_path, monkeypatch, use_list):
    source = (_named_child_list("effect") if use_list else
              _mounted(7).replace("SetAnimation(spine,", "SetAnimation(effect,")
              .replace("if spine and uis then", "if effect then"))
    root = _root(tmp_path, "0007", 7, source)
    scene = _build(root, tmp_path, monkeypatch, ("mounted-root", "untouched"), root_level=True)
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [
        ("in", False), ("in", True)]


def test_mounted_object_target_does_not_guess_a_descendant(tmp_path, monkeypatch):
    root = _root(tmp_path, "0007", 7, _named_child_list("effect"))
    scene = _build(root, tmp_path, monkeypatch, ("nested-spine",))
    assert scene.parts[0].spine.default_animation == "in"
    assert any(d.code == "event_runtime_target_unmatched" for d in scene.diagnostics)


@pytest.mark.parametrize("wrapper,closing", [
    ("local function unused()", "end"), ("Timer.After(10, function()", "end)"),
])
def test_unproven_helper_or_callback_is_not_a_direct_runtime_animation(tmp_path, wrapper, closing):
    source = '''function Activity3DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
  local spine = LuaUtil.FindChild(effect, "eventcovers_0003_2", true)
  ''' + wrapper + '''
    SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
  ''' + closing + '''
end
'''
    root = _root(tmp_path, "0003", 3, source)
    assert root.event_runtime.overrides == ()


def test_entrance_cover_uses_nonlooping_idle_without_claiming_map_background(tmp_path, monkeypatch):
    root = _root(tmp_path, "0035", 35,
                 'function Activity35DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n',
                 _entrance(["eventcovers_0035"]),
                 'if 35 == data.show_id then\n  AbyssExploreMgr.Enter(EXPLORE_MAP_ID.ACTIVITY_7)\nend')
    scene = _build(root, tmp_path, monkeypatch)
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [("in", False)] * 2
    assert all([(s.animation, s.loop) for s in p.spine.animation_sequence] == [("in", False), ("idle", False)]
               for p in scene.parts)
    assert dict(root.metadata)["runtime_purpose"] == "entrance_only"
    assert any(d.code == "event_runtime_purpose" and "entrance_only" in d.message for d in scene.diagnostics)
    purpose = next(d for d in scene.diagnostics if d.code == "event_runtime_purpose")
    assert dict(purpose.details)["background_renderer"] == "exploration_map_unsupported"


def test_sequence_stage_keeps_its_exact_prefab_when_assets_are_missing(tmp_path):
    root = _root(tmp_path, "0005", 5,
                 'function Activity5DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n',
                 _entrance([f"eventcovers_0005_{n}" for n in (4, 1, 2, 3)]))
    scenes = builder.build_portrait_scenes(VERSION, [root], AssetIndex(VERSION, ()))
    assert len(scenes) == 1
    assert scenes[0].parts == ()
    assert [d.code for d in scenes[0].diagnostics if d.severity == "error"] == [
        "prefab_asset_missing"]
    details = dict(scenes[0].diagnostics[0].details)
    assert details["prefab_name"].endswith("eventcovers_0005_4.prefab")
    assert dict(scenes[0].config_metadata)["stage_index"] == "1"


def test_unknown_runtime_preserves_prefab_and_reports_uncertainty(tmp_path, monkeypatch):
    root = _root(tmp_path, "0003", 3)
    scene = _build(root, tmp_path, monkeypatch)
    assert [p.spine.default_animation for p in scene.parts] == ["in", "in"]
    assert dict(root.metadata)["runtime_purpose"] == "unknown"
    assert any(d.code == "event_runtime_unknown" and d.severity == "warning" for d in scene.diagnostics)


def test_runtime_changes_invalidate_config_hash_even_when_bg_main_unchanged(tmp_path):
    first = _root(tmp_path, "0003", 3, _mounted())
    second = _root(tmp_path, "0003", 3, _mounted(animation="rest"))
    assert first.prefab_name == second.prefab_name
    assert builder._root_config_hash(first, "a") != builder._root_config_hash(second, "a")


def test_runtime_reads_requested_version_and_ignores_commented_calls(tmp_path, monkeypatch):
    folder = tmp_path / "lua" / "401"
    folder.mkdir(parents=True)
    (folder / "Activity3DungeonWindow.lua").write_text(_mounted(animation="wrong"), encoding="utf-8")
    root = _root(tmp_path, "0003", 3, _mounted() + '\n-- SkeletonAnimationUtil.SetAnimation(spine, 0, "wrong", true)')
    assert _build(root, tmp_path, monkeypatch).parts[0].spine.animation_sequence[-1].animation == "idle"


@pytest.mark.parametrize("show_id,child", [(2, "eventcovers_0001_bg"), (4, "eventcovers_0004")])
def test_runtime_uses_config_show_id_and_lua_child_names_not_event_id_table(tmp_path, monkeypatch, show_id, child):
    root = _root(tmp_path, "0003", show_id, _mounted(show_id, animation="rest", child=child))
    scene = _build(root, tmp_path, monkeypatch)
    assert scene.parts[0].spine.default_animation == "in"
    assert any(d.code == "event_runtime_target_unmatched" for d in scene.diagnostics)


def test_conditional_alternative_animations_are_not_a_known_completion_sequence(tmp_path, monkeypatch):
    dungeon = '''function Activity3DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
  local spine = LuaUtil.FindChild(effect, "eventcovers_0003_2", true)
  if alternate then
    SkeletonAnimationUtil.SetAnimation(spine, 0, "idle", true)
  else
    SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
  end
end
'''
    root = _root(tmp_path, "0003", 3, dungeon)
    scene = _build(root, tmp_path, monkeypatch)
    assert scene.parts[0].spine.default_animation == "in"
    assert any(d.code == "event_runtime_animation_unknown" for d in scene.diagnostics)


def test_entrance_animation_is_not_overwritten_by_another_function(tmp_path, monkeypatch):
    entrance = _entrance(["eventcovers_0035"]) + '''
function Activity35EntranceAnimWindow.Other()
  SkeletonAnimationUtil.SetAnimation(go, 0, "wrong", true)
end
'''
    root = _root(tmp_path, "0035", 35,
                 'function Activity35DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', entrance)
    assert _build(root, tmp_path, monkeypatch).parts[0].spine.animation_sequence[-1].animation == "idle"


@pytest.mark.parametrize("event_id,show_id,target", [
    ("0002", 1, "eventcovers_0002"), ("0004", 4, "eventcovers_0004"),
])
def test_audited_event_windows_override_named_child_and_preserve_sibling(
        tmp_path, monkeypatch, event_id, show_id, target):
    root = _root(tmp_path, event_id, show_id, _mounted(show_id, child=target))
    scene = _build(root, tmp_path, monkeypatch, (target, "untouched"))
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [
        ("in", False), ("in", True)]


@pytest.mark.parametrize("statement", [
    '''local help = 'SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)' ''',
    '''local help = "SkeletonAnimationUtil.SetAnimation(spine, 0, 'rage', true)" ''',
    '''local help = [[SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)]]''',
    '''local help = [==[\nSkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)\n]==]''',
])
def test_runtime_ignores_animation_api_tokens_inside_lua_strings(tmp_path, statement):
    source = '''function Activity3DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
  local spine = LuaUtil.FindChild(effect, "eventcovers_0003_2", true)
''' + statement + '\nend\n'
    (tmp_path / "Activity3DungeonWindow.lua").write_text(source, encoding="utf-8")

    runtime = load_event_runtime(tmp_path, "3", PREFIX + "eventcovers_0003.prefab")

    assert runtime.purpose == "mounted_background"
    assert runtime.overrides == ()


@pytest.mark.parametrize("body", [
    '''if alternate then
    SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
  end''',
    '''if configData then
    if alternate then
      SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
    end
  end''',
    '''if configData then
  else
    SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
  end''',
    '''if alternate then
    SkeletonAnimationUtil.SetAnimation(spine, 0, "in", false, 0, function()
      if spine and uis then
        SkeletonAnimationUtil.SetAnimation(spine, 0, "rage", true)
      end
    end)
  end''',
])
def test_unsupported_enclosing_condition_keeps_prefab_animation(tmp_path, monkeypatch, body):
    dungeon = '''function Activity3DungeonWindow.LoadBg()
  effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
  local spine = LuaUtil.FindChild(effect, "eventcovers_0003_2", true)
''' + body + '\nend\n'
    root = _root(tmp_path, "0003", 3, dungeon)
    assert root.event_runtime.overrides == ()
    scene = _build(root, tmp_path, monkeypatch)
    assert scene.parts[0].spine.default_animation == "in"
    assert any(d.code == "event_runtime_animation_unknown" for d in scene.diagnostics)


def test_known_config_guard_and_completed_unrelated_branch_keep_literal_override(tmp_path):
    dungeon = '''function Activity3DungeonWindow.LoadBg()
  if alternate then
    local help = "if false then end"
  end
  if configData then
    effect = UIUtil.SetEffectToUI(configData.bg_main, holder)
    local spine = LuaUtil.FindChild(effect, "eventcovers_0003_2", true)
    SkeletonAnimationUtil.SetAnimation(spine, 0, "idle", true)
  end
end
'''
    root = _root(tmp_path, "0003", 3, dungeon)
    assert [(o.target, o.animation, o.loop) for o in root.event_runtime.overrides] == [
        ("eventcovers_0003_2", "idle", True)]


def _mounted_component_loop():
    return '''function Activity36DungeonWindow.LoadBg()
  UIUtil.SetHolderCenter(uis.Main.BackGround.BackGroundHolder)
  effect = UIUtil.SetEffectToUI(configData.bg_main, uis.Main.BackGround.BackGroundHolder)
  if effect then
    LuaUtil.PlayEffect(effect)
    local cmps = effect:GetComponentsInChildren(typeof(CS.Spine.Unity.SkeletonAnimation))
    for i = 1, cmps.Length do
      local go = cmps[i - 1].gameObject
      SkeletonAnimationUtil.SetAnimation(go, 0, "in", false, 0, function()
        if effect then
          SkeletonAnimationUtil.SetAnimation(go, 0, "idle", true)
        end
      end)
    end
  end
end
'''


@pytest.mark.parametrize("instance", ["effect", "mountedVisual"])
def test_mounted_exact_component_loop_preserves_entrance_and_callback_idle(tmp_path, monkeypatch, instance):
    dungeon = _mounted_component_loop().replace("effect", instance)
    root = _root(tmp_path, "0036", 36, dungeon)

    assert root.event_runtime.purpose == "mounted_background"
    assert [(o.target, o.animation, o.loop) for o in root.event_runtime.overrides] == [
        ("*", "in", False)]
    scene = _build(root, tmp_path, monkeypatch)
    assert [(p.spine.default_animation, p.spine.loop) for p in scene.parts] == [
        ("in", False), ("in", False)]
    assert not any(d.code == "event_runtime_animation_unknown" for d in scene.diagnostics)


@pytest.mark.parametrize("before,after", [
    ("effect:GetComponentsInChildren", "other:GetComponentsInChildren"),
    ("if effect then", "if alternate then"),
    ("local go = cmps[i - 1].gameObject", "local go = other[i - 1].gameObject"),
])
def test_mounted_component_loop_requires_bound_instance_and_audited_guard(tmp_path, before, after):
    root = _root(tmp_path, "0036", 36, _mounted_component_loop().replace(before, after))
    assert root.event_runtime.overrides == ()

# Regressions for playback: dropping the first call loses the entrance and
# changing a callback loop changes the configured tail of the sequence.
def test_mounted_completion_keeps_first_animation_and_each_call_evidence(tmp_path, monkeypatch):
    root = _root(tmp_path, "0003", 3, _mounted())
    override = root.event_runtime.overrides[0]
    assert (override.animation, override.loop) == ("in", False)
    assert [(step.animation, step.loop) for step in override.sequence] == [("in", False), ("idle", True)]
    assert [step.evidence.rsplit(":", 1)[1] for step in override.sequence] == ["4", "6"]
    scene = _build(root, tmp_path, monkeypatch)
    assert scene.parts[0].spine.animation_sequence == override.sequence
    assert scene.parts[1].spine.animation_sequence == ()


def test_sequential_stages_retain_applicable_component_overrides(tmp_path, monkeypatch):
    _root(tmp_path, "0005", 5,
          'function Activity5DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n',
          _entrance(["eventcovers_0005_4", "eventcovers_0005_1"]))
    roots = load_eventcover_roots(tmp_path / "lua", VERSION)
    assert len(roots) == 2
    assert [r.event_runtime.sequence_prefabs for r in roots] == [
        (PREFIX + "eventcovers_0005_4.prefab",), (PREFIX + "eventcovers_0005_1.prefab",)]
    for stage in roots:
        assert [(o.target, o.animation, o.loop) for o in stage.event_runtime.overrides] == [("*", "in", False)]
        scene = _build(stage, tmp_path, monkeypatch)
        assert all([(s.animation, s.loop) for s in p.spine.animation_sequence] == [
            ("in", False), ("idle", False)] for p in scene.parts)


def _fade(instance="o", duration="0.8"):
    return '''
  SkeletonAnimationUtil.SetAlpha(''' + instance + ''', 0)
  local tweenId
  tweenId = LeanTween.value(0, 1, ''' + duration + '''):setOnUpdate(function(val)
    LuaUtil.SetAlpha(''' + instance + ''', val, true)
    color.a = val
    setSpritesColor(''' + instance + ''', color)
    SkeletonAnimationUtil.SetAlpha(''' + instance + ''', val)
  end)
'''


def test_entrance_fade_is_preserved_on_each_stage_and_its_metadata(tmp_path):
    entrance = _entrance(["eventcovers_0005_4", "eventcovers_0005_1"])
    entrance = entrance.replace('  local cmps', _fade() + '  local cmps')
    _root(tmp_path, "0005", 5,
          'function Activity5DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', entrance)
    roots = load_eventcover_roots(tmp_path / "lua", VERSION)
    assert all(r.event_runtime.fade_in_seconds == 0.8 for r in roots)
    assert all(dict(r.metadata)["runtime_fade_in_seconds"] == "0.8" for r in roots)
    assert all(any("Activity5EntranceAnimWindow.lua:" in e for e in r.event_runtime.evidence) for r in roots)


@pytest.mark.parametrize("fade", [
    _fade("other"),
    '  if alternate then\n' + _fade() + '  end\n',
    _fade().replace('SkeletonAnimationUtil.SetAlpha(o, 0)', ''),
    _fade(duration="-1"),
    '--[[' + _fade() + ']]',
])
def test_unevidenced_or_unrelated_fade_is_not_applied(tmp_path, fade):
    entrance = _entrance(["eventcovers_0035"]).replace('  local cmps', fade + '  local cmps')
    root = _root(tmp_path, "0035", 35,
                 'function Activity35DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', entrance)
    assert root.event_runtime.fade_in_seconds == 0.0


def test_sprite_only_cover_keeps_parts_with_inapplicable_spine_diagnostic(tmp_path):
    from app.features.preview.portrait_config.event_runtime import apply_event_runtime
    from app.features.preview.portrait_config.model import RendererState, ScenePart, SpriteState

    root = _root(tmp_path, "0028", 28, _mounted(28).replace('SetAnimation(spine,', 'SetAnimation(effect,')
                 .replace('if spine and uis then', 'if effect then'))
    part = ScenePart("sprite", AssetRef("a" * 32, 1), "cover", "root/cover",
                     renderer=RendererState("SpriteRenderer", True), sprite=SpriteState(AssetRef("a" * 32, 2)))
    parts, diagnostics = apply_event_runtime((part,), root.event_runtime)
    assert parts == (part,)
    assert any(d.code == "event_runtime_spine_not_applicable" for d in diagnostics)
    assert not any(d.code == "event_runtime_target_unmatched" or d.severity == "error" for d in diagnostics)



def _stage_transition_timing():
    fade = _fade().rstrip()
    return fade + ''' :setOnComplete(function()
    RemoveTween(tweenId)
    tweenId = LeanTween.delayedCall(2, function()
      PlayDailyEntranceAnim(num + 1, endNum)
      RemoveTween(tweenId)
      if num < endNum then
        post_blur:SetActive(false)
        tweenId = LeanTween.value(1, 0, 0.5):setOnUpdate(function(val)
          LuaUtil.SetAlpha(o, val, true)
          SkeletonAnimationUtil.SetAlpha(o, val)
        end):setOnComplete(function()
          RemoveTween(tweenId)
          holder:Dispose()
        end).id
      end
    end).id
  end).id
'''


def test_stage_transition_timing_is_metadata_without_invented_animation(tmp_path):
    entrance = _entrance(["eventcovers_0005_4", "eventcovers_0005_1"])
    start = entrance.index('  local cmps')
    entrance = entrance[:start] + _stage_transition_timing() + 'end\n'
    _root(tmp_path, "0005", 5,
          'function Activity5DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', entrance)
    roots = load_eventcover_roots(tmp_path / "lua", VERSION)
    assert len(roots) == 2
    for stage in roots:
        assert stage.event_runtime.overrides == ()
        assert dict(stage.metadata)["runtime_hold_seconds"] == "2.0"
        assert dict(stage.metadata)["runtime_fade_out_seconds"] == "0.5"
        assert stage.event_runtime.fade_in_seconds == 0.8


def test_unrelated_stage_transition_tween_does_not_establish_timing(tmp_path):
    entrance = _entrance(["eventcovers_0035"])
    start = entrance.index('  local cmps')
    timing = _stage_transition_timing().replace('SkeletonAnimationUtil.SetAlpha(o, val)',
                                                'SkeletonAnimationUtil.SetAlpha(other, val)')
    entrance = entrance[:start] + timing + 'end\n'
    root = _root(tmp_path, "0035", 35,
                 'function Activity35DungeonWindow.LoadBg()\n UIUtil.SetHolderCenter(holder)\nend\n', entrance)
    assert dict(root.metadata)["runtime_hold_seconds"] == "0.0"
    assert dict(root.metadata)["runtime_fade_out_seconds"] == "0.0"
