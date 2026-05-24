import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dlc import project_settings as ps


def test_set_get_roundtrip(tmp_path):
    assert ps.get_setting(tmp_path, "clip_window") is None
    assert ps.get_setting(tmp_path, "clip_window", "DEF") == "DEF"
    ps.set_setting(tmp_path, "clip_window", '{"before":200,"after":599}')
    assert ps.get_setting(tmp_path, "clip_window") == '{"before":200,"after":599}'
    ps.set_setting(tmp_path, "clip_window", '{"before":10,"after":20}')
    assert ps.get_setting(tmp_path, "clip_window") == '{"before":10,"after":20}'
    ps.set_setting(tmp_path, "finalize_window", '{"before":1,"after":2}')
    assert ps.get_setting(tmp_path, "clip_window") == '{"before":10,"after":20}'
    assert ps.get_setting(tmp_path, "finalize_window") == '{"before":1,"after":2}'


def test_db_file_created_under_project(tmp_path):
    ps.set_setting(tmp_path, "k", "v")
    assert (tmp_path / "ui_settings.sqlite").is_file()
