---
name: videogen
description: Generate an LTX-2.3 video (with native audio/dialogue) on the GPU ComfyUI server from a description and optional reference image. Use when the user invokes /videogen, asks to "make a video of ...", "영상 만들어줘", or wants an AI video clip. Handles t2v, i2v (start-frame), and character-consistency (Ingredients reference-sheet) modes.
---

# /videogen — LTX-2.3 video generation pipeline

Wraps the verified 2026-07-07 pipeline. Full background: the toolkit README.

## Hard rules (non-negotiable)

- **15s (361 frames) MAX per single-shot** — user-set limit; beyond it lip-sync decouples and props morph. Longer videos: ExtendSampler chain (see memory).
- Frames must be **8n+1** (121=5s, 241=10s, 361=15s @24fps). Resolution multiples of 32 (default 928×576, 24fps).
- **GPU sequential** — one ComfyUI job at a time, never parallel (set COMFYUI_URL).
- **Report generation seconds** in every delivery (helper prints `gen_seconds`).
- Heavy generation runs in **background** (Bash run_in_background or agent) so the session stays responsive; text-channel request → deliver file to text channel.

## Sub-options (first word of the /videogen argument)

| Invocation | Route |
|---|---|
| `/videogen <description>` | DEFAULT: 15s AV single-shot (real photos + named persons) |
| `/videogen beats <description>` (aliases: `비트합본`, `합본`) | Beat-concat route: Ingredients 121f per beat → reroll each until composed (frame-inspect every roll) → ffmpeg concat re-encode (h264 crf18 + aac 128k). ONLY on this explicit sub-option — never unprompted |
| `/videogen long <description>` (alias: `롱`) | >15s seamless: ExtendSampler chain (video) + T2A audio mux |

## Mode selection

| Situation | Mode | Workflow file (<toolkit>/workflows/) |
|---|---|---|
| Description only, or description + start-frame image | **AV single-shot** (default; native voice/SFX, auto lip-sync) | `ltx23-av-singleshot.json` (bf16 distilled-1.1 — faster AND sharper than fp8) |
| Same character/prop/location must persist across clips | **Ingredients** (121f units only!) | `ltx23-iclora-ingredients.json` |
| >15s seamless (video-only + T2A audio muxed after) | ExtendSampler chain — **helper insufficient**: patch beat prompts (9002/9012), seeds (9005/9015), num_new_frames (9006/9016) manually or via repeated `--set` | `ltx23-seamless-long.json` |
| Establish-then-extend combo (motion continuity + 15s dialogue, weaker likeness lock) | Step 1: Ingredients 2-panel 121f to establish the scene → Step 2: extract the best composed frame (`ffmpeg -vf "select=eq(n\,60)" -vframes 1`) → Step 3: AV single-shot 361f i2v from that frame with the full multi-beat named-person dialogue prompt | both workflows in sequence |

Mode toggle: the helper flips i2v/t2v automatically — `--image` present → i2v (bypass node 4977 set false), absent → t2v (bypass true; image node ignored). Ingredients refsheets: pre-resize shorter side to latent height; AV single-shot resizes internally (node 4981).

## Prompt authoring (do this BEFORE calling the helper)

Follow the official structure — one flowing present-tense paragraph (or `### Reference Sheet Description` + `### Target Description` for Ingredients). Name real people directly ("Donald Trump", "Uncle Roger") — LTX auto-matches their voice characteristics; generic descriptions get generic voices. Must include: shot/camera, scene/lighting, action beats in order, character physical description, dialogue in quotes with acting beats ("says in a low rasping voice: \"...\""), audio design (ambience + SFX + "no background music" unless wanted). Rules:
- **Object hand-offs**: when a held object changes, script the release ("slides the blade back into its scabbard, lets go, both hands empty, then grasps the ewer already standing on the table"). Add invariant line: "every object stays itself throughout".
- Anti-collage line for Ingredients mode: "the frame is one single continuous cinematic shot — never a reference sheet, never split panels".
- Complex authoring → invoke the `ltx-prompt` skill.

## Generate

```bash
printf '%s' "$PROMPT" > /tmp/videogen_prompt.txt
python3 <toolkit>/scripts/videogen.py \
  --workflow <toolkit>/workflows/ltx23-av-singleshot.json \
  --prompt-file /tmp/videogen_prompt.txt \
  [--image ref.png] --frames 361 --seed <n> --prefix <name> --out /tmp/<name>.mp4
```
Run in background. Timing (measured): **AV single-shot ≈1.0-1.2s/frame** (121f=132s, 361f≈360-440s); **Ingredients distilled ≈2.4s/frame** (121f≈290s). Helper validates rules (frames 121-361 & 8n+1, res 32-mult ≥256), refuses a busy GPU (override `--allow-busy`), queues, polls, downloads, ffprobe-QCs, prints JSON with `gen_seconds`/`mode`/`seed`. Ingredients frames node (5072) auto-detected; other exotic nodes via `--map` / `--set NODEID.KEY=VALUE`.

## Failure handling

Helper prints `{"ok": false, "error": ...}` on every failure: server unreachable → report GPU server down, don't retry-loop; workflow validation errors → fix node patches; execution error → read message, fix, requeue; timeout → the error includes the exact `/interrupt` / queue-delete curl to cancel the orphan job — run it before requeueing. GPU busy → wait for the running job (never stack).

## QC before delivery (mandatory)

1. Helper output: audio stream present, `max_volume` > -30dB (if dialogue/SFX expected).
2. Frame grid: extract frames every ~2s (`ffmpeg -vf "select=eq(n\,N)" -vframes 1`), Read the grid — composition, character consistency, no sheet-copy/panels.
3. **Beat transitions: 0.5s-interval frames, track each held object's identity** (prop-morph check — stills can lie about motion; if user reports a morph you missed, trust the user).
4. Dialogue check (when scripted): extract audio → `mlx_whisper` transcribe → compare lines.
5. Defect → reroll with new seed (cheap); prompt-level fix for morphs (hand-off scripting).

## Deliver

Deliver to the user's channel: attach mp4 + **full settings (workflow, model, mode/start-frame, resolution/frames/fps, sampler config, seed) + the complete prompt text + 생성 N초** + any honest defect notes. Settings and prompts accompany EVERY deliverable. Never claim quality without the QC pass.
