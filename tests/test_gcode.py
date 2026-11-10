import os
from pathlib import Path

from pi_screen.gcode import GcodeInfoCache, current_layer, read_gcode_info

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "s3d_sample.gcode"


def test_total_counts_layer_comments():
    assert read_gcode_info(SAMPLE).total_layers == 3


def test_build_time_parsed(tmp_path):
    assert read_gcode_info(SAMPLE).build_seconds == 3900

    minutes_only = tmp_path / "short.gcode"
    minutes_only.write_text("; layer 1, Z = 0.2\nG1 X1\n;   Build Time: 42 minutes\n")
    assert read_gcode_info(minutes_only).build_seconds == 2520


def test_current_layer_before_first_layer_is_zero():
    assert current_layer(read_gcode_info(SAMPLE), 0) == 0


def test_current_layer_at_and_after_offsets():
    info = read_gcode_info(SAMPLE)
    assert current_layer(info, info.offsets[1]) == 2
    assert current_layer(info, 10**9) == 3


def test_file_without_comments(tmp_path):
    plain = tmp_path / "plain.gcode"
    plain.write_text("G28\nG1 X10 Y10\nG1 X20 Y20\n")
    info = read_gcode_info(plain)
    assert info.total_layers == 0
    assert info.build_seconds is None
    assert current_layer(info, 500) == 0


def test_cache_returns_none_for_missing_file(tmp_path):
    assert GcodeInfoCache().get(tmp_path / "missing.gcode") is None


def test_cache_rebuilds_after_mtime_change(tmp_path):
    path = tmp_path / "p.gcode"
    path.write_text("; layer 1, Z = 0.2\n")
    cache = GcodeInfoCache()
    assert cache.get(path).total_layers == 1

    path.write_text("; layer 1, Z = 0.2\n; layer 2, Z = 0.4\n")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert cache.get(path).total_layers == 2
