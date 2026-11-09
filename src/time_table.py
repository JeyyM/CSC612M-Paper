"""Build the three brightness kernels and time each sum on every clip.

The kernel clock starts after the frames are in memory. The timing table
gets one row per kernel per clip, and it is rewritten after each clip.
"""

import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "clips"
FFMPEG = Path(
    r"C:\Users\asus\AppData\Local\Programs\Python\Python313\Lib\site-packages"
    r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
)
GCC = Path(r"C:\winlibs\mingw64\bin\gcc.exe")
NASM = Path(r"C:\winlibs\mingw64\bin\nasm.exe")
SRC = ROOT / "src"
BUILD = Path(__file__).resolve().parent / "build"
WIDTH, HEIGHT, FRAME_COUNT = 1920, 1080, 900
TIMING_HEADER = "clip,method,median_s,runs_s,speedup_vs_ordinary,speedup_vs_compiler\n"


# Run one GCC command and stop if the compiler fails.
def compile_with_gcc(arguments):
    subprocess.run([str(GCC), *arguments], check=True)


# Compile the C loop twice and assemble the handwritten loop, then link the clock.
def compile_clock():
    BUILD.mkdir(parents=True, exist_ok=True)

    # Ordinary C. The vectorizer is off, so the loop stays one pixel at a time.
    print("compiling Ordinary C", flush=True)
    compile_with_gcc(
        [
            "-O2",
            "-fno-tree-vectorize",
            "-fno-tree-slp-vectorize",
            "-DBRIGHTNESS_FUNCTION=brightness_ordinary_c",
            "-S",
            "-o",
            str(BUILD / "brightness_ordinary_c.s"),
            str(SRC / "brightness_c.c"),
        ]
    )

    # C with auto-vectorization on. -fno-tree-vectorize and -fno-tree-slp-vectorize are left off.
    print("compiling Optimized C", flush=True)
    compile_with_gcc(
        [
            "-O2",
            "-mavx2",
            "-ftree-vectorize",
            "-DBRIGHTNESS_FUNCTION=brightness_compiler_c",
            "-S",
            "-o",
            str(BUILD / "brightness_compiler_c.s"),
            str(SRC / "brightness_c.c"),
        ]
    )

    # AVX2 assembly loop: that does 32 pixels per instruction
    print("assembling Handwritten AVX2", flush=True)
    subprocess.run(
        [
            str(NASM), "-f", "win64",
            "-o", str(BUILD / "brightness_avx2.o"),
            str(SRC / "brightness_avx2.asm"),
        ],
        check=True,
    )

    print("linking the clock", flush=True)
    compile_with_gcc(
        [
            "-O2",
            "-mavx2",
            "-o",
            str(BUILD / "time_kernel.exe"),
            str(SRC / "time_kernel.c"),
            str(BUILD / "brightness_ordinary_c.s"),
            str(BUILD / "brightness_compiler_c.s"),
            str(BUILD / "brightness_avx2.o"),
        ]
    )


def timing_clips():
    """The eighteen lossless clips, in clip order."""
    clips = sorted(CLIPS.glob("[0-9][0-9].mkv"))
    if len(clips) != 18:
        raise SystemExit(f"expected 18 clips, found {len(clips)}")
    return clips


def pass_frames(ffmpeg, clock):
    """Copy the gray frames into the clock. One pipe filling would stall the run."""
    try:
        while True:
            chunk = ffmpeg.stdout.read(1 << 20)
            if not chunk:
                break
            clock.stdin.write(chunk)
    finally:
        clock.stdin.close()
        ffmpeg.stdout.close()


def collect_report(pipe, pieces):
    """Keep the timing sheet. Progress is printed from the other pipe."""
    pieces.append(pipe.read())


def show_progress(pipe):
    """Print each progress line as soon as the clock writes it."""
    for line in iter(pipe.readline, b""):
        print(line.decode("utf-8").rstrip(), flush=True)


