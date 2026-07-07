# ltx23-toolkit

Battle-tested ComfyUI workflows + a CLI helper for **LTX-2.3 22B** video generation with native audio — packaged so any ComfyUI user can install and run in minutes.

What you get:
- **AV single-shot** (`workflows/ltx23-av-singleshot.json`) — t2v or i2v, up to 15s in one generation, with LTX's native voices/dialogue/SFX and automatic lip-sync. Fast recipe: full bf16 distilled checkpoint, 8-step ManualSigmas, CFG 1 (~1.2s/frame on a 12GB GPU with RAM offload).
- **Ingredients** (`workflows/ltx23-iclora-ingredients.json`) — reference-sheet character/prop/location consistency (IC-LoRA), 121-frame clips.
- **MSR multi-subject** (`workflows/ltx23-msr-multisubject.json`) — feed up to 4 character reference images + a background reference DIRECTLY into the LiconMSR node (requires the community `LTX-2.3-Licon-MSR-V1` LoRA): both/all likenesses hold simultaneously in one continuous shot, ~0.56s/frame at 50fps. Start your prompt with "Maintain strong reference consistency for both characters and the background — do not change faces, hairstyles, costumes, or the background layout." Best route for multi-character dialogue scenes. Tips: the background reference DOMINATES — any character visible in it will be generated, so inspect bg images before feeding; for scenes with two opposing sides (armies, flag sets), generate per-side SOLO shots (drop LiconMSR input `"2"`, one ref into slot 1, per-side clean background) and cut-edit — shared-background two-shots mix the sides' props/flags structurally; need an exact glyph or logo on a banner? draw it onto the background reference (e.g. PIL) — MSR reproduces it faithfully.
- **Seamless long video** (`workflows/ltx23-seamless-long.json`) — LTXVExtendSampler chain: 15s+ continuous video (no cuts) via latent-overlap extension; video-only, add audio with a T2A pass.
- **`scripts/videogen.py`** — patch → queue → poll → download → ffprobe QC in one command, with hard-won guardrails baked in.

## Requirements

- ComfyUI ≥ 0.25 with [ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) custom nodes
- Models (place in your ComfyUI model dirs):

