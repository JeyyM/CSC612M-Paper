"""Build the 18 lossless practice clips.

Each clip holds one easy screen for 30 seconds. A dense page covers it
for 20 frames, about two-thirds of a second. The page start is drawn
once from a fixed seed. A clip that is already built is left alone.
"""

import csv
import random
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
IMAGES = ROOT / "images"
FFMPEG = Path(
    r"C:\Users\asus\AppData\Local\Programs\Python\Python313\Lib\site-packages"
    r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
)

SEED = 20261010
WIDTH, HEIGHT = 1920, 1080
FPS = 30
TOTAL = 900
HOLD = 20
FRAME_BYTES = WIDTH * HEIGHT * 3

LOG_FIELDS = [
    "clip",
    "group",
    "easy",
    "dense",
    "start",
    "hold",
    "frames",
    "bytes",
    "status",
]

# Plain screens. Each one is used twice in the comparison set.
SIMPLE = [
    IMAGES / "simple" / "agenda slide.png",
    IMAGES / "simple" / "empty dashboard.png",
    IMAGES / "simple" / "empty-editor.png",
    IMAGES / "simple" / "section divider.png",
    IMAGES / "simple" / "grid-desktop.png",
    IMAGES / "simple" / "editor-desktop.png",
]

# Busier screens. These six stay out of the comparison mean.
MEDIUM = [
    IMAGES / "medium" / "analytics.png",
    IMAGES / "medium" / "blank-writer.png",
    IMAGES / "medium" / "board.png",
    IMAGES / "medium" / "control-panel.png",
    IMAGES / "medium" / "file-manager.png",
    IMAGES / "medium" / "appearance.png",
]

# The short page someone has to read.
DENSE = [
    IMAGES / "dense" / "article.png",
    IMAGES / "dense" / "code-editor.png",
    IMAGES / "dense" / "rfc-body.png",
    IMAGES / "dense" / "spec-columns.png",
    IMAGES / "dense" / "spreadsheet.png",
    IMAGES / "dense" / "terminal-log.png",
]


def pairs():
    """Clips 01–06 match in order. 07–12 repeat the screens with the next page. 13–18 are the boundary."""
    matched = []
    shifted = []
    boundary = []
    for index in range(6):
        matched.append(("comparison", SIMPLE[index], DENSE[index]))
        next_page = DENSE[(index + 1) % 6]
        shifted.append(("comparison", SIMPLE[index], next_page))
        boundary_page = DENSE[(index + 2) % 6]
        boundary.append(("boundary", MEDIUM[index], boundary_page))
    return matched + shifted + boundary


def starts():
    """Eighteen page starts, frames 180 through 700. The seed makes the draw repeatable."""
    rng = random.Random(SEED)
    return [rng.randint(180, 700) for _ in range(18)]


def run(args):
    """Start FFmpeg. Stop the script if FFmpeg fails."""
    subprocess.run(
        [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
    )


def picture_filter(path):
    """FFmpeg filter that makes one still a 1920x1080 RGB frame."""
    with Image.open(path) as image:
        size = image.size
    if size == (WIDTH, HEIGHT):
        return "format=rgb24,setsar=1"
    return f"scale={WIDTH}:{HEIGHT}:flags=lanczos,format=rgb24,setsar=1"


def encode(easy, dense, start, dest):
    """Write one lossless clip. The dense page covers the easy picture for 20 frames."""
    last = start + HOLD - 1
    graph = (
        f"[0:v]{picture_filter(easy)}[easy];"
        f"[1:v]{picture_filter(dense)}[page];"
        f"[easy][page]overlay=format=rgb:enable='between(n,{start},{last})',format=rgb24"
    )
    inputs = [
        "-loop", "1", "-framerate", str(FPS), "-i", str(easy),
        "-loop", "1", "-framerate", str(FPS), "-i", str(dense),
    ]
    length = [
        "-filter_complex", graph,
        "-frames:v", str(TOTAL),
        "-fps_mode", "cfr",
        "-an",
    ]
    # QP 0 is lossless. One group of pictures, and no keyframe inserted at the page.
    lossless = [
        "-c:v", "libx264rgb",
        "-qp", "0",
        "-preset", "medium",
        "-pix_fmt", "rgb24",
        "-x264-params", "keyint=900:min-keyint=900:scenecut=0",
    ]
    run([*inputs, *length, *lossless, str(dest)])


def raw_still(path):
    """Pixels of an original picture, sized the same way the clip stored it."""
    proc = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-vf", picture_filter(path),
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True,
        capture_output=True,
    )
    if len(proc.stdout) != FRAME_BYTES:
        raise SystemExit(f"{path.name} scaled to {len(proc.stdout)} bytes")
    return proc.stdout


