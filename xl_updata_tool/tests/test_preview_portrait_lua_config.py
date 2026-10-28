from app.features.preview.portrait_config.lua_config import load_portrait_roots


def test_loads_exact_10118_skin_roots_and_positions_by_version(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseFashion.lua").write_text(
        '''
[12002181] = { id = 12002181, card_id = 10000218,
  show_spine = "cardspine_10118_1.prefab", show_spine_position = {394, -816} },
[12002182] = { id = 12002182, card_id = 10000218,
  show_spine = "cardspine_10118_2.prefab", show_spine_position = {328, -466} },
[12002183] = { id = 12002183, card_id = 10000218,
  show_spine = "cardspine_10118_3.prefab", show_spine_position = {502, -477} },
''', encoding="utf-8")
    roots = load_portrait_roots(tmp_path / "lua", 402)
    assert [(r.skin_id, r.prefab_name, r.position) for r in roots] == [
        ("12002181", "cardspine_10118_1.prefab", (394, -816)),
        ("12002182", "cardspine_10118_2.prefab", (328, -466)),
        ("12002183", "cardspine_10118_3.prefab", (502, -477)),
    ]
    assert all(r.evidence for r in roots)
    assert all(r.version_timestamp == 402 for r in roots)
    assert dict(roots[0].field_evidence)["show_spine"].endswith(":3")
    assert dict(roots[0].field_evidence)["show_spine_position"].endswith(":3")


def test_missing_show_spine_is_explicit_not_guessed(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseFashion.lua").write_text(
        '[12002181] = { id = 12002181, card_id = 10000218 },', encoding="utf-8"
    )
    roots = load_portrait_roots(tmp_path / "lua", 402)
    assert len(roots) == 1
    assert roots[0].prefab_name is None
    assert roots[0].unresolved_reason == "missing_show_spine"


def test_ignores_commented_out_spine_and_position_fields(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseFashion.lua").write_text(
        '''[12002181] = {
  id = 12002181,
  -- show_spine = "stale.prefab",
  -- show_spine_position = {1, 2}
},''',
        encoding="utf-8",
    )
    roots = load_portrait_roots(tmp_path / "lua", 402)
    assert roots[0].prefab_name is None
    assert roots[0].position is None
    assert roots[0].unresolved_reason == "missing_show_spine"


def test_reads_only_selected_lua_version(tmp_path):
    for version, prefab in ((401, "old.prefab"), (402, "new.prefab")):
        lua = tmp_path / "lua" / str(version)
        lua.mkdir(parents=True)
        (lua / "BaseFashion.lua").write_text(
            f'[12002181] = {{ id = 12002181, card_id = 10000218, show_spine = "{prefab}" }},',
            encoding="utf-8",
        )
    roots = load_portrait_roots(tmp_path / "lua", 402)
    assert [root.prefab_name for root in roots] == ["new.prefab"]


def test_balances_nested_lua_tables_and_ignores_braces_in_strings(tmp_path):
    lua = tmp_path / "lua" / "402"
    lua.mkdir(parents=True)
    (lua / "BaseFashion.lua").write_text(
        '''[12002181] = {
  id = 12002181,
  card_id = 10000218,
  nested = { note = "brace } in string" },
  show_spine = "cardspine_10118_1.prefab",
  show_spine_position = {394, -816}
},
[12002182] = { id = 12002182, card_id = 10000218 },''',
        encoding="utf-8",
    )
    roots = load_portrait_roots(tmp_path / "lua", 402)
    assert [root.skin_id for root in roots] == ["12002181", "12002182"]
    assert roots[0].position == (394.0, -816.0)
