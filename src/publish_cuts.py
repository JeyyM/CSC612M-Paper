"""
Runs the 3 kernels on every clip and writes the cut lists to a file.
Inputs gray frames from FFMPEG. Each block in the file names its clip.
"""

import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent
FFMPEG = Path(
    r"C:\Users\asus\AppData\Local\Programs\Python\Python313\Lib\site-packages"
    r"\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
)
GCC = Path(r"C:\winlibs\mingw64\bin\gcc.exe")
NASM = Path(r"C:\winlibs\mingw64\bin\nasm.exe")
BUILD = SRC / "build"
CUT_FINDER = BUILD / "list_cuts.exe"
CLIPS = ROOT / "clips"
CHUNK = 1 << 20


def compile_cut_finder():
    """Build list_cuts.exe from the C loop, compiled twice, and the assembly."""
    BUILD.mkdir(parents=True, exist_ok=True)

    # Ordinary C. The vectorizer is off, so the loop stays one pixel at a time.
    subprocess.run(
        [
            str(GCC), "-O2", "-fno-tree-vectorize", "-fno-tree-slp-vectorize",
            "-DBRIGHTNESS_FUNCTION=brightness_ordinary_c", "-c", "-o", str(BUILD / "brightness_ordinary_c.o"),
            str(ROOT / "src" / "brightness_c.c"),
        ],
        check=True,
    )

    # C with auto-vectorization on. -fno-tree-vectorize and -fno-tree-slp-vectorize are left off.
    # The vectorizer rewrites the one-pixel loop so one instruction handles many pixels.
    subprocess.run(
        [
            str(GCC), "-O2", "-mavx2", "-ftree-vectorize",
            "-DBRIGHTNESS_FUNCTION=brightness_compiler_c", "-c", "-o", str(BUILD / "brightness_compiler_c.o"),
            str(ROOT / "src" / "brightness_c.c"),
        ],
        check=True,
    )

    # AVX2 assembly loop: that does 32 pixels per instruction
    subprocess.run(
        [
            str(NASM), "-f", "win64",
            "-o", str(BUILD / "brightness_avx2.o"),
            str(ROOT / "src" / "brightness_avx2.asm"),
        ],
        check=True,
    )

    subprocess.run(
        [
            str(GCC), "-O2", "-mavx2", "-o", str(CUT_FINDER),
            str(SRC / "list_cuts.c"),
            str(BUILD / "brightness_ordinary_c.o"),
            str(BUILD / "brightness_compiler_c.o"),
            str(BUILD / "brightness_avx2.o"),
        ],
        check=True,
    )


def pass_frames(ffmpeg, cut_finder):
    """Pass the frames from FFMPEG to list_cuts"""
    try:
        while True:
            chunk = ffmpeg.stdout.read(CHUNK)
            if not chunk:
                break
            cut_finder.stdin.write(chunk)
    finally:
        cut_finder.stdin.close()
        ffmpeg.stdout.close()


def read_pipe(pipe, pieces):
    """Read one finished pipe. The pieces list is shared with the main thread."""
    pieces.append(pipe.read())


def clip_paths():
    """The eighteen lossless clips, in order."""
    paths = sorted(CLIPS.glob("[0-9][0-9].mkv"))
    if len(paths) != 18:
        raise SystemExit(f"expected 18 clips, found {len(paths)}")
    return paths


def cuts_for(clip):
    """Turn one clip into gray frames and return the three cut lists."""
    ffmpeg = subprocess.Popen(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(clip),
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        stdout=subprocess.PIPE,
    )
    cut_finder = subprocess.Popen(
        [str(CUT_FINDER), "1920", "1080"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # All three pipes have to be serviced together, or one of them fills and stalls.
    printed = []
    errors = []
    threads = [
        threading.Thread(target=pass_frames, args=(ffmpeg, cut_finder)),
        threading.Thread(target=read_pipe, args=(cut_finder.stdout, printed)),
        threading.Thread(target=read_pipe, args=(cut_finder.stderr, errors)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    cut_finder.wait()
    ffmpeg.wait()

    text = b"".join(printed).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
    if cut_finder.returncode != 0:
        message = b"".join(errors).decode("utf-8", "replace")
        raise SystemExit(message or f"list_cuts exited {cut_finder.returncode} on {clip.name}")
    shown = [line for line in text.splitlines() if not line.startswith("quiet ")]
    return "clip " + clip.stem + "\n" + "\n".join(shown) + "\n"


def main():
    compile_cut_finder()
    blocks = []
    for clip in clip_paths():
        block = cuts_for(clip)
        blocks.append(block)
        print(block, end="\n")

    cuts_file = CLIPS / "cuts.txt"
    cuts_file.write_text("\n".join(blocks), encoding="utf-8", newline="\n")
    print(f"wrote {cuts_file}")


if __name__ == "__main__":
    main()
