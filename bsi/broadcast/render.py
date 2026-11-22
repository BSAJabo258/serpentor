"""Episode assembly: narration + living-data frames -> finished deliverables.

Outputs per episode folder:
  episode.mp4      16:9 documentary episode (YouTube)
  short.mp4        9:16 cut with burned captions (Shorts / Reels / TikTok)
  thumbnail.png    1280x720
  carousel_NN.png  1080x1350 slides (Instagram / Facebook / LinkedIn / X)
  captions.srt     closed captions for the episode
  description.md   description with chapters, sources, status and AI disclosure
  post.txt         social caption
  manifest.json    every output's SHA-256, plus the case-file spec hash it was built from
"""
import hashlib
import json
import os
import random
import re
import subprocess
from pathlib import Path

import numpy as np

from ..text import sentences  # noqa: F401  (shared, abbreviation-aware)
from . import visuals, voice
from .episode import STATUS_LABEL

FPS = int(os.environ.get("BSI_FPS", "30"))
LEAD = 0.35     # silence before narration in each scene
TAIL = 0.65     # breath after
XF = 0.4        # crossfade seconds
ANIM_END = {"title": 2.2, "claim": 4.0, "evidence": 2.0, "scale": 2.3, "questions": 4.0,
            "note": 1.0, "sources": 3.5, "timeline": 999}




def fmt_ts(t, srt=True):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s - int(s)) * 1000):03d}"
    return f"{int(m)}:{int(s):02d}" if h < 1 else f"{int(h)}:{int(m):02d}:{int(s):02d}"


def narrate(script, ep_dir: Path):
    work = ep_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    tracks, used = [], set()
    for i, sc in enumerate(script["scenes"], 1):
        pcm, v = voice.speak(sc["narration"], work, i, ep_dir)
        used.add(v.split(":")[0] if v != "own" else "own")
        lead = np.zeros(int(LEAD * voice.RATE), np.int16)
        tail = np.zeros(int(TAIL * voice.RATE), np.int16)
        seg = np.concatenate([lead, pcm, tail])
        min_len = int(3.5 * voice.RATE)
        if len(seg) < min_len:
            seg = np.concatenate([seg, np.zeros(min_len - len(seg), np.int16)])
        tracks.append({"pcm": seg, "speech": len(pcm) / voice.RATE, "dur": len(seg) / voice.RATE, "voice": v})
    return tracks, used


def captions_for(scene_text, start, speech_dur):
    """Sentence timings within a scene, proportional to length."""
    ss = sentences(scene_text)
    total = sum(len(s) for s in ss) or 1
    t, out = start + LEAD, []
    for s in ss:
        d = speech_dur * len(s) / total
        out.append((t, t + d, s))
        t += d
    return out


def frames(script, tracks, W, H, ctx_base, cache_dir, captions=None, scene_idx=None):
    """Yield raw RGB frames for the whole timeline, crossfading between scenes."""
    idx = scene_idx or list(range(len(script["scenes"])))
    prev_last = None
    t_global = 0.0
    page_cache = {}

    def page_image(ev):
        k = ev["sha256"]
        if k not in page_cache:
            page_cache[k] = visuals.page_image(ev, cache_dir)
        return page_cache[k]

    for i in idx:
        sc = script["scenes"][i]
        dur = tracks[i]["dur"]
        n = int(round(dur * FPS))
        has_motion = sc["type"] == "timeline" or (sc["type"] == "evidence" and page_image(sc["evidence"]) is not None)
        anim_end = ANIM_END.get(sc["type"], 2.0)
        cached, cached_cap = None, None
        for f in range(n):
            t = f / FPS
            cap = None
            if captions:
                tg = t_global + t
                cap = next((c[2] for c in captions if c[0] <= tg < c[1]), None)
            ctx = {**ctx_base, "page_image": page_image, "caption": cap}
            if cached is not None and not has_motion and t > anim_end and cap == cached_cap:
                frame_bytes = cached
            else:
                im = visuals.render(sc, W, H, t, dur, ctx)
                if prev_last is not None and t < XF:
                    from PIL import Image
                    im = Image.blend(prev_last, im, t / XF)
                frame_bytes = im.tobytes()
                if t > anim_end and not has_motion:
                    cached, cached_cap = frame_bytes, cap
                last_im = im
            yield frame_bytes
        prev_last = visuals.render(sc, W, H, dur, dur, {**ctx_base, "page_image": page_image, "caption": None}) \
            if not has_motion else last_im
        t_global += dur


