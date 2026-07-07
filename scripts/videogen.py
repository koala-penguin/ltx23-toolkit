#!/usr/bin/env python3
"""videogen.py — LTX-2.3 generation helper for the /videogen skill.

Patches a saved API-format ComfyUI workflow, queues it, polls to completion,
downloads the mp4, runs ffprobe QC, and prints a JSON result (incl. gen seconds).
Prints {"ok": false, "error": ...} JSON on EVERY failure path.

Usage:
  python3 videogen.py --workflow ~/projects/comfyui-workflows/ltx23-t2v-i2v-av-singleshot.json \
      --prompt-file /tmp/prompt.txt [--image /path/ref.png] [--frames 361] [--seed 12345] \
      [--width 928 --height 576] [--prefix videogen] [--out /tmp/out.mp4] [--timeout 1800] \
      [--set NODEID.KEY=VALUE ...]

Node-id map (singleshot workflow defaults):
  prompt 2483 / negative 2612 / image 2004 / frames 4979 (auto-falls back to 5072
  for the ingredients workflow) / latent 3059 / seed 4832 / save 4852 /
  i2v-bypass 4977 (True = ignore image = t2v; helper sets it from --image presence).
Override with --map '{"frames":"5072",...}'. Arbitrary extra patches: --set.
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request, urllib.parse

SERVER = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DEFAULT_MAP = {"prompt": "2483", "negative": "2612", "image": "2004",
               "frames": "4979", "latent": "3059", "seed": "4832", "save": "4852",
               "bypass": "4977"}

def fail(msg, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}))
    sys.exit(1)

def api(path, data=None, timeout=30):
    req = urllib.request.Request(SERVER + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"} if data is not None else {})
    return json.load(urllib.request.urlopen(req, timeout=timeout))

def upload_image(path):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        fail(f"image file not found: {path}")
    r = subprocess.run(["curl", "-s", "--max-time", "60", "-X", "POST",
                        "-F", f"image=@{path}", "-F", "overwrite=true",
                        f"{SERVER}/upload/image"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        fail(f"image upload failed (curl rc={r.returncode}): {r.stderr[:200]}")
    try:
        return json.loads(r.stdout)["name"]
    except Exception:
        fail(f"image upload returned non-JSON: {r.stdout[:200]}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", help="ComfyUI base URL (overrides COMFYUI_URL)")
    p.add_argument("--workflow", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--negative-file")
    p.add_argument("--image")
    p.add_argument("--frames", type=int, default=361)
    p.add_argument("--width", type=int, default=928)
    p.add_argument("--height", type=int, default=576)
    p.add_argument("--seed", type=int, default=(int(time.time() * 1000) % 10**9))
    p.add_argument("--prefix", default="videogen")
    p.add_argument("--out", default="/tmp/videogen_out.mp4")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--map", help="JSON overrides for node-id map")
    p.add_argument("--set", action="append", default=[],
                   help="extra patch NODEID.KEY=VALUE (VALUE parsed as JSON if possible)")
    p.add_argument("--allow-busy", action="store_true",
                   help="queue even if another job is running")
    a = p.parse_args()
    global SERVER
    if a.server: SERVER = a.server.rstrip("/")

    if not (121 <= a.frames <= 361):
        fail(f"frames must be 121..361 (15s max — beyond this, AV lip-sync decouples and prop permanence degrades); got {a.frames}. "
             "Longer: use segment/extend pipelines.")
    if (a.frames - 1) % 8 != 0:
        fail(f"frames must be 8n+1 (121/241/361...); got {a.frames}.")
    if a.width % 32 or a.height % 32 or a.width < 256 or a.height < 256:
        fail("width/height must be multiples of 32, >= 256.")
    prompt_path = os.path.expanduser(a.prompt_file)
    if not os.path.isfile(prompt_path):
        fail(f"prompt file not found: {prompt_path}")
    prompt_text = open(prompt_path).read().strip()
    if not prompt_text:
        fail("prompt file is empty.")

    m = dict(DEFAULT_MAP)
    if a.map: m.update(json.loads(a.map))

    wf_path = os.path.expanduser(a.workflow)
    if not os.path.isfile(wf_path):
        fail(f"workflow not found: {wf_path}")
    wf = json.load(open(wf_path))
    wf.pop("_readme", None)

    # auto-fallbacks for known workflow variants
    if m["frames"] not in wf and "5072" in wf: m["frames"] = "5072"
    required = {k: m[k] for k in ("prompt", "frames", "latent", "seed", "save")}
    missing = {k: nid for k, nid in required.items() if nid not in wf}
    if missing:
        fail(f"node ids missing from workflow: {missing} — check the file's "
             "_readme.patch_points and pass --map.", map_used=m)

    wf[m["prompt"]]["inputs"]["text"] = prompt_text
    if a.negative_file:
        neg = os.path.expanduser(a.negative_file)
        if not os.path.isfile(neg): fail(f"negative file not found: {neg}")
        if m.get("negative") in wf:
            wf[m["negative"]]["inputs"]["text"] = open(neg).read().strip()
    if a.image:
        if m.get("image") not in wf:
            fail("--image given but image node missing from workflow.", map_used=m)
        wf[m["image"]]["inputs"]["image"] = upload_image(a.image)
    # i2v/t2v toggle: bypass=True disables image conditioning (t2v)
    if m.get("bypass") in wf:
        wf[m["bypass"]]["inputs"]["value"] = (a.image is None)
    wf[m["frames"]]["inputs"]["value"] = a.frames
    wf[m["latent"]]["inputs"]["width"] = a.width
    wf[m["latent"]]["inputs"]["height"] = a.height
    wf[m["seed"]]["inputs"]["noise_seed"] = a.seed
    wf[m["save"]]["inputs"]["filename_prefix"] = a.prefix
    for s in a.set:
        target, val = s.split("=", 1)
        nid, key = target.split(".", 1)
        if nid not in wf: fail(f"--set node {nid} not in workflow.")
        try: val = json.loads(val)
        except Exception: pass
        wf[nid]["inputs"][key] = val

    # GPU is one-job-at-a-time: refuse to stack unless told
    try:
        q = api("/queue", timeout=25)
        busy = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
        if busy and not a.allow_busy:
            fail(f"GPU busy ({busy} job(s) queued/running). Wait or pass --allow-busy.")
    except urllib.error.URLError as e:
        fail(f"ComfyUI server unreachable at {SERVER}: {e}")

    try:
        resp = api("/prompt", {"prompt": wf})
    except urllib.error.HTTPError as e:
        try: body = json.load(e)
        except Exception: body = {"raw": str(e)}
        fail("workflow validation failed",
             node_errors=body.get("node_errors"), detail=body.get("error"))
    pid = resp["prompt_id"]
    print(f"queued {pid}", file=sys.stderr)

    t0, poll_fails = time.time(), 0
    while time.time() - t0 < a.timeout:
        time.sleep(20)
        try:
            h = api(f"/history/{pid}", timeout=25)
            poll_fails = 0
        except Exception as e:
            poll_fails += 1
            if poll_fails % 5 == 0:
                print(f"warn: {poll_fails} consecutive poll failures ({e})", file=sys.stderr)
            continue
        if pid not in h: continue
        st = h[pid].get("status", {})
        if not st.get("completed") and st.get("status_str") != "error": continue
        if st.get("status_str") == "error":
            errs = [f"{x[1].get('node_type')}: {str(x[1].get('exception_message'))[:200]}"
                    for x in st.get("messages", []) if x[0] == "execution_error"]
            if not errs:
                errs = [f"{x[0]}" for x in st.get("messages", [])
                        if x[0] not in ("execution_start", "execution_cached", "execution_success")]
            fail("execution error: " + ("; ".join(errs) or "unknown"), prompt_id=pid)
        ts = [x[1].get("timestamp") for x in st.get("messages", [])
              if isinstance(x[1], dict) and x[1].get("timestamp")]
        gen_s = round((max(ts) - min(ts)) / 1000) if len(ts) >= 2 else None
        for nid, out in (h[pid].get("outputs") or {}).items():
            for key, arr in out.items():
                if not isinstance(arr, list): continue
                for f in arr:
                    if isinstance(f, dict) and f.get("filename", "").endswith(".mp4"):
                        qs = urllib.parse.urlencode({"filename": f["filename"],
                             "subfolder": f.get("subfolder", ""), "type": "output"})
                        data = urllib.request.urlopen(f"{SERVER}/view?{qs}", timeout=300).read()
                        out_path = os.path.expanduser(a.out)
                        open(out_path, "wb").write(data)
                        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,duration,bit_rate", "-of", "json",
                            out_path], capture_output=True, text=True)
                        vol = subprocess.run(["ffmpeg", "-i", out_path, "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
                        maxvol = [l for l in vol.stderr.splitlines() if "max_volume" in l]
                        print(json.dumps({"ok": True, "prompt_id": pid, "gen_seconds": gen_s,
                            "prompt_text": prompt_text,
                            "mode": "i2v" if a.image else "t2v", "seed": a.seed,
                            "file": out_path, "bytes": len(data),
                            "streams": json.loads(probe.stdout).get("streams", []),
                            "max_volume": maxvol[0].split(":")[-1].strip() if maxvol else None}))
                        return
        fail("completed but no mp4 in outputs", prompt_id=pid)
    fail(f"timeout after {a.timeout}s — job may still be running. "
         f"Cancel: curl -X POST {SERVER}/interrupt (if running) or "
         f"curl -X POST {SERVER}/queue -d '{{\"delete\":[\"{pid}\"]}}' (if pending).",
         prompt_id=pid)

if __name__ == "__main__":
    main()