# Turn one clip into gray frames and time the three sums.
# Reading the clip is outside the kernel clock.
def run_clock(clip):
    print(f"reading {clip.name}", flush=True)
    ffmpeg = subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(clip),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    clock = subprocess.Popen(
        [str(BUILD / "time_kernel.exe"), str(WIDTH), str(HEIGHT), str(FRAME_COUNT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    report = []
    threads = [
        threading.Thread(target=pass_frames, args=(ffmpeg, clock)),
        threading.Thread(target=collect_report, args=(clock.stdout, report)),
        threading.Thread(target=show_progress, args=(clock.stderr,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    clock.wait()
    ffmpeg.wait()
    text = b"".join(report).decode("utf-8")
    if clock.returncode != 0:
        raise SystemExit(text or f"time_kernel exited {clock.returncode}")
    print(text, end="" if text.endswith("\n") else "\n")
    return text


# Turn the clock's printed lines into one row per kernel.
# A kernel line is: name, the word median, the median, the word runs, then five times.
def read_clock_report(text):
    rows = []
    check_line = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "pairs":
            check_line = parts
            continue
        rows.append(
            {
                "method": parts[0],
                "median_s": parts[2],
                "runs_s": " ".join(parts[4:]),
            }
        )
    return check_line, rows


# Attach this clip's speedups. Ordinary C is the baseline for the clip.
def rows_for_clip(stem, rows):
    medians = {row["method"]: float(row["median_s"]) for row in rows}
    ordinary_median = medians["brightness_ordinary_c"]
    compiler_median = medians["brightness_compiler_c"]
    attached = []
    for row in rows:
        name = row["method"]
        median = medians[name]
        speedup_vs_compiler = ""
        if name != "brightness_ordinary_c":
            speedup_vs_compiler = f"{compiler_median / median:.4f}"
        attached.append(
            {
                "clip": stem,
                "method": name,
                "median_s": row["median_s"],
                "runs_s": row["runs_s"],
                "speedup_vs_ordinary": f"{ordinary_median / median:.4f}",
                "speedup_vs_compiler": speedup_vs_compiler,
            }
        )
    return attached


# Write every finished clip. A later clip replaces the file with the longer table.
def write_timing_table(rows):
    timing_file = CLIPS / "timing.csv"
    with timing_file.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(TIMING_HEADER)
        for row in rows:
            handle.write(
                f"{row['clip']},{row['method']},{row['median_s']},{row['runs_s']},"
                f"{row['speedup_vs_ordinary']},{row['speedup_vs_compiler']}\n"
            )
    return timing_file


def mean(values):
    return sum(values) / len(values)


# Write the note that says what each row of the timing table is.
def write_timing_note(checks, rows):
    compiler = [float(row["speedup_vs_ordinary"]) for row in rows if row["method"] == "brightness_compiler_c"]
    avx_vs_compiler = [float(row["speedup_vs_compiler"]) for row in rows if row["method"] == "brightness_avx2"]
    avx_vs_ordinary = [float(row["speedup_vs_ordinary"]) for row in rows if row["method"] == "brightness_avx2"]
    lines = [
        "Clips 01 through 18. Each clip is 1920x1080, 900 frames, 899 pairs.",
        "brightness_ordinary_c: src/brightness_c.c, compiled -O2 with the vectorizer off.",
        "brightness_compiler_c: the same source, compiled -O2 -mavx2 with the vectorizer on. GCC widened the bytes. It did not emit the SAD instruction.",
        "brightness_avx2: src/brightness_avx2.asm, assembled with nasm -f win64, 32 bytes per vpsadbw. The clock covers the sum only, after frames are in memory.",
        "One row per kernel per clip. Five passes each. The table keeps the median, and the runs are sorted.",
        "Speedup is within that clip. Ordinary C is 1.0000.",
        f"Speedup, compiler against plain, mean of the 18 clips: {mean(compiler):.2f}x.",
        f"Speedup, AVX2 against compiler, mean of the 18 clips: {mean(avx_vs_compiler):.2f}x.",
        f"Speedup, AVX2 against plain, mean of the 18 clips: {mean(avx_vs_ordinary):.2f}x.",
        "Encoding time is not in this table.",
        "Kernel check, one line per clip:",
    ]
    for stem, check_line in checks:
        lines.append(f"{stem} {' '.join(check_line)}")
    lines.append("Cut lists, one per kernel: clips/cuts.txt.")
    lines.append("")
    note = CLIPS / "timing.txt"
    note.write_text("\n".join(lines), encoding="utf-8")
    return note


# Build the clock, time every clip, and save the table as each clip finishes.
def main():
    compile_clock()
    clips = timing_clips()
    finished = []
    checks = []
    timing_file = CLIPS / "timing.csv"
    for clip in clips:
        print(f"clip {clip.stem} of {len(clips)}", flush=True)
        report = run_clock(clip)
        check_line, rows = read_clock_report(report)
        checks.append((clip.stem, check_line))
        finished.extend(rows_for_clip(clip.stem, rows))
        timing_file = write_timing_table(finished)
        print(f"wrote {timing_file} through clip {clip.stem}", flush=True)
    note = write_timing_note(checks, finished)
    print(f"wrote {note}")


if __name__ == "__main__":
    main()
