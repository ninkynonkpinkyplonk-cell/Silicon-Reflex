/* ctypes DLL: initialization allocates in Python; this module never mallocs. */
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
#include <windows.h>
#define API __declspec(dllexport)
#else
#include <time.h>
typedef int64_t LONG64;
#define API __attribute__((visibility("default")))
static LONG64 InterlockedCompareExchange64(volatile LONG64 *p, LONG64 value, LONG64 expected) {
    __atomic_compare_exchange_n(p, &expected, value, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return expected;
}
static LONG64 InterlockedExchange64(volatile LONG64 *p, LONG64 value) {
    return __atomic_exchange_n(p, value, __ATOMIC_SEQ_CST);
}
#endif
#define XXH_INLINE_ALL
#include "vendor/xxhash.h"
#define ABSTAIN 255u
#define CAPACITY 4096u

/* Sequence counters occupy separate cache lines. mmap is page aligned. */
typedef struct {
    volatile LONG64 request;
    unsigned char pad0[56];
    volatile LONG64 response;
    unsigned char pad1[56];
    uint64_t hash;
    uint32_t length;
    uint8_t mode, threshold, result;
    unsigned char pad2[49];
    unsigned char input[CAPACITY];
} Channel;
typedef char check_layout[(offsetof(Channel, input) == 192) ? 1 : -1];
static const uint8_t *decisions, *confidence;
static const uint64_t *fingerprints;
static uint64_t mask;

static double counter_ns(void) {
#ifdef _WIN32
    LARGE_INTEGER ticks, frequency;
    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&ticks);
    return (double)ticks.QuadPart * 1e9 / (double)frequency.QuadPart;
#else
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)now.tv_sec * 1e9 + (double)now.tv_nsec;
#endif
}

API uint32_t core_channel_size(void) { return (uint32_t)sizeof(Channel); }
API uint64_t core_hash(const void *bytes, size_t length) {
    return XXH64(bytes, length, 0);
}
API int core_bind(const uint8_t *d, const uint8_t *c, const uint64_t *f,
                  uint64_t count) {
    if (!d || !c || !f || count < 2 || (count & (count - 1))) return 0;
    decisions = d; confidence = c; fingerprints = f; mask = count - 1;
    return 1;
}
API void core_unbind(void) { decisions = NULL; confidence = NULL; fingerprints = NULL; mask = 0; }
/* Branchless after initialization. 255 represents missing/low confidence. */
API uint8_t core_lookup(uint64_t key, uint8_t threshold) {
    uint64_t i = key & mask;
    uint32_t valid = (uint32_t)(fingerprints[i] == key)
                   & (uint32_t)(confidence[i] >= threshold)
                   & (uint32_t)(decisions[i] <= 2);
    uint32_t bits = 0u - valid;
    return (uint8_t)((decisions[i] & bits) | (ABSTAIN & ~bits));
}
/* Interlocked operations provide cross-process publication barriers. SPSC only. */
API int core_publish(Channel *s, uint64_t sequence) {
    if (InterlockedCompareExchange64(&s->request, 0, 0) !=
        InterlockedCompareExchange64(&s->response, 0, 0)) return 0;
    InterlockedExchange64(&s->request, (LONG64)sequence);
    return 1;
}
API int core_process(Channel *s) {
    LONG64 seq = InterlockedCompareExchange64(&s->request, 0, 0);
    if (seq == InterlockedCompareExchange64(&s->response, 0, 0)) return 0;
    if (!decisions || s->length > CAPACITY || s->mode > 1) s->result = ABSTAIN;
    else s->result = core_lookup(s->mode ? s->hash : core_hash(s->input, s->length), s->threshold);
    InterlockedExchange64(&s->response, seq);
    return 1;
}
API int core_ready(Channel *s, uint64_t sequence) {
    return InterlockedCompareExchange64(&s->response, 0, 0) == (LONG64)sequence;
}
/* QPC only around the batch, not every decision; mixed caller-provided keys. */
API double core_benchmark(const uint64_t *keys, uint32_t count, uint64_t rounds,
                          uint8_t threshold, uint64_t *checksum) {
    double begin, end;
    uint64_t total = 0, n;
    uint32_t i = 0;
    if (!decisions || !count || !rounds) return -1;
    begin = counter_ns();
    for (n = 0; n < rounds; ++n) {
        total += core_lookup(keys[i], threshold);
        if (++i == count) i = 0;
    }
    end = counter_ns();
    *checksum = total;
    return (end - begin) / (double)rounds;
}
