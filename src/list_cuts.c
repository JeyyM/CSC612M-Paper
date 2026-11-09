/* Will read grayscale frames to detect when the cut happens */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <io.h>
#include <fcntl.h>

uint64_t brightness_ordinary_c(const uint8_t *first, const uint8_t *second, size_t pixel_count);
uint64_t brightness_compiler_c(const uint8_t *first, const uint8_t *second, size_t pixel_count);
uint64_t brightness_avx2(const uint8_t *first, const uint8_t *second, size_t pixel_count);

#define MAX_CUTS 32

/* Receive absolute sum from frames to mark the frame of the cut */
static void mark_cut(int *cuts, int *count, int *in_a_run, int frame, int above) {
    int already_in_a_run = *in_a_run;

    /* Back below the line. The next rise can start a new run. */
    if (!above) {
        *in_a_run = 0;
        return;
    }

    *in_a_run = 1;

    /* This pair is above the line, but the previous one was too. */
    if (already_in_a_run) {
        return;
    }
    if (*count >= MAX_CUTS) {
        return;
    }

    cuts[*count] = frame;
    *count += 1;
}

/* Prints the frames found by a method such as
   "brightness_ordinary_c 538 558"
   "brightness_compiler_c 538 558"
   "brightness_avx2 538 558"
*/
static void print_cuts(const char *name, const int *cuts, int count) {
    int index;

    printf("%s", name);
    for (index = 0; index < count; index++) {
        printf(" %d", cuts[index]);
    }
    printf("\n");
}

int main(int argument_count, char **arguments) {
    int width;           // frame width, from the first argument
    int height;          // frame height, from the second argument
    size_t pixel_count;  // width times height, one byte per pixel

    uint64_t cut_line;  // minimum needed to count as a cut which is 2
    uint8_t *previous_frame; // the frame already read
    uint8_t *current_frame;  // the frame just read
    int frame = 0;               // index of the frame just read, also the number of pairs
    
    // for mismatch detection of the 3 methods
    int mismatches = 0;          // pairs where the three sums disagree
    int quiet = 0;               // pairs at or below the line, the easy_below_2 count

    // for storing the frames marked by the 3 methods
    int ordinary_cuts[MAX_CUTS];
    int compiler_cuts[MAX_CUTS];
    int assembly_cuts[MAX_CUTS];

    // for counting the number of cuts stored by each method which should be 2 in total
    int ordinary_count = 0;
    int compiler_count = 0;
    int assembly_count = 0;

    // for tracking if the previous pair was above the line to avoid duplicate cuts
    int ordinary_in_a_run = 0;
    int compiler_in_a_run = 0;
    int assembly_in_a_run = 0;

    // to make sure width and height are provided
    if (argument_count != 3) {
        fprintf(stderr, "usage: list_cuts width height < gray\n");
        return 2;
    }

    // turn string arguments into integers
    width = atoi(arguments[1]);
    height = atoi(arguments[2]);
    if (width <= 0 || height <= 0) {
        fprintf(stderr, "bad size\n");
        return 2;
    }

    // how many pixels are in the frame
    pixel_count = (size_t)width * (size_t)height;
    cut_line = 2 * pixel_count;

    // makes windows receive the videos as the bytes they are from FFMPEG
    // rather than turning it into text
    _setmode(_fileno(stdin), _O_BINARY);

    // reserve memory for the frames
    previous_frame = (uint8_t *)malloc(pixel_count);
    current_frame = (uint8_t *)malloc(pixel_count);
    if (!previous_frame || !current_frame) {
        fprintf(stderr, "out of memory\n");
        return 2;
    }

    // For reading the first frame which as no previous frame yet
    if (fread(previous_frame, 1, pixel_count, stdin) != pixel_count) {
        fprintf(stderr, "need at least one frame\n");
        return 2;
    }

    /* Each pass reads one new frame and compares it with the frame before it. */
    while (fread(current_frame, 1, pixel_count, stdin) == pixel_count) {
        /* Absolute brightness change of this pair, from each kernel. */
        uint64_t ordinary_sum = brightness_ordinary_c(previous_frame, current_frame, pixel_count);
        uint64_t compiler_sum = brightness_compiler_c(previous_frame, current_frame, pixel_count);
        uint64_t assembly_sum = brightness_avx2(previous_frame, current_frame, pixel_count);
        uint8_t *held_frame; // for holding the address of the frame to be swapped later
        frame++;

        /* The three kernels should return the same sum. */
        if (ordinary_sum != compiler_sum || ordinary_sum != assembly_sum) {
            mismatches++;
        }

        /* Store the frame number when a cut is detected */
        mark_cut(ordinary_cuts, &ordinary_count, &ordinary_in_a_run, frame, ordinary_sum > cut_line);
        mark_cut(compiler_cuts, &compiler_count, &compiler_in_a_run, frame, compiler_sum > cut_line);
        mark_cut(assembly_cuts, &assembly_count, &assembly_in_a_run, frame, assembly_sum > cut_line);

        /* A pair at or below the line is quiet. run_system.py turns this into easy_below_2. */
        if (assembly_sum <= cut_line) {
            quiet++;
        }

        /* The frame just read becomes the previous frame. */
        held_frame = previous_frame;
        previous_frame = current_frame;
        current_frame = held_frame;
    }

    /* pairs and mismatches are separate rows. quiet is for run_system.py. */
    printf("pairs %d\n", frame);
    printf("mismatches %d\n", mismatches);
    printf("quiet %d\n", quiet);
    print_cuts("brightness_ordinary_c", ordinary_cuts, ordinary_count);
    print_cuts("brightness_compiler_c", compiler_cuts, compiler_count);
    print_cuts("brightness_avx2", assembly_cuts, assembly_count);

    free(previous_frame);
    free(current_frame);
    /* 0 when the three sums agreed on every pair. */
    return mismatches == 0 ? 0 : 1;
}
