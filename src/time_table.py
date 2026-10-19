"""Build the three brightness kernels and time each sum.

The kernel clock starts after the frames are in memory. The three medians
are written to the timing table.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "clips"
# One clip is enough for the clock. Every clip is the same size, so the kernel
# does the same work on each. This file is the agenda slide and the article.
TIMING_CLIP = CLIPS / "01.mkv"
FFMPEG = Path(
    r"C:\Users\asus\AppData\Local\Programs\Python\Python313\Lib\site-packages"
    r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
)
GCC = Path(r"C:\winlibs\mingw64\bin\gcc.exe")
NASM = Path(r"C:\winlibs\mingw64\bin\nasm.exe")
SRC = ROOT / "src"
BUILD = Path(__file__).resolve().parent / "build"
WIDTH, HEIGHT, FRAME_COUNT = 1920, 1080, 900


# Run one GCC command and stop if the compiler fails.
def compile_with_gcc(arguments):
    subprocess.run([str(GCC), *arguments], check=True)


# Compile the C loop twice and assemble the handwritten loop, then link the clock.
def compile_clock():
    BUILD.mkdir(parents=True, exist_ok=True)

    # Ordinary C. The vectorizer is off, so the loop stays one pixel at a time.
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
    subprocess.run(
        [
            str(NASM), "-f", "win64",
            "-o", str(BUILD / "brightness_avx2.o"),
            str(SRC / "brightness_avx2.asm"),
        ],
        check=True,
    )

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


# Turn the clip into gray frames and time the three sums.
# Reading the clip is outside the kernel clock.
def run_clock():
    ffmpeg = subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(TIMING_CLIP),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    result = subprocess.run(
        [str(BUILD / "time_kernel.exe"), str(WIDTH), str(HEIGHT), str(FRAME_COUNT)],
        stdin=ffmpeg.stdout,
        capture_output=True,
        text=True,
    )
    ffmpeg.stdout.close()
    ffmpeg.wait()
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    print(result.stdout, end="")
    return result.stdout


# Turn the clock's printed lines into one row per kernel.
# A kernel line is: name, the word median, the median, the word runs, then five times.
def read_clock_report(text):
    rows = []
    check_line = None
    for line in text.splitlines():
        parts = line.split()
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


# Write the medians, the five runs, and the speedups.
# Ordinary C has no speedup against the compiler.
def write_timing_table(rows, ordinary_median, compiler_median):
    timing_file = CLIPS / "timing.csv"
    medians = {row["method"]: float(row["median_s"]) for row in rows}
    with timing_file.open("w", encoding="utf-8", newline="") as handle:
        handle.write("method,median_s,runs_s,speedup_vs_ordinary,speedup_vs_compiler\n")
        for row in rows:
            name = row["method"]
            median = medians[name]
            speedup_vs_ordinary = f"{ordinary_median / median:.4f}"
            speedup_vs_compiler = ""
            if name != "brightness_ordinary_c":
                speedup_vs_compiler = f"{compiler_median / median:.4f}"
            handle.write(
                f"{name},{row['median_s']},{row['runs_s']},{speedup_vs_ordinary},{speedup_vs_compiler}\n"
            )
    return timing_file


# Write the note that says what each row of the timing table is.
def write_timing_note(check_line, ordinary_median, compiler_median, assembly_median):
    note = CLIPS / "timing.txt"
    note.write_text(
        "\n".join(
            [
                f"Clip: {TIMING_CLIP.relative_to(ROOT).as_posix()}, agenda slide and article, 1920x1080, 900 frames, 899 pairs.",
                "brightness_ordinary_c: src/brightness_c.c, compiled -O2 with the vectorizer off.",
                "brightness_compiler_c: the same source, compiled -O2 -mavx2 with the vectorizer on. GCC widened the bytes. It did not emit the SAD instruction.",
                "brightness_avx2: src/brightness_avx2.asm, assembled with nasm -f win64, 32 bytes per vpsadbw. The clock covers the sum only, after frames are in memory.",
                "Five passes each. The table keeps the median, and the runs are sorted.",
                f"Speedup, compiler against plain: {ordinary_median / compiler_median:.2f}x.",
                f"Speedup, AVX2 against compiler: {compiler_median / assembly_median:.2f}x.",
                "Encoding time is not in this table.",
                f"Kernel check: {' '.join(check_line)}.",
                "Cut lists, one per kernel: clips/cuts.txt.",
                "",
            ]
        ),
        encoding="utf-8",
    )


# Build the clock, time the kernels, and save the table.
def main():
    compile_clock()
    report = run_clock()
    check_line, rows = read_clock_report(report)

    medians = {row["method"]: float(row["median_s"]) for row in rows}
    ordinary_median = medians["brightness_ordinary_c"]
    compiler_median = medians["brightness_compiler_c"]
    assembly_median = medians["brightness_avx2"]

    timing_file = write_timing_table(rows, ordinary_median, compiler_median)
    write_timing_note(check_line, ordinary_median, compiler_median, assembly_median)
    print(f"wrote {timing_file}")


if __name__ == "__main__":
    main()
