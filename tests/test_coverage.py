"""Coverage groups share scene time; the coverage cut alternates angles while dialogue runs underneath."""
from studio import coverage, lipsync_engine, planner


def ep(shots, lines):
    return {"shots": shots, "dialogue_takes": {str(k): {"text": t, "file": f"d{k}.wav", "duration": d}
                                               for k, (t, d) in enumerate(lines)}}


def single(sid, cid, cover, dialogue, order):
    return planner.new_shot(id=sid, scene_id="S01", cover=cover, order=order, dialogue=dialogue, action_seconds=0.8,
                            characters=[{"id": cid, "position": "center"}])


DLG = [{"speaker": "m", "line": "We're closed.", "pause": 1.0},
       {"speaker": "h", "line": "I know. Five minutes? It's warm in here.", "pause": 0.6}]
LINES = [("We're closed.", 1.0), ("I know. Five minutes? It's warm in here.", 3.6)]


def test_layout_gives_a_group_one_span_and_cutaways_no_time():
    shots = [single("A_m", "m", "g1", DLG, 0), single("A_h", "h", "g1", DLG, 1),
             planner.new_shot(id="INS", scene_id="S01", cutaway="insert", duration_s=3, order=2),
             single("B_m", "m", "g2", DLG[:1], 3)]
    rows = {r["id"]: r for r in lipsync_engine.layout(ep(shots, LINES), 10.5)}
    assert rows["A_m"]["start"] == rows["A_h"]["start"] == 0
    assert rows["A_m"]["len"] == rows["A_h"]["len"] == round(1.0 + 1.0 + 0.6 + 3.6 + 0.8, 3)
    assert rows["B_m"]["start"] == rows["A_m"]["len"]          # the insert took no scene time
    assert rows["A_h"]["lines"][1][2] == 2.6                  # line offsets identical in both singles


def test_plan_alternates_with_j_and_l_cuts_and_punch_in():
    g = {"len": 7.0, "singles": {"m": {}, "h": {}},
         "lines": [("m", 1.0, 1.0, "We're closed."), ("h", 2.6, 3.6, "I know. Five minutes? It's warm in here.")]}
    p = coverage.plan_pieces(g, [])
    keys = [(k, rf["zoom"]) for _, _, _, k, rf in p]
    assert keys[0] == ("m", 1) and ("h", 1) in keys and ("h", 1.32) in keys   # second angle on the long line
    assert keys[-1][0] == "m"                                                   # long line ends on the listener
    assert all(b - a >= coverage.CUT_MIN - 1e-9 for a, b, *_ in p[:-1])
    assert p[0][0] == 0 and abs(p[-1][1] - 7.0) < 1e-9
    assert all(abs(p[i][1] - p[i + 1][0]) < 1e-9 for i in range(len(p) - 1))   # no gaps


def test_cutaway_fills_the_pause_before_its_line():
    g = {"len": 8.0, "singles": {"m": {}, "h": {}},
         "lines": [("h", 0.5, 1.0, "What do you want?"), ("m", 4.0, 1.0, "Bills.")]}
    p = coverage.plan_pieces(g, [{"id": "INS", "cutaway": "insert", "cue_line": "Bills."}])
    ins = [x for x in p if x[2] == "cutaway"]
    assert ins and ins[0][0] < 4.0 <= ins[0][1] + 0.2


def test_to_coverage_splits_lines_and_sets_eyelines():
    shots = [planner.new_shot(id="S01_SH01", scene_id="S01", order=0, dialogue=DLG,
                              characters=[{"id": "m", "position": "left"}, {"id": "h", "position": "right"}])]
    bible = {"characters": {"m": {"id": "m", "name": "Maryam"}, "h": {"id": "h", "name": "Hakon"}}}
    out = coverage.to_coverage(ep(shots, LINES), bible, "S01", 10.5)
    assert [s["id"] for s in out] == ["S01_CV01_m", "S01_CV01_h"]
    assert out[0]["cover"] == out[1]["cover"] and out[0]["dialogue"] == out[1]["dialogue"]
    assert "frame right" in out[0]["action"] and "frame left" in out[1]["action"]


