"""Second encode of the 18 clips with x264's ordinary scene-cut and GOP.

The scene-cut-off files in encodes/ and decisions.csv are left as they are.
This pass omits -g, -keyint_min, and -sc_threshold, so FFmpeg's libx264
defaults apply. Votes use the frame lists already stored for each clip.
"""

import csv
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import run_system as rs

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
DECISIONS = ROOT / "decisions_scenecut.csv"
ENCODER_NOTE = WORK / "scenecut_on_encoder.txt"
WORKERS = 2


def encode_default(reference, dest, crf):
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".partial.mp4")
    if partial.exists():
        partial.unlink()
    rs.run(
        [
            "-i", str(reference), "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            str(partial),
        ]
    )
    partial.replace(dest)


rs.encode_file = encode_default


def frames_of(text):
    return tuple(int(part) for part in text.split()) if text.strip() else ()


def load_rows(score_dir):
    summary = score_dir / "summary.csv"
    if not summary.exists():
        return []
    rows = []
    with summary.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {
                "crf": int(raw["crf"]),
                "bytes": int(raw["bytes"]),
                "ninth": float(raw["ninth"]),
                "ninth_frame": int(raw["ninth_frame"]),
                "fortyfifth": float(raw["fortyfifth"]),
                "fortyfifth_frame": int(raw["fortyfifth_frame"]),
            }
            for key in ("spread", "settled", "cut", "random", "detail"):
                row[key] = float(raw[key]) if raw[key] not in ("", None) else None
            rows.append(row)
    rows.sort(key=lambda row: row["crf"])
    return rows


def confirm_defaults():
    if ENCODER_NOTE.exists():
        return
    reference = ROOT / "01.mkv"
    dest = WORK / "_probe" / "crf36.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            str(rs.FFMPEG), "-y", "-hide_banner", "-i", str(reference), "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "36",
            "-pix_fmt", "yuv420p",
            "-frames:v", "30",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    lines = [
        line.strip()
        for line in (proc.stderr or "").splitlines()
        if "scenecut" in line or "keyint" in line or "x264 [info]" in line
    ]
    ENCODER_NOTE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("encoder defaults:\n" + "\n".join(lines), flush=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])


def one_clip(meta, drawn):
    name = meta["clip"]
    reference = ROOT / f"{name}.mkv"
    encode_dir = WORK / name / "encodes_b"
    score_dir = WORK / name / "scores_b"
    crossings = frames_of(meta["cuts"])
    settled = frames_of(meta["settled_frames"])
    detail = frames_of(meta["detail_frames"])
    votes = {
        "spread": rs.REGULAR_INTERVAL,
        "settled": settled,
        "cut": rs.cut_frames(crossings),
        "random": drawn,
        "detail": detail,
    }
    rows = load_rows(score_dir)
    print(f"clip {name} resume {len(rows)} rungs", flush=True)
    rows = rs.measure(reference, encode_dir, score_dir, rs.CRF_STRENGTHS, rows, votes)
    while True:
        extra = rs.bracket(rows)
        extra += rs.missing_steps(rows, "spread")
        extra += rs.missing_steps(rows, "settled")
        extra = sorted({crf for crf in extra if crf not in {row["crf"] for row in rows}})
        if not extra:
            break
        print(f"  {name} fill {extra}", flush=True)
        rows = rs.measure(reference, encode_dir, score_dir, extra, rows, votes)
    summary = score_dir / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    record = {
        "clip": name,
        "group": meta["group"],
        "cuts": meta["cuts"],
        "easy_below_2": meta["easy_below_2"],
        "settled_frames": meta["settled_frames"],
        "detail_frames": meta["detail_frames"],
    }
    for key in ("spread", "settled", "cut", "random", "detail"):
        picked = rs.choose(rows, key)
        if picked is None:
            record[f"{key}_crf"] = ""
            record[f"{key}_ninth"] = ""
            record[f"{key}_shortfall"] = ""
            record[f"{key}_shortfall80"] = ""
            record[f"{key}_bytes"] = ""
            record[f"{key}_ninth_frame"] = ""
            print(f"  {name} {key}: no rung", flush=True)
            continue
        record[f"{key}_crf"] = picked["crf"]
        record[f"{key}_ninth"] = f"{picked['ninth']:.4f}"
        record[f"{key}_shortfall"] = f"{max(0.0, rs.TARGET - picked['ninth']):.4f}"
        record[f"{key}_shortfall80"] = f"{max(0.0, 80.0 - picked['ninth']):.4f}"
        record[f"{key}_bytes"] = picked["bytes"]
        record[f"{key}_ninth_frame"] = picked["ninth_frame"]
        print(
            f"  {name} {key}: crf {picked['crf']} ninth {picked['ninth']:.2f} "
            f"shortfall {max(0.0, rs.TARGET - picked['ninth']):.2f} bytes {picked['bytes']}",
            flush=True,
        )
    return record


def write_decisions(rows):
    rows = sorted(rows, key=lambda row: int(row["clip"]))
    with DECISIONS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    confirm_defaults()
    drawn = rs.random_frames()
    with (ROOT / "decisions.csv").open(encoding="utf-8", newline="") as handle:
        metas = list(csv.DictReader(handle))
    done = {}
    if DECISIONS.exists():
        with DECISIONS.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                done[row["clip"]] = row
    pending = [meta for meta in metas if meta["clip"] not in done]
    print(f"{len(done)} done, {len(pending)} to run", flush=True)
    lock = threading.Lock()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(one_clip, meta, drawn): meta["clip"] for meta in pending}
        for future in as_completed(futures):
            name = futures[future]
            record = future.result()
            with lock:
                done[name] = record
                write_decisions(list(done.values()))
            print(f"clip {name} done, {len(done)}/18, {time.perf_counter() - started:.0f}s", flush=True)
    print(f"wrote {DECISIONS}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
