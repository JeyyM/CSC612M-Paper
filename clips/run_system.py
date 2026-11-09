"""
Picks a Compression Rate Factor (CRF) for each clip.
Uses Video Multi-Method Assessment Fusion (VMAF) to score the frames and picks the 
CRF that maximizes the VMAF score.
"""

import csv
import json
import random
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPS = Path(__file__).resolve().parent
WORK = CLIPS / "work"

FFMPEG = Path(
    r"C:\Users\asus\AppData\Local\Programs\Python\Python313\Lib\site-packages"
    r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
)
CUT_FINDER = ROOT / "src" / "build" / "list_cuts.exe"

SEED = 20261010
WIDTH, HEIGHT = 1920, 1080
TOTAL = 900  # frames per clip
NPIX = WIDTH * HEIGHT
TARGET = 90.0  # the VMAF score we want to stay above

# Eight frames on a regular interval across the 900. A 20-frame page fits in the gaps.
REGULAR_INTERVAL = (56, 168, 281, 393, 506, 618, 731, 843)
# The first CRF strengths to encode. A higher number is a smaller, rougher file.
CRF_STRENGTHS = (28, 32, 36, 40, 44)
RULES = ("spread", "settled", "cut", "random", "detail")


def run(args, cwd=None):
    """Runs ffmpeg quietly. check=True so it crashes if ffmpeg fails."""
    subprocess.run(
        [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        cwd=cwd,
    )


def compile_cut_finder():
    """Runs list_cuts.exe. The same three kernels as the clock and the cut list."""
    source = ROOT / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from publish_cuts import compile_cut_finder as build

    build()


def random_frames():
    """8 random frames that every clip shares. Saved to a file so it's the same every run.

    The first 18 draws are thrown away on purpose, so the page start numbers
    from build_clips.py don't change.
    """
    path = CLIPS / "random_frames.txt"
    if path.exists():
        return tuple(int(x) for x in path.read_text(encoding="utf-8").split())

    rng = random.Random(SEED)
    for _ in range(18):
        rng.randint(180, 700)

    chosen = []
    while len(chosen) < 8:
        frame = rng.randrange(TOTAL)
        if frame not in chosen:
            chosen.append(frame)

    path.write_text(" ".join(str(f) for f in chosen) + "\n", encoding="utf-8")
    return tuple(chosen)


def crossings_of(clip):
    """Decodes the clip to grayscale and pipes it into list_cuts.exe.

    The cut frames are the first frame of each run above 2. quiet_share is
    the fraction of pairs at or below that line, stored as easy_below_2.
    """
    ff = subprocess.Popen(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error",
         "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE,
    )
    finder = subprocess.Popen(
        [str(CUT_FINDER), str(WIDTH), str(HEIGHT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def pump():
        # copy ffmpeg output into list_cuts.exe, 1MB at a time
        try:
            while True:
                chunk = ff.stdout.read(1 << 20)
                if not chunk:
                    break
                finder.stdin.write(chunk)
        finally:
            finder.stdin.close()
            ff.stdout.close()

    out = []
    err = []
    # had to use threads here, otherwise the pipes fill up and everything freezes
    threads = [
        threading.Thread(target=pump),
        threading.Thread(target=lambda: out.append(finder.stdout.read())),
        threading.Thread(target=lambda: err.append(finder.stderr.read())),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    finder.wait()
    ff.wait()

    err_text = b"".join(err).decode("utf-8", "replace").strip()
    if finder.returncode not in (0, None):
        raise SystemExit(err_text or f"list_cuts exited {finder.returncode}")

    pairs = None
    mismatches = None
    quiet = None
    cuts_by_kernel = {}
    for line in b"".join(out).decode("utf-8").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "pairs":
            pairs = int(parts[1])
            continue
        if parts[0] == "mismatches":
            mismatches = int(parts[1])
            continue
        if parts[0] == "quiet":
            quiet = int(parts[1])
            continue
        cuts_by_kernel[parts[0]] = [int(frame) for frame in parts[1:]]

    names = ("brightness_ordinary_c", "brightness_compiler_c", "brightness_avx2")
    lists = [cuts_by_kernel[name] for name in names]
    if lists[0] != lists[1] or lists[0] != lists[2]:
        raise SystemExit("cut lists disagree")
    if not pairs or quiet is None:
        raise SystemExit("list_cuts did not report any pairs")

    return lists[2], quiet / pairs, f"pairs {pairs} mismatches {mismatches}"


def frames_from(crossings, offset):
    """Takes up to 8 frames starting at (cut + offset), using the first cut that has room."""
    chosen = []
    for i, crossing in enumerate(crossings):
        if i + 1 < len(crossings):
            next_cut = crossings[i + 1]
        else:
            next_cut = TOTAL
        frame = crossing + offset
        if frame >= next_cut:
            continue
        while frame < next_cut and len(chosen) < 8:
            chosen.append(frame)
            frame += 1
        if len(chosen) == 8:
            break
    return tuple(chosen)


def settled_frames(crossings):
    """8 frames starting 8 frames after the cut (so the page has settled down)."""
    return frames_from(crossings, 8)


def cut_frames(crossings):
    """8 frames starting right on the cut."""
    return frames_from(crossings, 0)


def gray_frame(clip, number):
    """Grabs one frame as grayscale bytes. Only used for the detail check."""
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error",
         "-i", str(clip),
         "-vf", f"select=eq(n\\,{number})",
         "-vframes", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True,
        capture_output=True,
    )
    if len(proc.stdout) != NPIX:
        raise SystemExit(f"{clip.name} frame {number} gray is {len(proc.stdout)} bytes")
    return proc.stdout


def variance(raw):
    """How spread out the gray values are. A page of text scores higher than a plain slide."""
    mean = sum(raw) / NPIX
    mean_sq = sum(v * v for v in raw) / NPIX
    return mean_sq - mean * mean


def detail_frames(clip, crossings):
    """8 frames from whichever is busier: the opening screen or the page."""
    if not crossings:
        return tuple(range(8))
    opening = variance(gray_frame(clip, 0))
    page = variance(gray_frame(clip, crossings[0]))
    if page > opening:
        return tuple(range(crossings[0], crossings[0] + 8))
    return tuple(range(8))


def load_vmaf(path):
    """Reads the VMAF json and returns the 900 scores in frame order."""
    data = json.loads(path.read_text(encoding="utf-8"))
    scores = {}
    for row in data["frames"]:
        scores[int(row["frameNum"])] = float(row["metrics"]["vmaf"])

    missing = [f for f in range(TOTAL) if f not in scores]
    if missing:
        raise SystemExit(f"{path} is missing frames {missing[:5]}")
    return [scores[f] for f in range(TOTAL)]


def nth_worst(scores, n):
    """Returns (frame, score) for the nth worst frame. n=1 is the worst one."""
    ranked = sorted(enumerate(scores), key=lambda item: item[1])
    return ranked[n - 1]


def encode_file(reference, dest, crf):
    """Encodes the whole clip at one CRF with scene-cut off. Skips if the file is already there."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([
        "-i", str(reference),
        "-an",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-g", "999",
        "-keyint_min", "999",
        "-sc_threshold", "0",
        str(dest),
    ])


def score_file(reference, distorted, log_path):
    """Gets VMAF for every frame (compressed vs lossless). Reuses the json if it exists."""
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "-i", str(distorted),
                "-i", str(reference),
                "-lavfi",
                "[0:v]format=yuv420p,setpts=PTS-STARTPTS[dist];"
                "[1:v]format=yuv420p,setpts=PTS-STARTPTS[ref];"
                f"[dist][ref]libvmaf=log_fmt=json:log_path={log_path.name}:n_subsample=1:n_threads=8",
                "-f", "null", "-",
            ],
            cwd=log_path.parent,
        )
    return load_vmaf(log_path)


def measure(reference, encode_dir, score_dir, crfs, rows, votes):
    """Encodes + scores every CRF in crfs that we don't have yet, and adds a row for it."""
    done = {row["crf"] for row in rows}
    for crf in crfs:
        if crf in done:
            continue
        print(f"  crf {crf}", flush=True)
        distorted = encode_dir / f"crf{crf}.mp4"
        encode_file(reference, distorted, crf)
        scores = score_file(reference, distorted, score_dir / f"crf{crf}.json")

        ninth_frame, ninth = nth_worst(scores, 9)
        forty_frame, forty = nth_worst(scores, 45)
        row = {
            "crf": crf,
            "bytes": distorted.stat().st_size,
            "ninth": ninth,
            "ninth_frame": ninth_frame,
            "fortyfifth": forty,
            "fortyfifth_frame": forty_frame,
        }
        # each rule's score is the worst of its 8 frames
        for name, frames in votes.items():
            row[name] = min(scores[f] for f in frames) if frames else None
        rows.append(row)
        done.add(crf)

    rows.sort(key=lambda row: row["crf"])
    return rows


def passing(rows, key):
    """Rows where this rule's score is still at least 90."""
    return [row for row in rows if row[key] is not None and row[key] >= TARGET]


def missing_steps(rows, key):
    """The CRFs we skipped between the last one that passes and the next one that fails.

    e.g. if 32 passes and 36 fails, this gives [33, 34, 35].
    """
    good = passing(rows, key)
    if not good:
        return []
    best = max(row["crf"] for row in good)
    higher = [row["crf"] for row in rows if row["crf"] > best]
    if not higher:
        return []
    return list(range(best + 1, min(higher)))


def choose(rows, key):
    """The highest CRF where the rule's 8 frames still score at least 90 (or None)."""
    good = passing(rows, key)
    if not good:
        return None
    return max(good, key=lambda row: row["crf"])


def bracket(rows):
    """Adds CRFs 2 at a time until the 9th worst score has one rung above 90 and one below.

    Stops at 18 on the low end and 42 on the high end.
    """
    extra = []
    if not rows:
        return extra
    if min(row["ninth"] for row in rows) >= TARGET and rows[-1]["crf"] < 42:
        extra.append(min(42, rows[-1]["crf"] + 2))
    if max(row["ninth"] for row in rows) < TARGET and rows[0]["crf"] > 18:
        extra.append(max(18, rows[0]["crf"] - 2))
    return extra


def clip_ids():
    """The clips that were actually built, in the same order as log.csv."""
    with (CLIPS / "log.csv").open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row["status"] == "built"]


def votes_for(reference, drawn):
    """Makes the 5 sets of 8 frames, one per rule. (We don't use the page start from the log.)"""
    crossings, quiet_share, kernel = crossings_of(reference)
    votes = {
        "spread": REGULAR_INTERVAL,
        "settled": settled_frames(crossings),
        "cut": cut_frames(crossings),
        "random": drawn,
        "detail": detail_frames(reference, crossings),
    }
    return crossings, quiet_share, kernel, votes


def fill_ladder(reference, encode_dir, score_dir, votes):
    """Encodes CRF_STRENGTHS first, then fills in the strengths between a pass and a miss."""
    rows = measure(reference, encode_dir, score_dir, CRF_STRENGTHS, [], votes)
    while True:
        extra = bracket(rows)
        extra += missing_steps(rows, "spread")
        extra += missing_steps(rows, "settled")
        have = {row["crf"] for row in rows}
        extra = sorted(set(crf for crf in extra if crf not in have))
        if not extra:
            return rows
        print("  fill", extra, flush=True)
        rows = measure(reference, encode_dir, score_dir, extra, rows, votes)


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def decision_row(meta, crossings, quiet_share, votes, rows):
    """One row for decisions.csv: what CRF each rule picked and how the file scored there."""
    record = {
        "clip": meta["clip"],
        "group": meta["group"],
        "cuts": " ".join(str(f) for f in crossings),
        "easy_below_2": f"{quiet_share:.4f}",
        "settled_frames": " ".join(str(f) for f in votes["settled"]),
        "detail_frames": " ".join(str(f) for f in votes["detail"]),
    }
    for key in RULES:
        picked = choose(rows, key)
        if picked is None:
            # nothing passed, leave the columns blank
            for col in ("crf", "ninth", "shortfall", "shortfall80", "bytes", "ninth_frame"):
                record[f"{key}_{col}"] = ""
            print(f"  {key}: no rung at {TARGET:.0f}", flush=True)
            continue

        shortfall = max(0.0, TARGET - picked["ninth"])
        record[f"{key}_crf"] = picked["crf"]
        record[f"{key}_ninth"] = f"{picked['ninth']:.4f}"
        record[f"{key}_shortfall"] = f"{shortfall:.4f}"
        record[f"{key}_shortfall80"] = f"{max(0.0, 80.0 - picked['ninth']):.4f}"
        record[f"{key}_bytes"] = picked["bytes"]
        record[f"{key}_ninth_frame"] = picked["ninth_frame"]
        print(
            f"  {key}: crf {picked['crf']} ninth {picked['ninth']:.2f} "
            f"shortfall {shortfall:.2f} bytes {picked['bytes']}",
            flush=True,
        )
    return record


def main():
    compile_cut_finder()
    drawn = random_frames()
    print("random frames", drawn, flush=True)

    decisions_path = CLIPS / "decisions.csv"
    decisions = []

    for meta in clip_ids():
        name = meta["clip"]
        reference = CLIPS / f"{name}.mkv"
        encode_dir = WORK / name / "encodes"
        score_dir = WORK / name / "scores"
        print("clip", name, flush=True)

        crossings, quiet_share, kernel, votes = votes_for(reference, drawn)
        print(" ", kernel, "cuts", crossings, f"easy_below_2={quiet_share:.3f}", flush=True)

        rows = fill_ladder(reference, encode_dir, score_dir, votes)
        write_csv(score_dir / "summary.csv", rows)

        decisions.append(decision_row(meta, crossings, quiet_share, votes, rows))
        write_csv(decisions_path, decisions)  # save after every clip in case it crashes
        print("clip", name, "done", flush=True)


if __name__ == "__main__":
    main()