def raw_frame(clip, number):
    """Pixels of one frame from a finished clip."""
    proc = subprocess.run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(clip),
            "-vf", f"select=eq(n\\,{number})",
            "-vframes", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True,
        capture_output=True,
    )
    if len(proc.stdout) != FRAME_BYTES:
        raise SystemExit(f"{clip.name} frame {number} is {len(proc.stdout)} bytes")
    return proc.stdout


def frame_count(clip):
    """How many frames FFmpeg finds in the clip."""
    proc = subprocess.run(
        [str(FFMPEG), "-i", str(clip), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    count = None
    for line in proc.stderr.splitlines():
        if "frame=" in line:
            token = line.split("frame=")[-1].split()[0]
            if token.isdigit():
                count = int(token)
    return count


def write_log(log_path, plan):
    """Replace clips/log.csv with the current plan."""
    with log_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(plan)


def read_log(log_path):
    if not log_path.exists():
        return {}
    with log_path.open(encoding="utf-8", newline="") as handle:
        return {row["clip"]: row for row in csv.DictReader(handle)}


def plan_clips(drawn):
    """One pending row per picture pair, numbered 01 through 18."""
    plan = []
    for number, (group, easy, dense) in enumerate(pairs(), start=1):
        missing = easy is None or dense is None
        plan.append(
            {
                "clip": f"{number:02d}",
                "group": group,
                "easy": "" if easy is None else str(easy.relative_to(ROOT)),
                "dense": "" if dense is None else str(dense.relative_to(ROOT)),
                "start": drawn[number - 1],
                "hold": HOLD,
                "status": "waiting on still" if missing else "pending",
            }
        )
    return plan


def restore_finished(plan, previous):
    """A clip already on disk, and marked built, keeps its recorded row."""
    for row, (group, easy, dense) in zip(plan, pairs()):
        if easy is None or dense is None:
            continue
        video = OUT / f"{row['clip']}.mkv"
        old = previous.get(row["clip"])
        if video.exists() and old and old.get("status") == "built":
            for key in LOG_FIELDS:
                if old.get(key):
                    row[key] = old[key]


def check_clip(easy, dense, video, start):
    """Confirm 900 frames, and that frame 0 and the page frame match the stills."""
    total = frame_count(video)
    opening = raw_frame(video, 0)
    page = raw_frame(video, start)
    passed = total == TOTAL and opening == raw_still(easy) and page == raw_still(dense)
    return {
        "frames": total,
        "bytes": video.stat().st_size,
        "status": "built" if passed else "check failed",
    }


def main():
    OUT.mkdir(exist_ok=True)
    log_path = OUT / "log.csv"

    plan = plan_clips(starts())
    restore_finished(plan, read_log(log_path))
    write_log(log_path, plan)
    print("starts written", [row["start"] for row in plan])

    for row, (group, easy, dense) in zip(plan, pairs()):
        if row["status"] == "built":
            print("keep", row["clip"])
            continue
        if easy is None or dense is None:
            print("skip", row["clip"])
            continue

        video = OUT / f"{row['clip']}.mkv"
        print("encode", row["clip"], "start", row["start"])
        encode(easy, dense, row["start"], video)
        row.update(check_clip(easy, dense, video, row["start"]))
        print(row["clip"], row["status"], row["frames"], row["bytes"])
        write_log(log_path, plan)


if __name__ == "__main__":
    main()
