"""Studio-queued reference-conditioned full-character video regeneration."""
import datetime
import math
import json
import time
import requests
from PIL import Image, ImageDraw
from pathlib import Path

from . import comfy, jobs, media, settings, store, timeline, workflows

FPS = 30


def recover_frame(job, sid, eid, shot_id, prompt_id):
    """Import an in-flight frame result after a Studio worker restart."""
    cfg=settings.load();client=comfy.Comfy(cfg["comfy_host"])
    job.say(f"Recovering the in-progress first-frame render for {shot_id}...")
    deadline=time.monotonic()+3600
    while time.monotonic()<deadline:
        job.check()
        response=requests.get(f"{client.base}/history/{prompt_id}",timeout=30)
        response.raise_for_status()
        result=response.json().get(prompt_id)
        if result:
            status=result.get("status",{})
            if status.get("status_str")=="error":
                job.say("The interrupted frame render did not finish; existing saved candidates are retained.")
                return
            if status.get("completed"):
                prompt=result.get("prompt",[])
                graph=prompt[2] if len(prompt)>2 else {}
                expected=f"studio/{eid}/{shot_id}"
                if not any(n.get("class_type")=="SaveImage" and n.get("inputs",{}).get("filename_prefix")==expected for n in graph.values()):
                    raise ValueError("Recovered prompt does not belong to the requested episode shot")
                images=[item for output in result.get("outputs",{}).values() for item in output.get("images",[]) if item.get("type")=="output"]
                folder=Path(store.ep_dir(sid,eid))/"frames"/shot_id
                paths=jobs._save_outputs(client,images,str(folder),"recovered_")
                def save(ep):
                    frame=store.find_shot(ep,shot_id)["frame"]
                    frame["candidates"].extend({"file":store.rel(p),"source":"qwen_2511","check":None,"recovered_prompt":prompt_id} for p in paths)
                store.update_episode(sid,eid,save)
                job.say(f"Recovered {len(paths)} first-frame candidates for {shot_id}; resuming the film.")
                return
        time.sleep(2)
    raise RuntimeError("Timed out recovering the interrupted frame render")


def render_prompt(sh, b):
    cast = sh["characters"]
    definitions = []
    for i,cid in enumerate(("hakon","maryam"),1):
        ch=b["characters"][cid]
        outfit=next(x["outfit"] for x in cast if x["id"]==cid)
        definitions.append(f"<Subject {i}> is {ch['name']} in <Picture {i}>. Complete character appearance: {ch['appearance']} Scene costume: {ch['outfits'][outfit]}")
    dialogue=" ".join(f"{d['speaker'].capitalize()} says exactly: {d['line']} Acting: {d['emotion']}." for d in sh["dialogue"])
    return ("subject_definitions:\n"+"\n".join(definitions)+"\n<Video 1> supplies the scene and acting reference only.\n\n"
      "summary:\n[video editing + full character replacement] Regenerate BOTH lead characters from the two pictures. Hakon replaces the original curly-haired man ONLY; Maryam replaces the original blonde woman ONLY. Replace full head, HAIR, neck, face, exposed skin and body appearance, not just faces. Hakon keeps his closely shaved head, mature stubble and tattoos; Maryam keeps long wavy DARK BROWN hair and olive skin. Keep the VIDEO'S historical or beach costumes, never portrait streetwear.\n\n"
      "retention_analysis:\n<Picture 1>: fully_preserved - Hakon complete identity and physique. <Picture 2>: fully_preserved - Maryam complete identity, hair and physique. <Video 1>: partially_preserved - retain camera, scene, costume, actor roles, exact expression changes, mouth articulation, gaze, poses, hand contact, foreground and background. Never transfer character identity to extras.\n\n"
      f"detailed_description:\n{sh['action']} {dialogue} "
      "Match all small reactions, blinks, hesitations, eyebrow lifts and smiles in the source. No constant beauty smiles. Keep source two-shot framing with both faces when shown, no added OTS cuts. Preserve hands and timing. No additional speaking or scene. Photorealistic continuous natural acting.\n\n"
      "overall_soundscape:\nOriginal source audio will be restored separately at its exact original timing.\nnon_diegetic_music:\nNo additional music.")