def encode(frame_iter, W, H, audio_wav: Path, out: Path, music: Path | None):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(audio_wav)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music), "-filter_complex",
                "[2:a]volume=0.35,aformat=channel_layouts=stereo[m];"
                "[1:a]aformat=channel_layouts=stereo,asplit=2[v][sc];"
                "[m][sc]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=400[duck];"
                "[v][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5", "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", os.environ.get("BSI_X264_PRESET", "veryfast"), "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for fb in frame_iter:
            p.stdin.write(fb)
    finally:
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("ffmpeg failed")


def pick_music(seed: str) -> Path | None:
    d = os.environ.get("BSI_MUSIC_DIR")
    if not d or not Path(d).is_dir():
        return None
    tracks = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in {".wav", ".mp3", ".flac", ".m4a", ".ogg"})
    return random.Random(seed).choice(tracks) if tracks else None


def short_scenes(script, tracks, limit=58.0):
    """Hook -> strongest evidence -> where it stands -> what would settle it, under a minute."""
    sc = script["scenes"]
    order = [i for i, s in enumerate(sc) if s["type"] == "title"]
    ev = [i for i, s in enumerate(sc) if s["type"] == "evidence"]
    if ev:
        order.append(ev[0])
    order += [i for i, s in enumerate(sc) if s["type"] == "scale"]
    order += [i for i, s in enumerate(sc) if s["type"] == "questions"]
    out, total = [], 0.0
    for i in order:
        if total + tracks[i]["dur"] <= limit:
            out.append(i)
            total += tracks[i]["dur"]
    return out


