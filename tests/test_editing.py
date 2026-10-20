import json

import pytest

from reel_mcp import media
from reel_mcp.analysis import pick_frame_times
from reel_mcp.editing import analyse_editing


def _analyse(path):
    return analyse_editing(media.decode_gray(path, 30), 30)


def _transitions(rep):
    return [(t["type"], t["at_s"]) for t in rep["transitions"]]


def test_hard_cuts(clips):
    rep = _analyse(clips["cuts"])
    assert _transitions(rep) == [("hard_cut", 1.5), ("hard_cut", 3.0), ("hard_cut", 4.5)]
    assert rep["pace"]["shot_count"] == 4
    json.dumps(rep)  # plain Python types only


def test_dissolve(clips):
    (kind, at), = _transitions(_analyse(clips["dissolve"]))
    assert kind == "dissolve" and at == pytest.approx(1.7, abs=0.15)


def test_fades(clips):
    assert [k for k, _ in _transitions(_analyse(clips["fades"]))] == ["fade_from_black", "fade_to_black"]


def test_flash_frame(clips):
    assert _transitions(_analyse(clips["flash"])) == [("flash_white", 1.5)]


def test_pan_is_camera_motion_not_a_cut(clips):
    rep = _analyse(clips["pan"])
    assert rep["transitions"] == [] and rep["in_shot_changes"] == []
    assert rep["shots"][0]["camera_move"] == "pan right"


def test_title_pop_in_is_an_in_shot_change_not_a_cut(clips):
    rep = _analyse(clips["popin"])
    assert rep["transitions"] == [] and rep["pace"]["shot_count"] == 1
    assert [c["at_s"] for c in rep["in_shot_changes"]] == [1.5]
    assert rep["shots"][0]["in_shot_changes"] == 1


def test_whip_pan_into_a_new_picture_is_a_transition(clips):
    (kind, at), = _transitions(_analyse(clips["whip"]))
    assert kind in ("whip_or_wipe", "motion_cut") and 1.35 <= at <= 1.65


def test_exposure_change_is_not_a_dissolve(clips):
    assert _analyse(clips["exposure"])["transitions"] == []


def test_static(clips):
    rep = _analyse(clips["static"])
    assert rep["transitions"] == [] and rep["in_shot_changes"] == [] and rep["shots"][0]["motion"] == "static"
    assert rep["pace"]["pace"] == "single continuous shot"


def test_frame_selection():
    shots = [{"index": 1, "start_s": 0, "end_s": 6, "duration_s": 6},
             {"index": 2, "start_s": 6, "end_s": 7, "duration_s": 1}]
    picks = pick_frame_times(shots, 7, 4)
    assert len(picks) == 4 and [s for _, s in picks].count(1) == 3  # spare frames go to the long shot
    many = [{"index": i, "start_s": i, "end_s": i + 1, "duration_s": 1} for i in range(1, 21)]
    chosen = [s for _, s in pick_frame_times(many, 21, 5)]
    assert len(chosen) == 5 and chosen[0] == 1 and chosen[-1] == 20 and chosen == sorted(set(chosen))
