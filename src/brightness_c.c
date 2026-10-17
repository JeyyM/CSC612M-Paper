/* C brightness sum. Compiled twice: vectorizer off as brightness_ordinary_c,
   and AVX2 auto-vectorization on as brightness_compiler_c. */

#include <stddef.h>
#include <stdint.h>

#ifndef BRIGHTNESS_FUNCTION
#define BRIGHTNESS_FUNCTION brightness_ordinary_c
#endif

uint64_t BRIGHTNESS_FUNCTION(const uint8_t *a, const uint8_t *b, size_t n) {
    uint64_t sum = 0;
    size_t i;
    for (i = 0; i < n; i++) {
        int diff = (int)a[i] - (int)b[i];
        sum += (uint64_t)(diff < 0 ? -diff : diff);
    }
    return sum;
}