def render(job, sid, eid, ids=None):
    cfg=settings.load(); e=store.get_episode(sid,eid); b=store.bible(sid)
    epdir=Path(store.ep_dir(sid,eid)); out=epdir/"out"/"source_faithful_regeneration";out.mkdir(parents=True,exist_ok=True)
    source=store.abs_media(e["reference_rebuild"]["source"])
    client=comfy.Comfy(cfg["comfy_host"])
    job.check()
    jobs._gpu_prepare(job,cfg,client);client.free()
    job.progress=[0,len(e["shots"])]
    # Two views with visible facial detail; current bible is never replaced by a
    # portrait from a different series or an older version of these characters.
    refs=[store.abs_media(b["characters"][cid]["refs"][1]) for cid in ("hakon","maryam")]
    images=[client.upload(p,f'time_travel_{cid}_{Path(p).name}') for cid,p in zip(("hakon","maryam"),refs)]
    def mark_running(ep):
        ep["reference_rebuild"].update(status="rendering")
        ep["reference_rebuild"].pop("error", None)
    store.update_episode(sid,eid,mark_running)
    for sh in e["shots"]:
        job.check()
        if ids and sh["id"] not in ids:continue
        r=sh["reference"]; n=r["end_frame"]-r["start_frame"]; duration=n/FPS
        dest=out/(sh["id"]+".mp4")
        try:
            existing = dest.exists() and media.probe(cfg["ffprobe"],str(dest))["video_duration"]>=duration-.04
        except media.MediaError:
            existing = False  # Interrupted encodes are regenerated, never treated as completed shots.
        if existing:
            job.say(sh["id"]+": resume existing generated take")
        job.say(f"{sh['id']}: regenerate {r['start_s']:.3f}–{r['end_s']:.3f}s, {sh['location_id']}")
        if existing:
            elapsed=0
        elif not sh["characters"]:
            last=out/"generated_garden_end.jpg"
            media._run([cfg["ffmpeg"],"-y","-v","error","-sseof","-0.10","-i",str(out/"REF_22.mp4"),"-frames:v","1",str(last)])
            vf="fps=30,scale=640:352,setsar=1,fade=t=out:st=0:d=2,drawtext=text='Reference @JULIVAVILOVA':fontsize=22:fontcolor=white:x=(w-tw)/2:y=(h-th)/2"
            media._run([cfg["ffmpeg"],"-y","-v","error","-loop","1","-i",str(last),"-an","-vf",vf,"-frames:v",str(n),"-c:v","libx264","-crf","16","-pix_fmt","yuv420p",str(dest)])
            elapsed=0
        else:
            frames=workflows.minimax_h3_frames(max(2,duration))
            prepared=out/(sh["id"]+"_motion_reference.mp4")
            media._run([cfg["ffmpeg"],"-y","-v","error","-ss",str(r["start_s"]),"-i",source,"-t",str(duration),"-an","-vf","fps=24,scale=640:352,tpad=stop_mode=clone:stop_duration=3","-frames:v",str(frames),"-c:v","libx264","-crf","16",str(prepared)])
            # For subsecond flashes, pad after trimming so conditioning never
            # leaks the following scene into this short generated insert.
            prepared_pad=out/(sh["id"]+"_motion_pad.mp4")
            media._run([cfg["ffmpeg"],"-y","-v","error","-i",str(prepared),"-vf","tpad=stop_mode=clone:stop_duration=3","-frames:v",str(frames),"-c:v","libx264","-crf","16",str(prepared_pad)])
            prompt=render_prompt(sh,b);(out/(sh["id"]+"_prompt.txt")).write_text(prompt)
            wf=workflows.minimax_h3_reference_edit(cfg,prompt,images,client.upload(str(prepared_pad),sh["id"]+"_motion.mp4"),frames,910110+sh["order"],640,352,"studio/time_travel_source/"+sh["id"])
            (out/(sh["id"]+"_workflow.json")).write_text(json.dumps(wf,indent=2))
            result=client.run(wf,cancel=lambda:job.cancel_flag);job.check();elapsed=result["seconds"]
            if not result["videos"]:raise RuntimeError(sh["id"]+": no generated video")
            raw=out/(sh["id"]+"_generated_raw.mp4");client.download(result["videos"][0],str(raw))
            media._run([cfg["ffmpeg"],"-y","-v","error","-i",str(raw),"-an","-vf","fps=30,scale=640:352,setsar=1,tpad=stop_mode=clone:stop_duration=1","-frames:v",str(n),"-c:v","libx264","-crf","16","-pix_fmt","yuv420p",str(dest)])
        frame=out/(sh["id"]+".jpg")
        media._run([cfg["ffmpeg"],"-y","-v","error","-i",str(dest),"-frames:v","1",str(frame)])
        item={"file":store.rel(str(dest)),"engine":"minimax_local","model":"H3 Ref2VA full-character regeneration","frames":n,"seconds":duration,"render_s":round(elapsed,1),"created":store.now(),"flags":["Visual review required"],"reference":r,"character_refs":[store.rel(p) for p in refs] if sh["characters"] else []}
        def save(ep):
            target=store.find_shot(ep,sh["id"])
            candidates=target["frame"]["candidates"]
            frame_index=next((i for i,c in enumerate(candidates) if c["file"]==store.rel(str(frame))),None)
            if frame_index is None:
                frame_index=len(candidates)
                candidates.append({"file":store.rel(str(frame)),"source":"reference_conditioned_generation","check":None})
            target["frame"].update(selected=frame_index,locked=False)
            items=target["takes"]["items"]
            take_index=next((i for i,t in enumerate(items) if t["file"]==item["file"]),None)
            if take_index is None:
                take_index=len(items)
                items.append(item)
            target["takes"].update(selected=take_index,locked=False,trim_in=0,trim_out=0)
            ep["reference_rebuild"]["shots_done"]=sum(bool(x["takes"]["items"]) for x in ep["shots"])
        updated=store.update_episode(sid,eid,save)
        job.progress=[updated["reference_rebuild"]["shots_done"],len(e["shots"])]
        job.say(f"{sh['id']}: saved, {elapsed:.1f}s render time; visual review required")
    e=store.get_episode(sid,eid)
    if not all(sh["takes"]["items"] for sh in e["shots"]):return
    job.check()
    job.say("Assembling the final film and original soundtrack...")
    concat=out/"concat.txt"
    concat.write_text("".join("file '"+sh["id"]+".mp4'\n" for sh in e["shots"]))
    silent=out/"regenerated_picture.mp4"
    media._run([cfg["ffmpeg"],"-y","-v","error","-f","concat","-safe","0","-i",str(concat),"-an","-c:v","copy",str(silent)])
    final=out/"A_Touch_Through_Time_Hakon_Maryam.mp4"
    media._run([cfg["ffmpeg"],"-y","-v","error","-i",str(silent),"-i",source,"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","copy","-t","87.1","-movflags","+faststart",str(final)])
    info=media.probe(cfg["ffprobe"],str(final))
    if abs(info["video_duration"]-87.1)>1/FPS:raise RuntimeError("Final duration mismatch")
    report={"output":info,"source_frames":2613,"generated_story_frames":2492,"generated_creator_credit_frames":121,"method":"full-character generative H3 Ref2VA; original soundtrack remuxed without revoicing; attribution over generated garden frame","visual_review":"required"}
    (out/"verification.json").write_text(json.dumps(report,indent=2))
    def finish(ep):
        ep["outputs"]={"final":store.rel(str(final)),"runtime":87.1,"reference_report":store.rel(str(out/"verification.json"))}
        ep["timeline"]=timeline.default_edit(ep,cfg,selected_takes=True)
        for clip in ep["timeline"]["clips"]:clip["muted"]=True
        if store.rel(source) not in ep["audio_assets"]:ep["audio_assets"].append(store.rel(source))
        ep["timeline"]["audio"]=[{"id":"original_soundtrack","file":store.rel(source),"source_in":0,"start_frame":0,"frames":round(85.25*FPS),"volume":1,"muted":False,"lane":0,"label":"Exact reference dialogue and soundtrack"}]
        ep["reference_rebuild"].update(status="rendered_pending_visual_review",completed=store.now())
    store.update_episode(sid,eid,finish)
    job.say("RENDER COMPLETE; visual approval pending: "+str(final))


