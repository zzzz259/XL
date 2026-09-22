from server_app.state import StateStore


def test_state_store_round_trips_sqlite_state(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    state = store.load()
    state.last_check = None
    state.last_version_timestamp = 123
    store.save(state)
    assert store.load().last_version_timestamp == 123
