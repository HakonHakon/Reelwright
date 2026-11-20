"""Verify the time-travel rebuild after its background renderer completes."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import media, settings, store
from rebuild_time_travel import SID, EID, FPS, SEGMENTS


def verify():
    cfg=settings.load();e=store.get_episode(SID,EID)
    out=Path(store.ep_dir(SID,EID))/"out"/"source_faithful_regeneration"
    report={"checked_at":datetime.datetime.now().astimezone().isoformat(),"checks":{},"visual_review":"Source/generated comparison sheets supplied; manual approval remains pending."}
    def check(name, value):
        report["checks"][name]=bool(value)
    check("render_finished",e["reference_rebuild"].get("status")=="rendered_pending_visual_review")
    if not report["checks"]["render_finished"]:
        report["render_state"]=e["reference_rebuild"]
        (out/"followup-check.json").write_text(json.dumps(report,indent=2))
        raise RuntimeError("Renderer did not finish: "+json.dumps(e["reference_rebuild"]))
    source=store.abs_media(e["reference_rebuild"]["source"])
    final=store.abs_media(e["outputs"]["final"])
    info=media.probe(cfg["ffprobe"],final)
    check("duration_87_1_seconds",abs(info["video_duration"]-87.1)<1/FPS)
    check("source_fps_preserved",info["fps"]==FPS)
    check("source_picture_dimensions_preserved",(info["width"],info["height"])==(640,352))
    check("all_23_shots_present",len(e["shots"])==23 and all(sh["takes"]["items"] for sh in e["shots"]))
    check("source_edit_boundaries_preserved",all((sh["reference"]["start_frame"],sh["reference"]["end_frame"])==(a,z) for sh,(a,z,*_) in zip(e["shots"],SEGMENTS)))
    def audio_hash(path):
        return media._run([cfg["ffmpeg"],"-v","error","-i",path,"-map","0:a:0","-c:a","copy","-f","hash","-hash","sha256","-"]).strip()
    report["source_audio_sha256"]=audio_hash(source)
    report["final_audio_sha256"]=audio_hash(final)
    check("original_dialogue_and_soundtrack_exact",report["source_audio_sha256"]==report["final_audio_sha256"])
    rows=[]
    for sh in e["shots"]:
        take=sh["takes"]["items"][0];path=store.abs_media(take["file"])
        raw=media._run([cfg["ffprobe"],"-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames","-of","json",path])
        frames=int(json.loads(raw)["streams"][0]["nb_read_frames"])
        expected=sh["reference"]["end_frame"]-sh["reference"]["start_frame"]
        rows.append({"shot_id":sh["id"],"frames":frames,"expected":expected})
        check(sh["id"]+"_frames",frames==expected)
    report["shots"]=rows
    report["comparison_sheets"]=[]
    for page in range(2):
        sheet=Image.new("RGB",(1280,6*202),(22,22,22));draw=ImageDraw.Draw(sheet)
        for cell,sh in enumerate(e["shots"][page*12:(page+1)*12]):
            x=(cell%2)*640;y=(cell//2)*202
            draw.text((x+4,y+3),f"{sh['id']} SOURCE | REGENERATED   {sh['reference']['start_s']:.3f}s",fill="white")
            for col,(path,seconds) in enumerate([(source,(sh["reference"]["start_s"]+sh["reference"]["end_s"])/2),(store.abs_media(sh["takes"]["items"][0]["file"]),sh["duration_s"]/2)]):
                thumb=out/f"qa_{sh['id']}_{col}.jpg"
                media._run([cfg["ffmpeg"],"-y","-v","error","-ss",str(seconds),"-i",path,"-frames:v","1","-vf","scale=320:176",str(thumb)])
                with Image.open(thumb) as image:sheet.paste(image,(x+col*320,y+24))
        target=out/f"source_generated_comparison_{page+1}.jpg";sheet.save(target,quality=93)
        report["comparison_sheets"].append(store.rel(str(target)))
    report["technical_checks_passed"]=all(report["checks"].values())
    (out/"followup-check.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    if not report["technical_checks_passed"]:raise RuntimeError("Some technical checks failed")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--watch",action="store_true");args=parser.parse_args()
    if args.watch:
        deadline=time.monotonic()+3*3600
        while time.monotonic()<deadline:
            state=subprocess.run(["systemctl","--user","is-active","reelwright-time-travel-rebuild.service"],capture_output=True,text=True).stdout.strip()
            if state not in ("active","activating","reloading"):break
            time.sleep(15)
        else:raise TimeoutError("Render still running after three hours")
    verify()
