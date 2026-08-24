from app.features.preview.catalog import build_skel_map, find_skel_paths, scan_cardspine_roles, scan_preview_roles


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


def test_scan_roles_filters_background_and_returns_sorted_names(tmp_path):
    cardspine = tmp_path / "cardspine"
    cardspine.mkdir()
    (cardspine / "zeta.skel").write_bytes(b"")
    (cardspine / "zeta_bg.skel").write_bytes(b"")
    (cardspine / "alpha.skel").write_bytes(b"")
    preview = tmp_path / "preview"
    (preview / "role_b").mkdir(parents=True)
    (preview / "role_a").mkdir()

    assert scan_cardspine_roles(str(cardspine)) == ["alpha", "zeta"]
    assert scan_preview_roles(str(preview)) == ["role_a", "role_b"]
