"""Unit tests for dlc.anipose_config — read/validate/targeted-write of
anipose config.toml. Pure module; no Flask, no worker imports."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dlc import anipose_config as ac  # noqa: E402


# A config with a leading comment, a [labeling] scheme list (must be preserved),
# and the three editable sections with an inline comment + assorted types.
_CONFIG_TEXT = """\
# anipose project config — do not hand-edit lists below
model_type = "deeplabcut"

[labeling]
scheme = [["Wrist", "MCP-1"], ["MCP-1", "PIP-1"]]
constraints = [[0, 1], [1, 2]]

[triangulation]
cam_regex = "cam([0-9])"
ransac = false
optim = true
scale_smooth = 2.0   # inline comment stays
scale_length = 3
reproj_error_threshold = 15
n_deriv_smooth = 2

[filter]
enabled = false
type = "medfilt"
medfilt = 7
offset_threshold = 25
score_threshold = 0.3
n_back = 3
spline = true

[filter3d]
enabled = true
medfilt = 5
offset_threshold = 15
"""


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(_CONFIG_TEXT)
    return p


# ── read_params ─────────────────────────────────────────────────────────────

class TestReadParams:
    def test_returns_three_sections_with_typed_values(self, config_file):
        params = ac.read_params(config_file)
        assert set(params) == {"triangulation", "filter", "filter3d"}

        tri = params["triangulation"]
        assert tri["cam_regex"] == "cam([0-9])"
        assert isinstance(tri["cam_regex"], str)
        assert tri["ransac"] is False and tri["optim"] is True
        assert isinstance(tri["ransac"], bool)
        assert tri["scale_smooth"] == 2.0 and isinstance(tri["scale_smooth"], float)
        assert tri["scale_length"] == 3 and isinstance(tri["scale_length"], int)
        assert tri["reproj_error_threshold"] == 15
        assert tri["n_deriv_smooth"] == 2

        flt = params["filter"]
        assert flt["enabled"] is False and isinstance(flt["enabled"], bool)
        assert flt["type"] == "medfilt"
        assert flt["medfilt"] == 7
        assert flt["score_threshold"] == 0.3
        assert flt["spline"] is True

        f3d = params["filter3d"]
        assert f3d["enabled"] is True
        assert f3d["medfilt"] == 5
        assert f3d["offset_threshold"] == 15

    def test_omits_absent_fields_and_list_params(self, config_file):
        params = ac.read_params(config_file)
        # scale_length_weak / optim_chunking not in the file → omitted
        assert "scale_length_weak" not in params["triangulation"]
        assert "optim_chunking" not in params["triangulation"]
        # list params never surface
        assert "scheme" not in params["triangulation"]
        assert "constraints" not in params["triangulation"]


# ── write_params (targeted, formatting-preserving) ──────────────────────────

class TestWriteParams:
    def test_roundtrips_values_and_leaves_other_lines_byte_identical(self, config_file):
        original_lines = config_file.read_text().splitlines(keepends=True)

        # change exactly one value in each section
        ac.write_params(config_file, {
            "triangulation": {"scale_length": 9},
            "filter": {"medfilt": 11},
            "filter3d": {"offset_threshold": 42},
        })

        new_lines = config_file.read_text().splitlines(keepends=True)
        assert len(new_lines) == len(original_lines)

        changed = {i for i, (a, b) in enumerate(zip(original_lines, new_lines))
                   if a != b}
        # exactly three lines changed — every other byte preserved
        assert len(changed) == 3
        for i in range(len(original_lines)):
            if i not in changed:
                assert new_lines[i] == original_lines[i], f"line {i} mutated"

        # the changed values re-read correctly and untouched ones are intact
        params = ac.read_params(config_file)
        assert params["triangulation"]["scale_length"] == 9
        assert params["filter"]["medfilt"] == 11
        assert params["filter3d"]["offset_threshold"] == 42
        assert params["triangulation"]["scale_smooth"] == 2.0
        assert params["filter"]["type"] == "medfilt"

    def test_preserves_scheme_and_comment_lines(self, config_file):
        text_before = config_file.read_text()
        ac.write_params(config_file, {"filter": {"enabled": True}})
        text_after = config_file.read_text()
        assert '# anipose project config' in text_after
        assert 'scheme = [["Wrist", "MCP-1"], ["MCP-1", "PIP-1"]]' in text_after
        assert 'scale_smooth = 2.0   # inline comment stays' in text_after
        # only the enabled flag flipped
        assert text_before.replace("enabled = false", "enabled = true", 1) == text_after

    def test_bool_and_string_encoding(self, config_file):
        ac.write_params(config_file, {
            "triangulation": {"ransac": True, "cam_regex": "cam[0-9]+"},
        })
        params = ac.read_params(config_file)
        assert params["triangulation"]["ransac"] is True
        assert params["triangulation"]["cam_regex"] == "cam[0-9]+"

    def test_inserts_missing_key_under_existing_section(self, config_file):
        # scale_length_weak is absent from [triangulation]
        ac.write_params(config_file, {"triangulation": {"scale_length_weak": 4}})
        params = ac.read_params(config_file)
        assert params["triangulation"]["scale_length_weak"] == 4
        # existing keys untouched
        assert params["triangulation"]["scale_length"] == 3

    def test_raises_when_target_section_absent(self, tmp_path):
        p = tmp_path / "config.toml"
        p.write_text("[triangulation]\nransac = false\n")
        with pytest.raises(ValueError):
            ac.write_params(p, {"filter": {"enabled": True}})


# ── validate_params ─────────────────────────────────────────────────────────

class TestValidateParams:
    def test_clean_params_return_empty(self):
        assert ac.validate_params({
            "triangulation": {"ransac": True, "scale_smooth": 2.0, "n_deriv_smooth": 3},
            "filter": {"enabled": False, "type": "viterbi", "medfilt": 7},
            "filter3d": {"medfilt": 5, "offset_threshold": 0},
        }) == []

    def test_rejects_even_medfilt(self):
        errs = ac.validate_params({"filter": {"medfilt": 8}})
        assert errs and any("medfilt" in e for e in errs)
        assert ac.validate_params({"filter3d": {"medfilt": 200}})  # out of range
        assert ac.validate_params({"filter": {"medfilt": 0}})      # < 1

    def test_rejects_negative_and_non_numeric(self):
        assert ac.validate_params({"triangulation": {"scale_smooth": -1}})
        assert ac.validate_params({"filter": {"offset_threshold": "x"}})
        assert ac.validate_params({"triangulation": {"n_deriv_smooth": -2}})

    def test_rejects_bad_bool_and_type(self):
        assert ac.validate_params({"filter": {"enabled": "yes"}})
        assert ac.validate_params({"triangulation": {"ransac": 1}})
        assert ac.validate_params({"filter": {"type": "kalman"}})
