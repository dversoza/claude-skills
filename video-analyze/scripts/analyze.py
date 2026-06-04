#!/usr/bin/env python3
"""video-analyze: transcribe a video and extract frames for selected time ranges.

Two subcommands form a pipeline with a model-in-the-loop step between them:

  1. transcribe <video>            -> audio.wav, transcript.srt, transcript.json, transcript.txt
  2. (the assistant reads the transcript and writes ranges.json)
  3. frames <video> <ranges.json>  -> frames/ + frames/manifest.json

The transcript is always produced in full (cheap). Frame extraction is gated to
the ranges the assistant judged relevant (visual tokens are the expensive part).

Dependencies: ffmpeg, ffprobe, whisper-cli (all on PATH). Python stdlib only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = Path.home() / ".cache" / "whisper" / "ggml-large-v3-turbo.bin"


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def need(tool):
    if not shutil.which(tool):
        die(f"'{tool}' not found on PATH")
    return tool


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        die(f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n{proc.stdout}")
    return proc.stdout


def workdir_for(video, out):
    if out:
        d = Path(out)
    else:
        v = Path(video)
        d = v.parent / f"{v.stem}_analysis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_ts(value):
    """Accept seconds (int/float) or 'HH:MM:SS(.ms)' / 'MM:SS' strings -> float seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        die(f"unparseable timestamp: {value!r}")
    parts = [float(p) for p in parts]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_ts(sec):
    sec = max(0.0, sec)
    m, s = divmod(int(round(sec)), 60)
    return f"{m}m{s:02d}s"


def cmd_transcribe(args):
    need("ffmpeg")
    need("whisper-cli")
    model = Path(os.environ.get("WHISPER_MODEL", DEFAULT_MODEL))
    if not model.exists():
        die(f"model not found: {model}\n"
            f"set WHISPER_MODEL or download with:\n"
            f"  curl -L --fail -o {DEFAULT_MODEL} \\\n"
            f"    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin")
    video = Path(args.video)
    if not video.exists():
        die(f"video not found: {video}")
    wd = workdir_for(video, args.out)
    wav = wd / "audio.wav"

    print(f"[1/2] extracting 16kHz mono audio -> {wav}")
    run(["ffmpeg", "-y", "-i", str(video), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(wav)])

    prefix = wd / "transcript"
    print(f"[2/2] transcribing (lang={args.lang}, model={model.name}) ...")
    whisper = ["whisper-cli", "-m", str(model), "-f", str(wav),
               "-l", args.lang, "-osrt", "-oj", "-otxt", "-of", str(prefix)]
    if args.lang == "auto":
        whisper[whisper.index("-l") + 1] = "auto"
    print(run(whisper)[-2000:])

    print("\ndone. outputs:")
    for ext in ("srt", "json", "txt"):
        p = prefix.with_suffix(f".{ext}")
        if p.exists():
            print(f"  {p}")
    print(f"\nnext: read {prefix.with_suffix('.srt')}, choose relevant ranges, write a ranges.json:")
    print('  [{"start": "1:05", "end": "1:20", "reason": "broken save button"}, ...]')
    print(f"then: analyze.py frames {video} {wd / 'ranges.json'}")


def merge_windows(windows):
    """windows: list of (start, end, label). Merge overlapping/adjacent, keep labels."""
    windows = sorted(windows, key=lambda w: w[0])
    merged = []
    for start, end, label in windows:
        if merged and start <= merged[-1][1]:
            ps, pe, pl = merged[-1]
            merged[-1] = (ps, max(pe, end), pl + [label] if isinstance(pl, list) else [pl, label])
        else:
            merged.append((start, end, [label]))
    return merged


