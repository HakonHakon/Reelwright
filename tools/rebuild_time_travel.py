"""Rebuild the existing time-travel episode against its uploaded performance.

Uses local H3 Ref2VA to regenerate both complete characters, not a face paste.
The source soundtrack is retained so dialogue, breaths and pauses stay exact.
Prepare is reversible; renders resume from saved per-shot files.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import comfy, jobs, media, planner, settings, store, timeline, workflows

SID = "hakon_maryam_a_touch_through_time"
EID = "a_touch_through_time_reference_adaptatio"
FPS = 30
# Original edit boundaries measured with FFmpeg at 30 fps. Longer continuous
# shots are partitioned for generation without changing their screen geography.
SEGMENTS = [
    (0, 102, "beach_cafe", "Beach Café", "Maryam descends the cafe steps toward camera; Hakon passes into frame from left. Natural walking, not an empty beach or arranged meeting."),
    (102, 117, "garden", "Indian Palace", "Half-second garden premonition. Same pair suddenly wear Indian historical costumes; startled reaction. Match source contact and pose."),
    (117, 252, "beach_cafe", "Beach Café", "Medium two-shot, Hakon LEFT and Maryam RIGHT. Accidental hand sensation: Maryam widens her eyes and raises her eyebrows, looks between Hakon and their hands. Hakon studies his open palms, puzzled."),
    (252, 417, "beach_cafe", "Beach Café", "Hakon examines his palms and asks if he knows Maryam. Maryam shakes her head slightly, confused. Keep both faces visible and their natural hesitant gestures, not smiling portrait poses."),
    (417, 481, "beach_cafe", "Beach Café", "They cautiously test the space between their fingers. Maryam brushes hair aside with her free hand, nervously amused; Hakon watches their hands."),
    (481, 493, "old_city", "Winter Palace", "Four-tenths-second snowy palace-square flash: heavy historical fur-trimmed winter clothes, both startled, same hand gesture and matching camera angle."),
    (493, 627, "beach_cafe", "Beach Café", "Immediate return in beach costumes. Maryam touches her hair near the temple, incredulous; Hakon raises his hands, cautiously fascinated. They glance at each other and their hands."),
    (627, 635, "european_market", "European Market", "Very brief historical European flower-market flash, timber buildings, pale dress and dark waistcoat. Preserve source pose and astonished expression."),
    (635, 643, "european_market", "European Market", "Second angle of the same short European-market flash. No spoken line and no extended stay here."),
    (643, 804, "beach_cafe", "Beach Café", "Back in white beach clothes. Hakon smiles in amused disbelief, palms open; Maryam cautiously brings her fingers to his. Show hands and glances downward, not constant eye contact."),
    (804, 974, "beach_cafe", "Beach Café", "Their palms meet and stay joined on the beach while Maryam hesitantly says 'Should we, like...'. Keep their connected hands visible, puzzled anticipation. Do not force an immediate departure on first touch."),
    (974, 1204, "bazaar", "Ancient Marketplace", "Hard cut into sunlit ancient eastern blue-tiled market. Hakon LEFT wears brown turban, embroidered robe and satchel; Maryam RIGHT wears a gold-beige veiled gown. They inspect their changed clothes in delighted disbelief. Maryam bends and lifts her skirt fabric, then looks at Hakon."),
    (1204, 1405, "bazaar", "Ancient Marketplace", "Same market two-shot and same costumes. Maryam asks about letting go, gesturing with her free hand toward their joined hands. Hakon looks down, uncertain amused smile, answers. Preserve the source hand contact rather than inventing extra transitions."),
    (1405, 1411, "beach_cafe", "Beach Café", "Rapid beach return, original white costumes. Preserve source pose exactly; no new line."),
    (1411, 1415, "bazaar", "Ancient Marketplace", "Rapid historical-market insert in the first bazaar costumes. Match source position and hand motion."),
    (1415, 1422, "beach_cafe", "Beach Café", "Second rapid beach insert, same original white clothes. Keep this flash short."),
    (1422, 1595, "bazaar", "Ancient Royal", "Market returns with a SECOND costume set as in source: Maryam LEFT in red-and-gold royal gown, headpiece and ivory veil; Hakon RIGHT in beige tunic, blue-grey robe and pale turban. He asks whether to stay; she looks around delighted, then asks the year. No invented bridge or extra beach return."),
    (1595, 1841, "bazaar", "Ancient Royal", "Same reverse two-shot and second historical costumes. Maryam examines Hakon's sleeve and unfamiliar outfit, amused disbelief. Hakon looks down at clothes and hands, invites a return. They prepare to repeat the hand experiment."),
    (1841, 2034, "beach_cafe", "Beach Café", "Immediate beach return; Maryam LEFT, Hakon RIGHT in original white costumes. He raises open hands, startled. She turns to check surroundings then says how strange it is; he shares her surprise. Preserve uncertain expressions before small smiles."),
    (2034, 2135, "beach_cafe", "Beach Café", "Wider cafe two-shot. Hakon gestures toward Maryam, asks about staying in the other place. Maryam smiles and says 'Let's explore.' They reach toward each other. Original cafe frontage and warm light remain readable."),
    (2135, 2325, "garden", "Indian Palace", "Hard cut to lush Indian palace celebration: Maryam LEFT in turquoise-green skirt, blue-red blouse and bright pink veil; Hakon RIGHT in golden-brown coat and tan turban. Tiger rests at left; monkey on upper branch, elephant in background, vivid traditionally dressed extras and fabric dyers. Both survey the surroundings in delighted amazement. No garden dialogue."),
    (2325, 2492, "garden", "Indian Palace", "They walk together under the trees. Maryam holds Hakon's arm, smiles up at him, he smiles and looks ahead; natural changing gaze. Moving side/rear camera follows them past bright dancers and foliage. No spoken lines and no newly invented monkey joke."),
    (2492, 2613, "end_card", "", "Creator attribution over the darkened GENERATED garden ending; no source-actor footage in the end card."),
]

# Corrected against source subtitles as well as local ASR. Original audio is the
# final authority, including hesitations and overlapping low-level utterances.
DIALOGUE = [
    (5.33, 6.27, "maryam", "What was that?", "startled, questioning"),
    (6.27, 7.95, "maryam", "Did you feel that?", "wide-eyed puzzled curiosity"),
    (7.95, 8.37, "hakon", "Yes.", "quietly surprised"),
    (9.30, 10.23, "hakon", "I don't know what that was.", "puzzled, studying palms"),
    (11.00, 11.85, "hakon", "Do I know you?", "uncertain, looking at Maryam"),
    (12.50, 13.35, "maryam", "I don't think so.", "confused, small head shake"),
    (17.90, 18.75, "maryam", "What was that?", "incredulous after flash"),
    (29.30, 30.25, "maryam", "Should we, like...", "hesitant anticipation"),
    (36.05, 37.15, "maryam", "What are you wearing?", "astonished at changed clothes"),
    (38.10, 38.65, "hakon", "And you?", "amused disbelief"),
    (40.13, 44.59, "maryam", "Wait. Do you think we let go, like... we go back to reality?", "curious realization; gestures at hands"),
    (44.95, 46.55, "hakon", "Yeah. That's how it works, I think.", "tentative, not certain"),
    (47.81, 50.10, "hakon", "Should we stay here, or you wanna go back?", "playfully curious"),
    (50.10, 51.21, "maryam", "I like it here.", "delighted"),
    (51.31, 52.09, "maryam", "What year is this?", "looks around, curious"),
    (52.29, 53.17, "hakon", "I have no clue.", "honest amused uncertainty"),
    (53.17, 56.11, "maryam", "But are you wearing, like, a completely different thing?", "inspects his unfamiliar costume"),
    (57.33, 59.01, "hakon", "Let's, let's try to go back to reality.", "gentle experiment, slight hesitation"),
    (59.05, 59.70, "hakon", "Ready?", "inviting, checks her reaction"),
    (59.70, 60.10, "maryam", "Okay.", "agreeing softly"),
    (63.41, 64.49, "maryam", "That's so strange.", "astonished"),
    (65.43, 66.59, "maryam", "I've never felt this.", "still processing the experience"),
    (66.59, 67.39, "hakon", "Me too.", "shared surprise"),
    (67.79, 70.10, "hakon", "Should we stay in the other one and see what happens?", "curious and inviting"),
    (70.10, 70.80, "maryam", "Let's explore.", "small excited smile"),
]


class LogJob:
    cancel_flag = False
    def say(self, text):
        print(f"{datetime.datetime.now():%H:%M:%S} {text}", flush=True)
    def check(self):
        pass


def validate_plan():
    assert SEGMENTS[0][0] == 0 and SEGMENTS[-1][1] == 2613
    assert all(a[1] == b[0] for a, b in zip(SEGMENTS, SEGMENTS[1:]))
    assert all(0 < (end-start)/FPS <= 15 for start, end, *_ in SEGMENTS)
    assert all(0 <= start < end < 71.1667 for start, end, *_ in DIALOGUE)
    assert [(a, b) for a, b, loc, *_ in SEGMENTS if loc == "old_city"] == [(481, 493)]


def prepare(source):
    validate_plan()
    epdir = Path(store.ep_dir(SID, EID))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = epdir / "revisions" / ("before-source-faithful-rebuild-" + stamp)
    backup.mkdir(parents=True)
    shutil.copy2(epdir / "episode.json", backup / "episode.json")
    shutil.copytree(Path(store.bible_dir(SID)), backup / "bible")
    if (epdir / "PROJECT_RULES.md").exists():
        shutil.copy2(epdir / "PROJECT_RULES.md", backup / "PROJECT_RULES.md")
    reference = epdir / "reference" / "source-performance.mp4"
    reference.parent.mkdir(exist_ok=True)
    shutil.copy2(source, reference)
    b = store.bible(SID)
    style = b["style"]
    style.update(default_engine="minimax_local", default_frame_source="qwen_2511",
                 look="Photorealistic cinematic live-action; source video controls framing, expressions, gesture and gaze; saved character portraits control full identity, hair and body appearance",
                 negative="duplicate lead identities, blonde Maryam, curly-haired Hakon, identity drift, deformed hands, extra fingers, modern clothes in historical scenes, static portrait acting",
                 music_policy="Preserve the uploaded source soundtrack and all original dialogue at exact original times. No rewritten lines or invented garden dialogue.")
    store.save_style(SID, style)
    if not store.get_location(SID, "european_market"):
        store.save_location(SID, {"id":"european_market", "name":"European Market Flash", "description":"Old European cobblestone street, timber-framed houses and bright flower stalls; period background people, daylight", "variants":{"day":"natural daylight"}, "plates":{}, "version":1})
    garden = store.get_location(SID, "garden")
    garden.update(description="Lush historical Indian palace garden celebration with huge trees and golden sunlight. A resting tiger at left, monkey on an upper branch, elephant deep in the background; fabric dyers and guests in bright turbans and orange/red traditional clothes. Source geography and extras are allowed; no modern vehicles.")
    store.save_location(SID, garden)
    shots, scenes = [], []
    for i, (start, end, loc, outfit, action) in enumerate(SEGMENTS):
        shot_id = f"REF_{i+1:02d}"
        dialogue = [{"speaker":cid, "line":line, "emotion":emotion, "source_start_s":a, "source_end_s":z}
                    for a,z,cid,line,emotion in DIALOGUE if start/FPS <= (a+z)/2 < end/FPS]
        cast = [] if loc == "end_card" else [{"id":cid,"outfit":outfit,"position":("left" if cid == ("hakon" if start < 1422 else "maryam") else "right")} for cid in ("hakon","maryam")]
        sh = planner.new_shot(id=shot_id, scene_id=shot_id, order=i, location_id=loc if cast else "",
             time_of_day="day", framing="wide" if i in (0,19,20,21) else "medium two-shot",
             camera="Match original reference camera angle, crop and movement. Preserve both visible faces and original shot axis; no compulsory OTS or fabricated reverse coverage.",
             characters=cast, action=action, dialogue=dialogue,
             sound="Exact original source soundtrack, dialogue, breaths, pauses, music and ambience.",
             action_seconds=0, duration_s=(end-start)/FPS, duration_locked=True,
             engine="minimax_local", frame_source="qwen_2511", first_frame=None,
             motion_prompt_extra="Follow the source performance: preserve changing gaze toward partner, hands, clothing and surroundings. Keep hand contact and release as actually shown, without enforcing an invented travel state machine.",
             notes="Source-matched full-character regeneration. Original footage is a motion reference only; final story picture must be generated. This shot is unapproved until visual review.")
        sh["reference"] = {"file":store.rel(str(reference)), "start_frame":start,"end_frame":end,
                           "start_s":start/FPS,"end_s":end/FPS,"fps":FPS}
        shots.append(sh)
        scenes.append({"id":shot_id,"location_id":sh["location_id"],"time_of_day":"day","summary":action,"characters":cast,"beats":[action],"dialogue":dialogue})
    e = store.get_episode(SID,EID)
    story = "# A TOUCH THROUGH TIME — source-faithful character regeneration\n\n87.1 seconds at 30 fps. Use the CURRENT saved Hakon and Maryam characters, not the original actors. Full head, hair, exposed skin and body identity are regenerated. Source performance controls expressions, scene order, camera, hand gestures and reaction timing. Original audio is preserved exactly.\n\n"
    story += "\n\n".join(f"## {a/FPS:.3f}–{z/FPS:.3f}s | {loc}\n{act}" for a,z,loc,_,act in SEGMENTS)
    story += "\n\n## Original dialogue (source audio remains authoritative)\n" + "\n".join(f"{a:.2f}–{z:.2f} {cid.upper()}: {line}" for a,z,cid,line,_ in DIALOGUE)
    e.update(story=story, scenes=scenes, shots=shots, dialogue_takes={}, audio_assets=[], outputs={},
             image={"width":1280,"height":704},video={"width":640,"height":352,"fps":FPS},
             gates={k:False for k in e["gates"]},music={"file":"","volume":1,"duck":False},
             production_rules={"reference_is_authoritative":True,"identity":"current approved character bible and reference images; regenerate full appearance", "dialogue":"original audio, exact words and timing; no invented lines", "framing":"match source two-shots; both faces allowed", "gaze":"source-directed partner, hands, costume or scenery", "hand_contact":"source performance; do not invent state-machine transitions", "wardrobe":"scene-specific outfit, including Ancient Marketplace, Ancient Royal and Indian Palace", "background_people":"source extras permitted; never duplicate lead identities", "render_method":"local generative H3 Ref2VA with two character references; no face-paste substitution"},
             reference_rebuild={"status":"prepared","source":store.rel(str(reference)),"backup":store.rel(str(backup)),"shots_done":0,"shots_total":len(shots),"method":"full-character H3 reference-conditioned regeneration"})
    e["timeline"] = {"schema_version":1,"revision":e.get("timeline",{}).get("revision",0)+1,"fps":FPS,"clips":[],"music":None,"markers":[],"range":None,"titles":[],"audio":[],"overlays":[]}
    store.save_episode(SID,e)
    rules = "# Source-faithful rebuild rules\n\nThe uploaded source-performance.mp4 is authoritative for scene order, expressions, framing, timing, hand contact and dialogue. Earlier adaptation/contact/OTS rules are superseded and backed up in revisions.\n\nUse the CURRENT saved Hakon and Maryam full appearances and scene-specific outfits. Never change their identities to the original actors. Regenerate story footage; never pass through source actors or substitute a face paste. Preserve source soundtrack. No rewritten romantic dialogue, no forced one-visible-face coverage, no invented empty-beach encounter, no enforced eye-contact-only rule and no invented immediate-travel-on-touch rule. Background extras and animals present in the source are allowed.\n\nKeep all brief flashes, second bazaar costume set, beach returns and silent garden ending. Generated frames and takes remain unapproved until visual review.\n"
    (epdir/"PROJECT_RULES.md").write_text(rules)
    (reference.parent/"source-faithful-plan.md").write_text(story)
    (reference.parent/"dialogue.json").write_text(json.dumps(DIALOGUE,ensure_ascii=False,indent=2))
    print("PREPARED",epdir,flush=True)


def render(ids=None):
    import requests
    response=requests.post(f"http://127.0.0.1:8020/api/s/{SID}/e/{EID}/reference_render",json={"shot_ids":ids or []},timeout=30)
    response.raise_for_status()
    print(json.dumps(response.json(),indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare",type=Path)
    parser.add_argument("--render",action="store_true")
    parser.add_argument("--shots",nargs="*")
    args=parser.parse_args()
    validate_plan()
    if args.prepare:prepare(args.prepare)
    if args.render:
        render(args.shots)
