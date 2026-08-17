from app.core.preview_catalog import build_skel_map, find_skel_paths


def test_find_skel_paths_handles_animation_and_composite_names(tmp_path):
    material = tmp_path / "material"
    material.mkdir()
    skel = material / "hero.skel"
    skel.write_bytes(b"skel")

    skel_map = build_skel_map(str(material))

    assert find_skel_paths("hero_idle.png", skel_map) == (
        str(skel), str(material / "hero.atlas")
    )
    assert find_skel_paths("hero_composite.png", skel_map) == (
        str(skel), str(material / "hero.atlas")
    )
    assert find_skel_paths("unknown.png", skel_map) == (None, None)