def extract_window(video, a, b, w_idx, frames_dir, args):
    """Extract frames for one window. Returns list of (Path, global_seconds).

    Scene mode selects a frame when the screen changes (scene > threshold), always
    keeps the first frame, and guarantees one at least every --floor seconds so slow
    or text-only changes (e.g. a single field re-rendering) are not missed.
    Interval mode samples at a fixed --fps. Both are capped at --max-per-window.
    """
    if args.mode == "scene":
        vf = (f"select='isnan(prev_selected_t)+gt(scene,{args.scene_threshold})"
              f"+gte(t-prev_selected_t,{args.floor})',showinfo")
    else:
        vf = f"fps={args.fps},showinfo"
    pattern = frames_dir / f"w{w_idx:02d}_%04d.png"
    out = run(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(video),
               "-vf", vf, "-vsync", "vfr", str(pattern)])
    produced = sorted(frames_dir.glob(f"w{w_idx:02d}_*.png"))
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", out)]
    if len(times) != len(produced):  # showinfo parse drifted; fall back to even spacing
        n = len(produced)
        times = [(b - a) * k / max(1, n - 1) for k in range(n)] if n > 1 else [0.0]

    if len(produced) > args.max_per_window:  # evenly subsample, keep first and last
        cap = args.max_per_window
        keep = {round(k * (len(produced) - 1) / (cap - 1)) for k in range(cap)} if cap > 1 else {0}
        kept = []
        for k, (p, t) in enumerate(zip(produced, times)):
            if k in keep:
                kept.append((p, t))
            else:
                p.unlink()
        produced, times = [p for p, _ in kept], [t for _, t in kept]

    result = []
    for k, (p, t) in enumerate(zip(produced, times)):
        newname = frames_dir / f"{fmt_ts(a + t)}_w{w_idx:02d}_{k:03d}.png"
        p.rename(newname)
        result.append((newname, a + t))
    return result


def cmd_frames(args):
    need("ffmpeg")
    video = Path(args.video)
    if not video.exists():
        die(f"video not found: {video}")
    ranges_path = Path(args.ranges)
    if not ranges_path.exists():
        die(f"ranges file not found: {ranges_path}")
    try:
        ranges = json.loads(ranges_path.read_text())
    except json.JSONDecodeError as e:
        die(f"invalid JSON in {ranges_path}: {e}")
    if not isinstance(ranges, list) or not ranges:
        die("ranges.json must be a non-empty JSON array")

    wd = workdir_for(video, args.out)
    frames_dir = wd / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    windows = []
    for i, r in enumerate(ranges):
        start = parse_ts(r.get("start", r.get("t", 0)))
        end = parse_ts(r["end"]) if "end" in r else start
        reason = r.get("reason", f"range {i}")
        a = max(0.0, start - args.pre)
        b = end + args.post
        windows.append((a, b, {"index": i, "reason": reason,
                               "orig_start": start, "orig_end": end}))
    merged = merge_windows(windows)

    manifest = []
    total = 0
    for w_idx, (a, b, labels) in enumerate(merged):
        frames = extract_window(video, a, b, w_idx, frames_dir, args)
        total += len(frames)
        manifest.append({
            "window": w_idx,
            "start": round(a, 2), "end": round(b, 2),
            "start_label": fmt_ts(a), "end_label": fmt_ts(b),
            "covers": labels,
            "frames": [p.name for p, _ in frames],
        })

    (frames_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    mode_desc = (f"scene (thr={args.scene_threshold}, floor={args.floor}s)"
                 if args.mode == "scene" else f"interval ({args.fps} fps)")
    print(f"extracted {total} frames across {len(merged)} window(s) (from {len(ranges)} range(s)) "
          f"using {mode_desc}, capped at {args.max_per_window}/window")
    print(f"  frames:   {frames_dir}/")
    print(f"  manifest: {frames_dir / 'manifest.json'}")


def main():
    p = argparse.ArgumentParser(description="Transcribe a video and extract frames for selected ranges.")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="extract audio and transcribe to srt/json/txt")
    t.add_argument("video")
    t.add_argument("--lang", default="pt", help="language code or 'auto' (default: pt)")
    t.add_argument("--out", default=None, help="output dir (default: <video>_analysis)")
    t.set_defaults(func=cmd_transcribe)

    f = sub.add_parser("frames", help="extract frames for ranges.json windows")
    f.add_argument("video")
    f.add_argument("ranges", help="path to ranges.json")
    f.add_argument("--out", default=None, help="output dir (default: <video>_analysis)")
    f.add_argument("--pre", type=float, default=2.0, help="seconds before each range start (default: 2)")
    f.add_argument("--post", type=float, default=5.0, help="seconds after each range end (default: 5)")
    f.add_argument("--mode", choices=("scene", "interval"), default="scene",
                   help="scene-change selection (default) or fixed-interval sampling")
    f.add_argument("--scene-threshold", type=float, default=0.15,
                   help="scene mode: change sensitivity 0-1, lower = more frames (default: 0.15)")
    f.add_argument("--floor", type=float, default=4.0,
                   help="scene mode: max seconds between frames, catches slow changes (default: 4)")
    f.add_argument("--max-per-window", type=int, default=10,
                   help="cap frames per window, evenly subsampled if exceeded (default: 10)")
    f.add_argument("--fps", type=float, default=1.0,
                   help="interval mode: frames per second within windows (default: 1)")
    f.set_defaults(func=cmd_frames)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
