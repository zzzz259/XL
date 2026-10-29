from app.features.preview.portrait_config import interaction_metadata
from app.features.preview.portrait_config.model import InteractionEvent

OVERLAY = """local OnClick = function(skeletonAnimation, animationName, gesture)
local go = skeletonAnimation.gameObject
local track = 1
local current = skeletonAnimation.state:GetCurrent(track)
if nil == current or current.IsComplete then
SkeletonAnimationUtil.SetAnimation(go, track, animationName, false)
end
end
return {OnClick = OnClick}"""


def test_verified_overlay_preserves_target_and_independent_track():
    event = InteractionEvent("CLICK", animation="touch", callback="touch_cardspine_10016_3")
    enriched, notes = interaction_metadata.parse_callback(event, OVERLAY, "same-version.lua:1")
    assert enriched.track == 1
    assert [(s.animation, s.loop) for s in enriched.sequence] == [("touch", False)]
    assert enriched.gate == "nil == current or current.IsComplete"
    assert not notes
    unknown, notes = interaction_metadata.parse_callback(event, OVERLAY.replace("track = 1", "track = 2"), "lua:1")
    assert not unknown.sequence and notes[0].code == "unsupported_interaction_callback"


def test_seven_evidenced_callback_sources_keep_action_idle_and_unknown_diagnostic():
    from pathlib import Path
    import json

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/portrait_click_callbacks_v1.json").read_text(encoding="utf-8")
    )
    assert len(fixture) == 7
    for name, text in fixture.items():
        event = InteractionEvent(
            "CLICK", animation="in" if name.endswith("10102_3") else "touch_feedback_1", callback=name
        )
        enriched, notes = interaction_metadata.parse_callback(event, text, name + ".lua:1")
        assert not notes and enriched.evidence
        assert enriched.sequence[0].animation == event.animation
        if name.endswith("10016_3"):
            assert enriched.track == 1 and len(enriched.sequence) == 1
        else:
            assert enriched.track == 0
            assert [(step.animation, step.loop) for step in enriched.sequence] == [
                (event.animation, False),
                ("idle", True),
            ]
        changed, notes = interaction_metadata.parse_callback(event, text + "\nUnknownHelper()\n", name + ".lua:1")
        assert not changed.sequence and notes[0].severity == "error"