| File | Dir | Source |
|---|---|---|
| `ltx-2.3-22b-distilled-1.1.safetensors` (bf16, recommended) or `ltx-2.3-22b-dev-fp8.safetensors` | `checkpoints/` | [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) / [LTX-2.3-fp8](https://huggingface.co/Lightricks/LTX-2.3-fp8) |
| `ltx-2.3-22b-distilled-lora-384-1.1.safetensors` (only for the dev/fp8 route) | `loras/` | Lightricks/LTX-2.3 |
| `ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors` (Ingredients mode) | `loras/` | [LTX-2.3-22b-IC-LoRA-Ingredients](https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients) (gated) |
| `gemma_3_12B_it_fpmixed.safetensors` + `ltx-2.3_text_projection_bf16.safetensors` | `text_encoders/` | Lightricks/LTX-2.3 |
| `LTX23_video_vae_bf16.safetensors` (Ingredients workflow) | `vae/` | Lightricks/LTX-2.3 |

Tested on Windows ComfyUI 0.25, 12GB VRAM + 100GB RAM. bf16 measured **faster** than fp8+LoRA (362s vs 439s per 15s clip) and sharper — offload cost is smaller than the extra LoRA pass.

> Model filenames inside the workflow JSONs must match your local files — edit the `CheckpointLoaderSimple` / `LTXAVTextEncoderLoader` / lora nodes if yours differ.

## Quick start

```bash
export COMFYUI_URL=http://127.0.0.1:8188   # your ComfyUI address

printf '%s' "your prompt (see Prompt rules)" > /tmp/prompt.txt
python3 scripts/videogen.py \
  --workflow workflows/ltx23-av-singleshot.json \
  --prompt-file /tmp/prompt.txt \
  --frames 361 --width 928 --height 576 \
  [--image start_frame.png] \
  --out my_video.mp4
```
`--image` switches to i2v (start-frame conditioning); omit for t2v — the helper flips the i2v-bypass node automatically. Output JSON includes `gen_seconds`, `mode`, `seed`, stream specs and loudness. The helper refuses a busy GPU (`--allow-busy` to override), validates frame/resolution rules, and prints `{"ok": false, ...}` JSON on every failure path (timeouts include the exact cancel command).

For Claude Code users the bundled skill also defines sub-options: `/videogen <desc>` (default 15s single-shot), `/videogen beats <desc>` (per-beat Ingredients generation + concat), `/videogen long <desc>` (seamless >15s extension + T2A audio).

## The rules this toolkit enforces (learned the hard way)

1. **15 s (361 frames) max per AV single-shot.** Beyond that, audio-mouth lip coupling decouples (voices play, mouths don't move) and held props start morphing into other objects. For longer videos use the seamless-long workflow (+ T2A audio) or concat 121f beats.
2. **Frames must be 8n+1** (121 = 5s, 241 = 10s, 361 = 15s @ 24fps). Resolution in multiples of 32.
3. **Ingredients mode composes only at ≤121 frames.** Longer runs regress to animating the reference sheet itself.
4. **Script object hand-offs in prompts.** When a character switches held objects, write the release explicitly ("slides the blade into its scabbard, lets go, both hands empty, then grasps the ewer already standing on the table") and add "every object stays itself throughout" — otherwise the sword becomes the wine cup.
5. **One GPU job at a time.** The helper refuses to queue while busy (`--allow-busy` to override).

## Prompt format

**Name real people directly** ("Donald Trump", "Uncle Roger") instead of generic descriptions — LTX matches the person's face AND voice characteristics automatically; generic descriptions get generic results. To force a dialogue language against a strong scene context, lock it explicitly ("declares loudly IN ENGLISH, in his American-accented voice, every word in English" — a Chinese-period setting will otherwise pull dialogue into Mandarin).

One flowing present-tense paragraph containing: shot/camera language, scene/lighting, ordered action beats, physical character description, dialogue in quotes with acting beats (`says in a low rasping voice: "..."`), and an audio design line (ambience + SFX + "no background music" unless wanted). For Ingredients mode use the two-part `### Reference Sheet Description` (positioned panel labels) + `### Target Description` structure — see the placeholder inside the workflow JSON.

## QC recipe

- `ffprobe` both streams; `volumedetect` max above ~-30 dB when dialogue is expected.
- Extract frames every ~2s for composition/consistency; **every 0.5s across beat transitions to catch prop morphs** (stills lie about motion — check the actual video too).
- Scripted dialogue? Transcribe the output audio with Whisper and diff against your lines.

## Claude Code users

`skill/videogen/SKILL.md` is a ready-made [Claude Code](https://claude.com/claude-code) skill: `cp -r skill/videogen ~/.claude/skills/` and invoke `/videogen <description>`. It wraps prompt authoring, mode selection, generation, and the QC recipe.

## Seamless long video (>15s)

`workflows/ltx23-seamless-long.json` chains LTXVExtendSampler: 121f base generation → +120f extensions conditioned on a 24-frame latent overlap, each extension with its own beat prompt → decode once. Patch points in the file's `_readme` (beat prompts at nodes 9002/9012, seeds 9005/9015). Extension passes must use plain `euler_ancestral` (cfg_pp samplers crash with the STG guider). Video-only: generate a matching soundtrack with the official T2A workflow and mux (`ffmpeg -map 0:v -map 1:a -c:v copy -c:a aac -b:a 128k`).

## License

MIT. Workflows adapted from [Lightricks/ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) official examples (converted to API format, tuned, and hardened). LTX-2.3 model weights are governed by the LTX-2 Community License — not distributed here.
