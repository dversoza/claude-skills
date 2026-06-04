---
name: video-analyze
description: Analyze a local video by transcribing its audio and extracting only the frames that matter. Use when the user asks to analyze a video, go through a screen recording, review a walkthrough or demo, diagnose bugs shown in a video, summarize a recorded meeting, or "watch" any local video file. Handles narrated walkthroughs (PM bug tours, QA repros, Loom-style screen recordings) and any spoken-over footage. Triggers on requests like "analyze this video", "go through this recording", "what does he point out in this video", "diagnose the issues in this screen recording", "transcribe and summarize this video".
---

# video-analyze

Claude cannot ingest video or audio directly, only text and images. This skill
converts a local video into those two things: a full transcript (text) and a
small set of relevant frames (images). The model reads the transcript, decides
which moments matter, and only those moments are turned into screenshots — so
visual tokens are spent on what is interesting, not on blindly sampled frames.

## Pipeline

```
transcribe  ->  model selects ranges  ->  frames  ->  model reads transcript + frames
```

1. Transcribe the whole video (cheap, always full).
2. Read the transcript and write `ranges.json` — the timestamp ranges worth seeing.
3. Extract frames only for those ranges.
4. Read transcript + frames together and produce the answer (summary, bug list, plan, etc.).

## Requirements

- `ffmpeg` / `ffprobe` (Homebrew: `brew install ffmpeg`)
- `whisper-cli` from whisper.cpp (Homebrew: `brew install whisper-cpp`)
- A ggml model at `~/.cache/whisper/ggml-large-v3-turbo.bin`, or set `WHISPER_MODEL`.
  Download:
  ```bash
  curl -L --fail -o ~/.cache/whisper/ggml-large-v3-turbo.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
  ```

## Step 1 — transcribe

```bash
python3 ~/.claude/skills/video-analyze/scripts/analyze.py transcribe <video> [--lang pt] [--out DIR]
```

`--lang` defaults to `pt`; use `auto` to detect, or any whisper language code.
Outputs into `<video>_analysis/`: `audio.wav`, `transcript.srt`, `transcript.json`,
`transcript.txt`. Read `transcript.srt` — the `HH:MM:SS,mmm` timestamps are what
you anchor frame ranges to.

## Step 2 — select ranges (the model does this)

Read the transcript and decide which moments are worth seeing. Do not rely on a
keyword grep — judge intent. For a narrated walkthrough that means both:

- pointing/deictic moments ("olha aqui", "esse botão", "essa tela"), and
- problem/request statements with no pointing word ("o login tá lento",
  "esse fluxo tá confuso", "queria que mudasse").

Skip filler, greetings, and tangents. Write `<video>_analysis/ranges.json` as an
array; `start`/`end` accept seconds or `MM:SS` / `HH:MM:SS`:

```json
[
  {"start": "1:05", "end": "1:20", "reason": "save button does nothing on click"},
  {"start": "3:42", "end": "3:55", "reason": "login flow described as too slow"}
]
```

`reason` is free text carried into the frame manifest so each screenshot keeps
its context. Keep ranges tight — frames get padded automatically (see below).

## Step 3 — extract frames

```bash
python3 ~/.claude/skills/video-analyze/scripts/analyze.py frames <video> <ranges.json> \
  [--pre 2] [--post 5] [--max-per-window 10] [--out DIR]
```

Each range is padded by `--pre`/`--post` seconds (a "look here" is often a beat
before the click); overlapping windows are merged so nothing is captured twice.

By default frames are selected on **scene change** (`--mode scene`): a frame is
kept whenever the screen changes meaningfully (`--scene-threshold`, default 0.15),
the first frame of each window is always kept, and at least one frame is taken
every `--floor` seconds (default 4) so slow or text-only changes — a single field
re-rendering, a wrong auto-generated code — are not missed. Each window is then
capped at `--max-per-window` (default 10), evenly subsampled. This keeps the set
small enough to view in full while still capturing each distinct on-screen state;
fixed-interval `fps` sampling produced many redundant near-identical frames.

For fast-moving footage where you want uniform sampling instead, use
`--mode interval --fps N`.

Frames land in `<video>_analysis/frames/`, named `<timestamp>_w<window>_<n>.png`
so the filename maps back to the transcript. `frames/manifest.json` records which
ranges each window covers. Raise `--max-per-window` if a video has brief,
transient states (a flashing modal) that even subsampling might drop.

## Step 4 — answer

Read `transcript.srt` and view the frames. Tie each frame to its transcript line
via the timestamp in the filename / manifest. For a bug-diagnosis request,
produce per item: the ask, the timestamp/frame evidence, the likely code path,
a hypothesis, and a proposed fix — then a prioritized plan.

## Notes

- Local files only; download/acquire the video separately first.
- The `<video>_analysis/` dir holds the wav and frames — gitignore it; it is scratch.
- Tune `--fps` up for fast-moving UI, down for mostly-static screens.
