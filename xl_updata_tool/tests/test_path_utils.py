from app.core.path_utils import get_base_dir, get_data_dir, get_output_dir


def test_runtime_directories_are_under_project_root():
    base = get_base_dir()

    assert get_data_dir().startswith(base)
    assert get_output_dir().startswith(base)
