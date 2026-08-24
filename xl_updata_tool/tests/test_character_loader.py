from app.features.characters.parser import extract_all_card_blocks, parse_t_args


def test_extract_all_card_blocks_handles_nested_tables():
    content = "return {[80100001] = {name = 'A', nested = {x = 1}}, [80100002] = {name = 'B'}}"

    blocks = extract_all_card_blocks(content)

    assert [raw_id for raw_id, _ in blocks] == [80100001, 80100002]
    assert "nested = {x = 1}" in blocks[0][1]


def test_parse_t_args_keeps_nested_calls_together():
    assert parse_t_args("80880001, T(1, 2), 'text'") == [
        "80880001",
        "T(1, 2)",
        "'text'",
    ]