def test_insert_during_a_line_shows_the_speaker_first_and_ends_on_a_face():
    g = {"len": 6.8, "singles": {"m": {}, "h": {}},
         "lines": [("h", 1.2, 1.1, "What do you want for it?"),
                   ("m", 3.1, 2.9, "My father never sent anyone back into the rain.")]}
    p = coverage.plan_pieces(g, [{"id": "PHOTO", "cutaway": "insert", "cue_line": "My father never sent anyone back into the rain."}])
    keys = [k for *_, k, _ in p]
    assert keys == ["h", "m", "PHOTO", "h"]          # speaker before the insert, listener's face at the end
    assert p[-1][1] - p[-1][0] >= coverage.CUT_MIN


def test_last_word_lands_on_the_listener():
    g = {"len": 6.7, "singles": {"m": {}, "h": {}},
         "lines": [("h", 1.3, 0.9, "Hakon."), ("m", 2.8, 1.3, "Come back when you're dry, Hakon.")]}
    assert [k for *_, k, _ in coverage.plan_pieces(g, [])][-1] == "h"


def test_coverage_prompt_does_not_quote_lines_and_square_is_opt_in():
    b = {"characters": {"m": {"id": "m", "name": "Maryam"}}}
    shot = {"cover": "g1", "characters": [{"id": "m"}], "dialogue": [{"speaker": "m", "line": "Bills."}]}
    text = lipsync_engine.prompt(shot, b)
    assert "Bills." not in text and "subtitle" not in text.lower()   # quoted words and the word itself both paint text
    s = {"video": {"width": 576, "height": 1024}, "ltx_lipsync": {"pixels": 983040}}
    w, h = lipsync_engine.size(s)
    assert w == h                                   # portrait renders square by default
    s["ltx_lipsync"]["portrait_square"] = False
    assert lipsync_engine.size(s)[1] > lipsync_engine.size(s)[0]


def test_dialogue_blocking_two_shot_then_ots_per_turn_sharing_frames():
    b = {"characters": {"m": {"id": "m", "name": "Maryam", "appearance": "slim", "outfits": {"default": "apron"}},
                        "h": {"id": "h", "name": "Hakon", "appearance": "glasses", "outfits": {"street": "jacket"}}},
         "locations": {"bakery": {"id": "bakery", "name": "Bakery", "description": "warm"}}}
    lines = [{"speaker": "m", "line": "We're closed."}, {"speaker": "h", "line": "I know."},
             {"speaker": "h", "line": "Five minutes?"}, {"speaker": "m", "line": "Fine."}]
    old = planner.new_shot(id="S03_SH01", scene_id="S03", location_id="bakery", dialogue=lines,
                           characters=[{"id": "m", "position": "left", "outfit": "default"},
                                       {"id": "h", "position": "right", "outfit": "street"}])
    out = planner.dialogue_blocking({"shots": [old]}, b, "S03")
    assert [s["id"] for s in out] == ["S03_TS01", "S03_OT01", "S03_OT02", "S03_OT03"]
    assert out[0]["dialogue"] == [] and out[0]["framing"] == "two-shot"
    assert [len(s["dialogue"]) for s in out[1:]] == [1, 2, 1]          # Hakon's two lines are one turn
    assert out[1]["frame_from"] == "" and out[3]["frame_from"] == "S03_OT01"   # Maryam's OTS shots share a frame
    assert all(s["engine"] == "infinitetalk" for s in out[1:]) and out[0]["engine"] == "ltx_lipsync"
    assert "RIGHT foreground" in out[1]["image_prompt_override"]       # over Hakon's (right) shoulder onto Maryam
    assert "LEFT foreground" in out[2]["image_prompt_override"]
    from studio import prompts
    assert "We're closed" not in prompts.infinitetalk_prompt(out[1], b)   # the audio drives the mouth; no words in prompts


def test_infinitetalk_workflow_chains_chunks_at_project_shape():
    from studio import settings, workflows
    s = settings.load(); s = {**s, "video": {"width": 576, "height": 1024, "fps": 24}}
    w, h = workflows.infinitetalk_size(s)
    assert h > w and w % 16 == 0 and h % 16 == 0
    wf = workflows.infinitetalk(s, "f.png", "v.wav", "P", 170, 5, "studio/x")
    nodes = [k for k, v in wf.items() if v["class_type"] == "WanInfiniteTalkToVideo"]
    assert len(nodes) == 3                                            # 81 + 72 + 72 frames cover 170
    assert "previous_frames" not in wf["it0"]["inputs"] and wf["it2"]["inputs"]["previous_frames"] == ["cat1", 0]
    assert wf["vid"]["inputs"]["images"] == ["cat2", 0] and wf["it1"]["inputs"]["mode"] == "single_speaker"