def verify(job, sid, eid):
    cfg=settings.load();e=store.get_episode(sid,eid)
    out=Path(store.ep_dir(sid,eid))/"out"/"source_faithful_regeneration"
    report={"checked_at":datetime.datetime.now().astimezone().isoformat(),"checks":{},"visual_review":"Source/generated comparison sheets supplied; manual approval remains pending."}
    def check(name, value):
        report["checks"][name]=bool(value)
    check("render_finished",e["reference_rebuild"].get("status")=="rendered_pending_visual_review")
    if not report["checks"]["render_finished"]:
        report["render_state"]=e["reference_rebuild"]
        (out/"followup-check.json").write_text(json.dumps(report,indent=2))
        raise RuntimeError("Renderer did not finish: "+json.dumps(e["reference_rebuild"]))
    job.check()
    source=store.abs_media(e["reference_rebuild"]["source"])
    final=store.abs_media(e["outputs"]["final"])
    info=media.probe(cfg["ffprobe"],final)
    check("duration_87_1_seconds",abs(info["video_duration"]-87.1)<1/FPS)
    check("source_fps_preserved",info["fps"]==FPS)
    check("source_picture_dimensions_preserved",(info["width"],info["height"])==(640,352))
    check("all_23_shots_present",len(e["shots"])==23 and all(sh["takes"]["items"] for sh in e["shots"]))
    check("source_edit_boundaries_preserved",all(sh["takes"]["items"][0].get("reference")==sh["reference"] for sh in e["shots"]))
    def audio_hash(path):
        return media._run([cfg["ffmpeg"],"-v","error","-i",path,"-map","0:a:0","-c:a","copy","-f","hash","-hash","sha256","-"]).strip()
    report["source_audio_sha256"]=audio_hash(source)
    report["final_audio_sha256"]=audio_hash(final)
    check("original_dialogue_and_soundtrack_exact",report["source_audio_sha256"]==report["final_audio_sha256"])
    rows=[]
    for sh in e["shots"]:
        job.check()
        take=sh["takes"]["items"][0];path=store.abs_media(take["file"])
        raw=media._run([cfg["ffprobe"],"-v","error","-select_streams","v:0","-count_frames","-show_entries","stream=nb_read_frames","-of","json",path])
        frames=int(json.loads(raw)["streams"][0]["nb_read_frames"])
        expected=sh["reference"]["end_frame"]-sh["reference"]["start_frame"]
        rows.append({"shot_id":sh["id"],"frames":frames,"expected":expected})
        check(sh["id"]+"_frames",frames==expected)
    report["shots"]=rows
    report["comparison_sheets"]=[]
    for page in range(math.ceil(len(e["shots"])/12)):
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
    job.say("Verification report: "+store.rel(str(out/"followup-check.json")))
    if not report["technical_checks_passed"]:raise RuntimeError("Some technical checks failed")