def sha(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def render_episode(script: dict, ep_dir: Path, *, landscape=(1920, 1080), vertical=(1080, 1920),
                   make_short=True) -> dict:
    ep_dir.mkdir(parents=True, exist_ok=True)
    cache = ep_dir / "work"
    tracks, voices = narrate(script, ep_dir)
    ctx = {"status": script["status"], "as_of": script["as_of"]}

    # audio + captions + chapters
    full = np.concatenate([t["pcm"] for t in tracks])
    voice.write_wav(cache / "narration.wav", full)
    caps, chapters, t0 = [], [], 0.0
    names = {"title": "Cold open", "claim": "The claim", "timeline": "The portrayals", "evidence": "Evidence",
             "scale": "Where it stands", "questions": "What would settle it", "note": "Producer's note",
             "sources": "Sources"}
    for sc, tr in zip(script["scenes"], tracks):
        caps += captions_for(sc["narration"], t0, tr["speech"])
        label = names[sc["type"]] + (f" {sc['index']}: {sc['evidence']['title'][:40]}" if sc["type"] == "evidence" else "")
        chapters.append((t0, label))
        t0 += tr["dur"]
    srt = "\n".join(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(caps, 1))
    (ep_dir / "captions.srt").write_text(srt)

    music = pick_music(f"{script['claim_id']}:{script['kind']}")
    W, H = landscape
    encode(frames(script, tracks, W, H, ctx, cache), W, H, cache / "narration.wav", ep_dir / "episode.mp4", music)
    files = {"episode": "episode.mp4", "captions": "captions.srt"}

    if make_short:
        idx = short_scenes(script, tracks)
        spcm = np.concatenate([tracks[i]["pcm"] for i in idx])
        voice.write_wav(cache / "short.wav", spcm)
        scaps, tt = [], 0.0
        for i in idx:
            scaps += captions_for(script["scenes"][i]["narration"], tt, tracks[i]["speech"])
            tt += tracks[i]["dur"]
        VW, VH = vertical
        encode(frames(script, tracks, VW, VH, ctx, cache, captions=scaps, scene_idx=idx), VW, VH,
               cache / "short.wav", ep_dir / "short.mp4", music)
        files["short"] = "short.mp4"

    visuals.thumbnail(script, cache_dir=cache).save(ep_dir / "thumbnail.png")
    files["thumbnail"] = "thumbnail.png"
    slides, n = [], 0
    for sc in script["scenes"]:
        if sc["type"] in ("title", "claim", "evidence", "scale", "questions", "sources") and n < 10:
            if sc["type"] == "evidence" and sum(1 for s in slides if s.startswith("ev")) >= 3:
                continue
            n += 1
            name = f"carousel_{n:02d}.png"
            visuals.still(sc, 1080, 1350, {**ctx, "page_image": lambda e: visuals.page_image(e, cache)}).save(ep_dir / name)
            files[name.removesuffix(".png")] = name
            slides.append("ev" if sc["type"] == "evidence" else sc["type"])

    used_tts = "piper" in voices
    (ep_dir / "description.md").write_text(description(script, chapters, used_tts, music))
    (ep_dir / "post.txt").write_text(post_caption(script))
    files.update({"description": "description.md", "post": "post.txt"})
    manifest = {"claim_id": script["claim_id"], "kind": script["kind"], "status": script["status"],
                "as_of": script["as_of"], "spec_hash": script["spec_hash"], "voices": sorted(voices),
                "music": music.name if music else None, "synthetic_voice": used_tts,
                "files": {k: {"path": v, "sha256": sha(ep_dir / v)} for k, v in files.items()}}
    (ep_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    files["manifest"] = "manifest.json"
    return {"files": files, "voice": ",".join(sorted(voices)), "synthetic_voice": used_tts,
            "duration": round(sum(t["dur"] for t in tracks), 1), "manifest": manifest}


def description(script, chapters, used_tts, music):
    sc0 = script["scenes"][0]
    src = next(s for s in script["scenes"] if s["type"] == "sources")
    lines = [script["title"], "",
             f"Claim under investigation: {sc0['claim']}",
             f"Status as of {script['as_of']}: {STATUS_LABEL[script['status']]} — computed from the evidence "
             "in the case file, not from opinion. It changes when the evidence changes.", "",
             "CHAPTERS"]
    lines += [f"{fmt_ts(t, srt=False)} {label}" for t, label in chapters]
    lines += ["", "SOURCES"]
    for s in src["sources"]:
        meta = ", ".join(x for x in [s["holder"] or "", str(s["date"] or s["year"] or ""), s["tier"]] if x)
        lines.append(f"• {s['title']} ({meta}) — sha256 {s['sha256'][:16]}")
    if not src["sources"]:
        lines.append("• None yet — this is an open case.")
    lines += ["", "HOW THIS SERIES WORKS",
              "Every claim is tested against original records. Evidence for AND against is shown. "
              "No institution and no alternative account gets automatic credibility, and no AI gets the final word.",
              "If you hold records that bear on this case, the file stays open."]
    if used_tts:
        lines += ["", "Disclosure: narration is a synthetic voice (local text-to-speech) reading a script "
                      "generated from the case file and reviewed by a human before publishing."]
    if music:
        lines += [f"Music: {music.stem} — original production by BAU Entertainment."]
    return "\n".join(lines) + "\n"


def post_caption(script):
    sc0 = script["scenes"][0]
    return (f"{sc0['hook']}\n\nCase File Nº {sc0['case_no']:04d}: {sc0['name']}\n"
            f"Status today: {STATUS_LABEL[script['status']]}\n\n"
            "Evidence for and against, with every source named. Full episode on the channel.\n\n"
            "#history #archives #primarysources #documentary #blackhistory\n")
