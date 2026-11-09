/* Only times each of the 3 kernels, does not encode, choose compression, etc. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <io.h>
#include <fcntl.h>
#include <windows.h>

uint64_t brightness_ordinary_c(const uint8_t *first, const uint8_t *second, size_t pixel_count);
uint64_t brightness_compiler_c(const uint8_t *first, const uint8_t *second, size_t pixel_count);
uint64_t brightness_avx2(const uint8_t *first, const uint8_t *second, size_t pixel_count);

typedef uint64_t (*brightness_function)(const uint8_t *first, const uint8_t *second, size_t pixel_count);

static double seconds_between(LARGE_INTEGER start, LARGE_INTEGER end, LARGE_INTEGER ticks_per_second) {
    return (double)(end.QuadPart - start.QuadPart) / (double)ticks_per_second.QuadPart;
}

/* The work being done. Adds the brightness sum of every neighboring pair. */
static uint64_t sum_every_pair(brightness_function function, const uint8_t *frames,
                               int frame_count, size_t pixel_count) {
    uint64_t total = 0;
    int frame;

    for (frame = 1; frame < frame_count; frame++) {
        const uint8_t *previous_frame = frames + (size_t)(frame - 1) * pixel_count;
        const uint8_t *current_frame = frames + (size_t)frame * pixel_count;
        total += function(previous_frame, current_frame, pixel_count);
    }
    return total;
}

/* One clock around all 899 pairs. Reading the clip is outside this clock. */
static double run_once(brightness_function function, const uint8_t *frames,
                            int frame_count, size_t pixel_count, LARGE_INTEGER ticks_per_second) {
    LARGE_INTEGER start;
    LARGE_INTEGER end;
    volatile uint64_t total;

    QueryPerformanceCounter(&start); // start the clock
    total = sum_every_pair(function, frames, frame_count, pixel_count); // do the work
    QueryPerformanceCounter(&end); // stop the clock
    (void)total; // avoid compiler warning about unused variable
    return seconds_between(start, end, ticks_per_second); // return the time taken
}

static int compare_times(const void *first, const void *second) {
    double first_time = *(const double *)first;
    double second_time = *(const double *)second;
    return (first_time > second_time) - (first_time < second_time);
}

/* A progress line goes to stderr so the timing sheet on stdout stays unchanged. */
static void show_progress(const char *message) {
    fprintf(stderr, "%s\n", message);
    fflush(stderr);
}

/* Run the kernel 5 times and report the median time */
static void report_kernel(const char *name, const char *label, brightness_function function,
                          const uint8_t *frames, int frame_count, size_t pixel_count,
                          LARGE_INTEGER ticks_per_second) {
    double runs[5];
    int run;
    char message[80];

    snprintf(message, sizeof message, "%s, warmup", label);
    show_progress(message);
    run_once(function, frames, frame_count, pixel_count, ticks_per_second);
    for (run = 0; run < 5; run++) {
        snprintf(message, sizeof message, "%s, pass %d of 5", label, run + 1);
        show_progress(message);
        runs[run] = run_once(function, frames, frame_count, pixel_count, ticks_per_second);
    }
    qsort(runs, 5, sizeof(double), compare_times);
    snprintf(message, sizeof message, "%s, median %.3f s", label, runs[2]);
    show_progress(message);
    printf("%s median %.6f runs %.6f %.6f %.6f %.6f %.6f\n",
           name, runs[2], runs[0], runs[1], runs[2], runs[3], runs[4]);
}

int main(int argument_count, char **arguments) {
    int width;
    int height;
    int frame_count;
    size_t pixel_count;
    uint8_t *frames;
    LARGE_INTEGER ticks_per_second;
    int mismatches = 0;
    int frame;

    if (argument_count != 4) {
        fprintf(stderr, "usage: time_kernel width height frames\n");
        return 2;
    }

    width = atoi(arguments[1]);
    height = atoi(arguments[2]);
    frame_count = atoi(arguments[3]);
    if (width <= 0 || height <= 0 || frame_count < 2) {
        fprintf(stderr, "bad size\n");
        return 2;
    }

    /* Every frame is read before the clock starts. */
    show_progress("reading the frames");
    pixel_count = (size_t)width * (size_t)height;
    frames = (uint8_t *)malloc(pixel_count * (size_t)frame_count);
    if (!frames) {
        fprintf(stderr, "out of memory\n");
        return 2;
    }
    _setmode(_fileno(stdin), _O_BINARY);
    if (fread(frames, pixel_count, (size_t)frame_count, stdin) != (size_t)frame_count) {
        fprintf(stderr, "short read\n");
        return 2;
    }

    show_progress("frames are in memory");

    /* The three kernels should return the same sum on every pair. */
    show_progress("checking that the three sums agree");
    for (frame = 1; frame < frame_count; frame++) {
        const uint8_t *previous_frame = frames + (size_t)(frame - 1) * pixel_count;
        const uint8_t *current_frame = frames + (size_t)frame * pixel_count;
        uint64_t ordinary_sum = brightness_ordinary_c(previous_frame, current_frame, pixel_count);
        uint64_t compiler_sum = brightness_compiler_c(previous_frame, current_frame, pixel_count);
        uint64_t assembly_sum = brightness_avx2(previous_frame, current_frame, pixel_count);

        if (ordinary_sum != compiler_sum || ordinary_sum != assembly_sum) {
            mismatches++;
        }
    }

    QueryPerformanceFrequency(&ticks_per_second);
    printf("pairs %d mismatches %d pixels %zu\n", frame_count - 1, mismatches, pixel_count);
    fflush(stdout);
    report_kernel("brightness_ordinary_c", "Ordinary C", brightness_ordinary_c, frames, frame_count, pixel_count, ticks_per_second);
    report_kernel("brightness_compiler_c", "Optimized C", brightness_compiler_c, frames, frame_count, pixel_count, ticks_per_second);
    report_kernel("brightness_avx2", "Handwritten AVX2", brightness_avx2, frames, frame_count, pixel_count, ticks_per_second);

    free(frames);
    return mismatches == 0 ? 0 : 1;
}
