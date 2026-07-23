"""Pure, Flask-importable read/validate/write of anipose ``config.toml``.

Only the numeric/toggle parameters of ``[triangulation]``, ``[filter]`` (2D)
and ``[filter3d]`` are exposed. List params (constraints/scheme/axes/…),
comments and formatting are preserved on write via *targeted line edits* —
the Flask container has the ``toml`` reader but no round-tripping writer
(tomlkit/tomli_w), so we never re-serialise the whole file.

Imports: stdlib + a TOML parser only (``toml`` in the Flask/worker container,
``tomli`` as a host-test fallback). No Flask, no anipose, no pandas.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# ── field schema (the ONLY keys this module ever touches) ───────────────────

# Per section, ordered list of the exposed fields.
_SCHEMA = {
    "triangulation": [
        "cam_regex", "ransac", "optim", "optim_chunking",
        "scale_smooth", "scale_length", "scale_length_weak",
        "reproj_error_threshold", "score_threshold",
        "n_deriv_smooth", "optim_chunking_size",
        "constraints", "constraints_weak",
    ],
    "filter": [
        "enabled", "spline", "multiprocessing", "type",
        "medfilt", "offset_threshold", "score_threshold", "n_back",
    ],
    "filter3d": [
        "enabled", "medfilt", "offset_threshold",
    ],
}

# Field kinds for typing (read) and validation.
_BOOL_FIELDS = {
    "triangulation": {"ransac", "optim", "optim_chunking"},
    "filter": {"enabled", "spline", "multiprocessing"},
    "filter3d": {"enabled"},
}
_STR_FIELDS = {
    "triangulation": {"cam_regex"},
    "filter": {"type"},
    "filter3d": set(),
}
# List-of-pairs fields (the anipose skeleton constraints): [[a, b], ...].
_LIST_FIELDS = {
    "triangulation": {"constraints", "constraints_weak"},
    "filter": set(),
    "filter3d": set(),
}
# medfilt: odd int in [1, 199]
_MEDFILT_FIELDS = {
    "triangulation": set(),
    "filter": {"medfilt"},
    "filter3d": {"medfilt"},
}
# everything else in the schema is a non-negative number.


def _num_fields(section: str) -> set:
    return (set(_SCHEMA[section])
            - _BOOL_FIELDS[section]
            - _STR_FIELDS[section]
            - _MEDFILT_FIELDS[section]
            - _LIST_FIELDS[section])


# ── TOML reader (resilient: toml in container, tomli on host) ────────────────

def _parse_toml(config_path) -> dict:
    try:
        import toml
        return toml.load(str(config_path))
    except ImportError:
        import tomli
        with open(config_path, "rb") as fh:
            return tomli.load(fh)


# ── read ────────────────────────────────────────────────────────────────────

def read_params(config_path) -> dict:
    """Return ``{"triangulation":{...}, "filter":{...}, "filter3d":{...}}``
    containing ONLY the exposed fields that are present in ``config.toml``,
    with values typed (bool/str/int/float)."""
    doc = _parse_toml(config_path)
    out = {"triangulation": {}, "filter": {}, "filter3d": {}}
    for section, fields in _SCHEMA.items():
        sect = doc.get(section)
        if not isinstance(sect, dict):
            continue
        for key in fields:
            if key not in sect:
                continue
            val = sect[key]
            if key in _BOOL_FIELDS[section]:
                out[section][key] = bool(val)
            elif key in _STR_FIELDS[section]:
                out[section][key] = str(val)
            elif key in _LIST_FIELDS[section]:
                # normalise to a list of 2-string pairs; drop malformed entries
                out[section][key] = [
                    [str(p[0]), str(p[1])] for p in val
                    if isinstance(p, (list, tuple)) and len(p) == 2
                ] if isinstance(val, list) else []
            else:
                out[section][key] = val  # native int/float from the parser
    return out


# ── TOML value encoding (targeted writes) ───────────────────────────────────

def _encode_toml_value(value) -> str:
    # bool must precede int (bool is an int subclass)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # list/tuple → TOML array (recursive; used for constraints = [["a","b"], …])
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_encode_toml_value(v) for v in value) + "]"
    # string → double-quoted TOML basic string
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


# ── raw-line section/key scanning ───────────────────────────────────────────

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _section_bounds(lines, section):
    """Return (header_idx, end_idx) for ``[section]``; end_idx is the index of
    the next section header (or len(lines)). header_idx is None if absent."""
    header_idx = None
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m and m.group(1).strip() == section:
            header_idx = i
            break
    if header_idx is None:
        return None, None
    end_idx = len(lines)
    for j in range(header_idx + 1, len(lines)):
        if _SECTION_RE.match(lines[j]):
            end_idx = j
            break
    return header_idx, end_idx


def _find_key_line(lines, key, lo, hi):
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=")
    for i in range(lo, hi):
        if key_re.match(lines[i]):
            return i
    return None


def _value_span_end(lines, key_idx):
    """Index just past a value that may open a multi-line array — e.g.
    ``constraints = [`` … ``]`` spread over lines. Balances [] / {} from
    ``key_idx``; a single-line value returns ``key_idx + 1``. (Bodypart names
    carry no brackets, so plain bracket counting is safe here.)"""
    depth = 0
    started = False
    i = key_idx
    while i < len(lines):
        for ch in lines[i]:
            if ch in "[{":
                depth += 1
                started = True
            elif ch in "]}":
                depth -= 1
        i += 1
        if started and depth <= 0:
            break
    return i if started else key_idx + 1


def write_params(config_path, params) -> None:
    """Targeted, formatting-preserving write of ``params`` into ``config.toml``.

    For each present ``(section, key, value)``: replace the ``key = <value>``
    line within the matching ``[section]`` block (preserving every other byte),
    or insert it just after the section header if the key is missing. Raises
    ``ValueError`` if a requested section is absent. Atomic (temp + os.replace).
    """
    config_path = Path(config_path)
    text = config_path.read_text()
    lines = text.splitlines(keepends=True)

    for section, kv in (params or {}).items():
        if not isinstance(kv, dict) or not kv:
            continue
        for key, value in kv.items():
            header_idx, end_idx = _section_bounds(lines, section)
            if header_idx is None:
                raise ValueError(f"section [{section}] not found in {config_path}")
            encoded = _encode_toml_value(value)
            key_idx = _find_key_line(lines, key, header_idx + 1, end_idx)
            if key_idx is not None:
                indent = re.match(r"^(\s*)", lines[key_idx]).group(1)
                # List values may span multiple source lines — collapse the whole
                # value span to one line so no dangling array rows are left behind.
                span_end = (_value_span_end(lines, key_idx)
                            if isinstance(value, (list, tuple)) else key_idx + 1)
                lines[key_idx:span_end] = [f"{indent}{key} = {encoded}\n"]
            else:
                lines.insert(header_idx + 1, f"{key} = {encoded}\n")

    out_text = "".join(lines)
    fd, tmp = tempfile.mkstemp(dir=str(config_path.parent),
                               prefix=".config.", suffix=".toml.tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(out_text)
        os.replace(tmp, str(config_path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── validation ──────────────────────────────────────────────────────────────

def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_params(params) -> list:
    """Return a list of human-readable error strings (empty when clean)."""
    errors = []
    for section, kv in (params or {}).items():
        if section not in _SCHEMA:
            errors.append(f"unknown section: {section}")
            continue
        if not isinstance(kv, dict):
            errors.append(f"[{section}] must be an object")
            continue
        for key, value in kv.items():
            if key not in _SCHEMA[section]:
                errors.append(f"[{section}] unknown field: {key}")
                continue
            if key in _BOOL_FIELDS[section]:
                if not isinstance(value, bool):
                    errors.append(f"[{section}] {key} must be a boolean")
            elif key in _MEDFILT_FIELDS[section]:
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value % 2 == 0 or value < 1 or value > 199):
                    errors.append(
                        f"[{section}] {key} must be an odd int in 1..199")
            elif section == "filter" and key == "type":
                if value not in ("medfilt", "viterbi"):
                    errors.append(
                        "[filter] type must be one of medfilt, viterbi")
            elif key in _STR_FIELDS[section]:
                if not isinstance(value, str):
                    errors.append(f"[{section}] {key} must be a string")
            elif key in _LIST_FIELDS[section]:
                if not isinstance(value, list):
                    errors.append(f"[{section}] {key} must be a list of [a, b] pairs")
                elif any(
                    not isinstance(p, (list, tuple)) or len(p) != 2
                    or not all(isinstance(x, str) and x.strip() for x in p)
                    for p in value
                ):
                    errors.append(
                        f"[{section}] {key}: each entry must be a pair of two "
                        f"non-empty bodypart names")
            else:  # numeric >= 0
                if not _is_number(value):
                    errors.append(f"[{section}] {key} must be a number")
                elif value < 0:
                    errors.append(f"[{section}] {key} must be >= 0")
    return errors
