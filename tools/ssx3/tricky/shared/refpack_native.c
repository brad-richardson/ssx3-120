// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
/* Native RefPack search: find_matches + optimal plan + greedy encode.
 *
 * Byte-identical port of tools/ssx3/tricky/shared/refpack_optimal.py
 * (find_matches, plan, emit_commands) and refpack_encode.py (encode) for
 * the Tricky world-archive pack search [native-refpack-1].
 *
 * Correctness contract (must hold for every input):
 *  - rp_optimal_plan() returns exactly the picks list of plan().
 *  - rp_optimal_encode() returns exactly the bytes of encode().
 *  - rp_greedy_encode() returns exactly the bytes of refpack_encode.encode().
 *  - rp_find_matches() returns exactly the (length, distance) candidate
 *    tuples of find_matches(), in the same per-position order.
 * Tie-breaks matter (within-set cost ties are common, not rare): the DP
 * visits long-match lengths in Python-set iteration order, embedded as a
 * generated table (refpack_cand_order.h). Dict insertion order of the
 * per-position class map is replicated by recording first-seen class order
 * during the most-recent-first chain scan.
 *
 * Determinism: single-threaded, no threads, no randomness, no floats, no
 * platform-dependent iteration. All working state is malloc'd per call.
 * C99, libc only.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "refpack_cand_order.h"

#define RP_MAX_LEN 1028
#define RP_MAX_DIST 131072
#define RP_MAX24 0xffffff

/* ------------------------------------------------------------------ */
/* hash chains: key (24-bit) -> ring of up to `cap` recent positions  */
/* ------------------------------------------------------------------ */

typedef struct {
    uint32_t key;
    int *ring;      /* cap positions, ring buffer */
    int count;      /* stored (<= cap) */
    int start;      /* index of oldest */
} rp_entry;

typedef struct {
    int *slot;      /* hash slots -> pool index, -1 = empty; power of two */
    uint32_t mask;
    rp_entry *pool; /* at most npool entries used */
    int npool;
    int cap;
} rp_chains;

static uint32_t rp_hash24(uint32_t key) {
    return key * 2654435761u;
}

static int rp_chains_init(rp_chains *c, int n, int cap) {
    uint32_t size = 16;
    while (size < (uint32_t)(n + 1) * 2)
        size <<= 1;
    c->slot = (int *)malloc(size * sizeof(int));
    if (!c->slot)
        return -1;
    for (uint32_t i = 0; i < size; i++)
        c->slot[i] = -1;
    c->mask = size - 1;
    c->pool = (rp_entry *)calloc((n > 0 ? (size_t)n : 1), sizeof(rp_entry));
    if (!c->pool) {
        free(c->slot);
        return -1;
    }
    c->npool = 0;
    c->cap = cap;
    return 0;
}

static void rp_chains_free(rp_chains *c) {
    for (int i = 0; i < c->npool; i++)
        free(c->pool[i].ring);
    free(c->pool);
    free(c->slot);
}

/* Find the entry for key, creating it (empty) when absent. */
static rp_entry *rp_chains_get(rp_chains *c, int n, uint32_t key) {
    uint32_t h = rp_hash24(key) & c->mask;
    while (c->slot[h] >= 0 && c->pool[c->slot[h]].key != key)
        h = (h + 1) & c->mask;
    if (c->slot[h] < 0) {
        if (c->npool >= n)
            return NULL; /* cannot happen: distinct keys <= n */
        rp_entry *e = &c->pool[c->npool];
        e->key = key;
        e->count = 0;
        e->start = 0;
        if (c->cap > 0) {
            e->ring = (int *)malloc((size_t)c->cap * sizeof(int));
            if (!e->ring)
                return NULL;
        } else {
            e->ring = NULL;
        }
        c->slot[h] = c->npool;
        c->npool++;
        return e;
    }
    return &c->pool[c->slot[h]];
}

/* Look up without creating; NULL when absent. */
static rp_entry *rp_chains_find(rp_chains *c, uint32_t key) {
    uint32_t h = rp_hash24(key) & c->mask;
    while (c->slot[h] >= 0) {
        if (c->pool[c->slot[h]].key == key)
            return &c->pool[c->slot[h]];
        h = (h + 1) & c->mask;
    }
    return NULL;
}

static int rp_chains_append(rp_chains *c, int n, uint32_t key, int pos) {
    rp_entry *e = rp_chains_get(c, n, key);
    if (!e)
        return -1;
    if (c->cap <= 0)
        return 0; /* maxlen=0 deque: appends are discarded */
    if (e->count < c->cap) {
        e->ring[(e->start + e->count) % c->cap] = pos;
        e->count++;
    } else {
        e->ring[e->start] = pos;
        e->start = (e->start + 1) % c->cap;
    }
    return 0;
}

/* k-th most recent position (k=0 is newest); caller bounds k < count. */
static int rp_entry_recent(const rp_entry *e, int cap, int k) {
    return e->ring[(e->start + e->count - 1 - k) % cap];
}

static uint32_t rp_key3(const unsigned char *d, int pos) {
    return ((uint32_t)d[pos] << 16) | ((uint32_t)d[pos + 1] << 8) | d[pos + 2];
}

/* Pure longest-common-prefix of d[a:] and d[b:], at most limit.
 * Same value as refpack_optimal._extend for all inputs. */
static int rp_extend(const unsigned char *d, int a, int b, int limit) {
    int n = 0;
    while (n < limit) {
        int k = limit - n < 64 ? limit - n : 64;
        if (memcmp(d + a + n, d + b + n, (size_t)k) == 0) {
            n += k;
            continue;
        }
        for (int t = 0; t < k; t++) {
            if (d[a + n + t] != d[b + n + t])
                return n + t;
        }
        return n + k; /* unreachable: memcmp differed, so the loop returns */
    }
    return n;
}

static int rp_cls(int dist) {
    return dist <= 1024 ? 0 : dist <= 16384 ? 1 : 2;
}

static int rp_minimum(int cls) {
    return cls == 0 ? 3 : cls == 1 ? 4 : 5;
}

/* ------------------------------------------------------------------ */
/* find_matches: per-position longest match per distance class.       */
/* Outputs per position (only pos < n-2 are written; the rest are 0): */
/* nmatch[pos], and nmatch[pos] entries of mcls/mlem/mdist row pos.   */
/* ------------------------------------------------------------------ */

static int rp_match_search(const unsigned char *d, int n, int candidates,
                           int skip_threshold, unsigned char *nmatch,
                           unsigned char *mcls, int *mlen, int *mdist) {
    rp_chains c;
    if (rp_chains_init(&c, n, candidates) != 0)
        return -1;
    int rc = -1;
    int carry_exists = 0, carry_L = 0, carry_D = 0;
    int pos = 0;
    while (pos < n - 2) {
        uint32_t key = rp_key3(d, pos);
        if (carry_exists && carry_L >= 3) {
            int cls = rp_cls(carry_D);
            int L = carry_L < RP_MAX_LEN ? carry_L : RP_MAX_LEN;
            nmatch[pos] = 1;
            mcls[pos * 3] = (unsigned char)cls;
            mlen[pos * 3] = L;
            mdist[pos * 3] = carry_D;
            carry_L--;
            if (rp_chains_append(&c, n, key, pos) != 0)
                goto out;
            pos++;
            continue;
        }
        int best_len[3] = {-1, -1, -1};
        int best_dist[3] = {0, 0, 0};
        int order[3];
        int ncls = 0;
        int limit = n - pos < RP_MAX_LEN ? n - pos : RP_MAX_LEN;
        rp_entry *e = rp_chains_find(&c, key);
        if (e) {
            for (int k = 0; k < e->count; k++) {
                int prev = rp_entry_recent(e, candidates, k);
                int dist = pos - prev;
                if (dist > RP_MAX_DIST)
                    break;
                int cls = rp_cls(dist);
                if (best_len[cls] >= limit)
                    continue;
                int length = rp_extend(d, prev, pos, limit);
                if (length >= rp_minimum(cls) &&
                    (best_len[cls] < 0 || length > best_len[cls])) {
                    if (best_len[cls] < 0)
                        order[ncls++] = cls;
                    best_len[cls] = length;
                    best_dist[cls] = dist;
                }
            }
        }
        nmatch[pos] = (unsigned char)ncls;
        for (int t = 0; t < ncls; t++) {
            int cls = order[t];
            mcls[pos * 3 + t] = (unsigned char)cls;
            mlen[pos * 3 + t] = best_len[cls];
            mdist[pos * 3 + t] = best_dist[cls];
        }
        if (rp_chains_append(&c, n, key, pos) != 0)
            goto out;
        /* longest = max by (length, distance), exactly like max() on tuples */
        int bl = -1, bd = 0;
        for (int t = 0; t < ncls; t++) {
            int cls = order[t];
            if (best_len[cls] > bl ||
                (best_len[cls] == bl && best_dist[cls] > bd)) {
                bl = best_len[cls];
                bd = best_dist[cls];
            }
        }
        if (ncls > 0 && bl >= skip_threshold) {
            carry_exists = 1;
            carry_L = bl - 1;
            carry_D = bd;
        }
        pos++;
    }
    rc = 0;
out:
    rp_chains_free(&c);
    return rc;
}

/* ------------------------------------------------------------------ */
/* optimal plan: backward DP over the 2/3/4-byte forms + literal runs */
/* ------------------------------------------------------------------ */

#define RP_INF 0x3fffffff

static int rp_form_cost(int L, int dist) {
    if (L <= 10 && dist <= 1024)
        return 2;
    if (L <= 67 && dist <= 16384)
        return 3;
    return 4;
}

/* choice encoding: kind 0 = stop(a=rem), 1 = lit(a=k), 2 = match(a=p,b=L,c=dist) */
typedef struct {
    int *cost;   /* n+1 */
    char *kind;  /* n */
    int *ca;     /* n */
    int *cb;     /* n */
    int *cc;     /* n */
} rp_dp;

/* Run the backward DP; returns 0 on success. */
static int rp_dp_run(const unsigned char *nmatch, const int *mlen,
                     const int *mdist, int n, rp_dp *dp) {
    dp->cost[n] = 1;
    for (int i = n - 1; i >= 0; i--) {
        int best = RP_INF, bk = -1, ba = 0, bb = 0, bc = 0;
        int rem = n - i;
        if (rem <= 3) {
            best = 1 + rem;
            bk = 0;
            ba = rem;
        }
        for (int k = 4; k <= 112 && k <= rem; k += 4) {
            int c = 1 + k + dp->cost[i + k];
            if (c < best) {
                best = c;
                bk = 1;
                ba = k;
            }
        }
        for (int p = 0; p < 4; p++) {
            int j = i + p;
            if (j >= n)
                break;
            int nm = nmatch[j];
            for (int t = 0; t < nm; t++) {
                int length = mlen[j * 3 + t];
                int dist = mdist[j * 3 + t];
                if (j + length > n)
                    length = n - j;
                int minimum = rp_minimum(rp_cls(dist));
                if (length < minimum)
                    continue;
                if (length <= 16) {
                    for (int L = minimum; L <= length; L++) {
                        int c = p + rp_form_cost(L, dist) + dp->cost[j + L];
                        if (c < best) {
                            best = c;
                            bk = 2;
                            ba = p;
                            bb = L;
                            bc = dist;
                        }
                    }
                } else {
                    int cnt = RP_CAND_COUNT[length];
                    const short *row = RP_CAND_ORDER[length];
                    for (int u = 0; u < cnt; u++) {
                        int L = row[u];
                        if (L > length)
                            continue;
                        /* minimum never filters here (all values >= 10),
                         * but keep the guard for parity with the set
                         * difference. */
                        if (L < minimum)
                            continue;
                        int c = p + rp_form_cost(L, dist) + dp->cost[j + L];
                        if (c < best) {
                            best = c;
                            bk = 2;
                            ba = p;
                            bb = L;
                            bc = dist;
                        }
                    }
                }
            }
        }
        if (bk < 0)
            return -1; /* unreachable: lit/stop always applies */
        dp->cost[i] = best;
        dp->kind[i] = (char)bk;
        dp->ca[i] = ba;
        dp->cb[i] = bb;
        dp->cc[i] = bc;
    }
    return 0;
}

/* Emit the 10FB byte stream for data following the DP choices.
 * Returns bytes written, or -1 when out is too small. */
static int rp_emit_optimal(const unsigned char *d, int n, const rp_dp *dp,
                           unsigned char *out, int cap) {
    int o = 0;
#define RP_PUT1(v) do { if (o >= cap) return -1; out[o++] = (unsigned char)(v); } while (0)
#define RP_PUTN(p, k) do { if (o + (k) > cap) return -1; \
    memcpy(out + o, (p), (size_t)(k)); o += (k); } while (0)
    RP_PUT1(0x10);
    RP_PUT1(0xfb);
    RP_PUT1((n >> 16) & 255);
    RP_PUT1((n >> 8) & 255);
    RP_PUT1(n & 255);
    int i = 0;
    while (1) {
        int bk, ba, bb, bc;
        if (i < n) {
            bk = dp->kind[i];
            ba = dp->ca[i];
            bb = dp->cb[i];
            bc = dp->cc[i];
        } else {
            bk = 0;
            ba = 0;
            bb = bc = 0;
        }
        if (bk == 0) {
            RP_PUT1(0xfc | ba);
            RP_PUTN(d + i, ba);
            break;
        }
        if (bk == 1) {
            RP_PUT1(0xe0 + ba / 4 - 1);
            RP_PUTN(d + i, ba);
            i += ba;
            continue;
        }
        {
            int p = ba, L = bb, dist = bc, offset = dist - 1;
            if (L <= 10 && dist <= 1024) {
                RP_PUT1(p | ((L - 3) << 2) | ((offset >> 8) << 5));
                RP_PUT1(offset & 255);
            } else if (L <= 67 && dist <= 16384) {
                RP_PUT1(0x80 | (L - 4));
                RP_PUT1((p << 6) | (offset >> 8));
                RP_PUT1(offset & 255);
            } else {
                RP_PUT1(0xc0 | p | (((L - 5) >> 8) << 2) | ((offset >> 16) << 4));
                RP_PUT1((offset >> 8) & 255);
                RP_PUT1(offset & 255);
                RP_PUT1((L - 5) & 255);
            }
            RP_PUTN(d + i, p);
            i += p + L;
        }
    }
    return o;
#undef RP_PUT1
#undef RP_PUTN
}

/* ------------------------------------------------------------------ */
/* greedy encoder (refpack_encode.encode)                             */
/* ------------------------------------------------------------------ */

static int rp_greedy_match(const unsigned char *d, int n, rp_chains *c,
                           int at, int *dist_out) {
    int length = 0, distance = 0;
    if (at + 3 <= n) {
        int limit = n - at < RP_MAX_LEN ? n - at : RP_MAX_LEN;
        uint32_t key = rp_key3(d, at);
        rp_entry *e = rp_chains_find(c, key);
        if (e) {
            for (int k = 0; k < e->count; k++) {
                int prev = rp_entry_recent(e, c->cap, k);
                int dist = at - prev;
                if (dist > RP_MAX_DIST)
                    break;
                int m = 3;
                if (length &&
                    memcmp(d + prev, d + at, (size_t)length) != 0)
                    continue;
                while (m < limit && d[prev + m] == d[at + m])
                    m++;
                int minimum = rp_minimum(rp_cls(dist));
                if (m >= minimum && m > length) {
                    length = m;
                    distance = dist;
                    if (m == limit)
                        break;
                }
            }
        }
    }
    *dist_out = distance;
    return length;
}

static int rp_greedy_remember(rp_chains *c, int n, const unsigned char *d,
                              int start, int end) {
    int stop = end < n - 2 ? end : n - 2;
    for (int i = start; i < stop; i++) {
        if (rp_chains_append(c, n, rp_key3(d, i), i) != 0)
            return -1;
    }
    return 0;
}

/* Worst-case greedy output for n input bytes (all literals + header). */
static int rp_greedy_cap(int n) {
    return 5 + n + n / 4 + 8;
}

static int rp_greedy_run(const unsigned char *d, int n, int candidates,
                         unsigned char *out, int cap) {
    rp_chains c;
    if (rp_chains_init(&c, n, candidates) != 0)
        return -1;
    int rc = -1, o = 0, pos = 0, pending = 0;
#define RP_GPUT1(v) do { if (o >= cap) goto out; out[o++] = (unsigned char)(v); } while (0)
#define RP_GPUTN(p, k) do { if (o + (k) > cap) goto out; \
    memcpy(out + o, (p), (size_t)(k)); o += (k); } while (0)
    RP_GPUT1(0x10);
    RP_GPUT1(0xfb);
    RP_GPUT1((n >> 16) & 255);
    RP_GPUT1((n >> 8) & 255);
    RP_GPUT1(n & 255);
    while (pos < n) {
        int dist = 0;
        int length = rp_greedy_match(d, n, &c, pos, &dist);
        if (length && length < RP_MAX_LEN) {
            int dummy = 0;
            int next_length = rp_greedy_match(d, n, &c, pos + 1, &dummy);
            if (next_length > length + 1)
                length = 0;
        }
        if (!length) {
            if (rp_greedy_remember(&c, n, d, pos, pos + 1) != 0)
                goto out;
            pos++;
            continue;
        }
        /* literals(pending, pos) */
        {
            int start = pending;
            while (pos - start >= 4) {
                int m = (pos - start) & ~3;
                if (m > 112)
                    m = 112;
                RP_GPUT1(0xe0 + m / 4 - 1);
                RP_GPUTN(d + start, m);
                start += m;
            }
            pending = start;
        }
        {
            int prefix = pos - pending;
            int offset = dist - 1;
            if (length <= 10 && dist <= 1024) {
                RP_GPUT1(prefix | ((length - 3) << 2) | ((offset >> 8) << 5));
                RP_GPUT1(offset & 255);
            } else if (length <= 67 && dist <= 16384) {
                RP_GPUT1(0x80 | (length - 4));
                RP_GPUT1((prefix << 6) | (offset >> 8));
                RP_GPUT1(offset & 255);
            } else {
                RP_GPUT1(0xc0 | prefix | (((length - 5) >> 8) << 2) |
                         ((offset >> 16) << 4));
                RP_GPUT1((offset >> 8) & 255);
                RP_GPUT1(offset & 255);
                RP_GPUT1((length - 5) & 255);
            }
            RP_GPUTN(d + pending, prefix);
        }
        if (rp_greedy_remember(&c, n, d, pos, pos + length) != 0)
            goto out;
        pos += length;
        pending = pos;
    }
    /* literals(pending, n) */
    {
        int start = pending;
        while (n - start >= 4) {
            int m = (n - start) & ~3;
            if (m > 112)
                m = 112;
            RP_GPUT1(0xe0 + m / 4 - 1);
            RP_GPUTN(d + start, m);
            start += m;
        }
        pending = start;
    }
    RP_GPUT1(0xfc | (n - pending));
    RP_GPUTN(d + pending, n - pending);
    rc = o;
out:
    rp_chains_free(&c);
    return rc;
#undef RP_GPUT1
#undef RP_GPUTN
}

/* ------------------------------------------------------------------ */
/* exported API (ctypes)                                              */
/* ------------------------------------------------------------------ */

const char *rp_version(void) {
    return "refpack-native 1 (optimal plan + greedy, order-table v1)";
}

/* Per-position matches. nmatch/mcls/mlen/mdist sized n, n, 3*n, 3*n.
 * Clamp: positions >= n-2 are left as-is (caller zeroes them).
 * Returns 0 on success, -1 on allocation failure. */
int rp_find_matches(const unsigned char *data, int n, int candidates,
                    int skip_threshold, unsigned char *nmatch,
                    unsigned char *mcls, int *mlen, int *mdist) {
    if (!data || n < 0 || !nmatch || !mcls || !mlen || !mdist)
        return -1;
    if (n == 0)
        return 0;
    return rp_match_search(data, n, candidates, skip_threshold, nmatch, mcls,
                           mlen, mdist);
}

/* Optimal picks. kinds/pa/pb/pc sized max_picks (>= n+1).
 * kind 0 = stop(a=rem), 1 = lit(a=k), 2 = match(a=p,b=L,c=dist).
 * Returns the pick count, or -1 on error. */
int rp_optimal_plan(const unsigned char *data, int n, int candidates,
                    int skip_threshold, unsigned char *kinds, int *pa,
                    int *pb, int *pc, int max_picks) {
    unsigned char *nmatch;
    unsigned char *mcls;
    int *mlen, *mdist;
    rp_dp dp;
    int rc = -1, npicks = 0, i;
    if (!data || n < 0 || n > RP_MAX24 || !kinds || !pa || !pb || !pc ||
        max_picks < n + 1)
        return -1;
    nmatch = (unsigned char *)calloc((size_t)n + 1, 1);
    mcls = (unsigned char *)calloc((size_t)n * 3 + 1, 1);
    mlen = (int *)calloc((size_t)n * 3 + 1, sizeof(int));
    mdist = (int *)calloc((size_t)n * 3 + 1, sizeof(int));
    dp.cost = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.kind = (char *)malloc((size_t)n + 1);
    dp.ca = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.cb = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.cc = (int *)malloc(((size_t)n + 1) * sizeof(int));
    if (!nmatch || !mcls || !mlen || !mdist || !dp.cost || !dp.kind || !dp.ca ||
        !dp.cb || !dp.cc)
        goto done;
    if (n > 0 &&
        rp_match_search(data, n, candidates, skip_threshold, nmatch, mcls,
                        mlen, mdist) != 0)
        goto done;
    if (rp_dp_run(nmatch, mlen, mdist, n, &dp) != 0)
        goto done;
    i = 0;
    while (1) {
        int bk, ba, bb, bc;
        if (i < n) {
            bk = dp.kind[i];
            ba = dp.ca[i];
            bb = dp.cb[i];
            bc = dp.cc[i];
        } else {
            bk = 0;
            ba = 0;
            bb = bc = 0;
        }
        if (npicks >= max_picks)
            goto done;
        kinds[npicks] = (unsigned char)bk;
        pa[npicks] = ba;
        pb[npicks] = bb;
        pc[npicks] = bc;
        npicks++;
        if (bk == 0)
            break;
        if (bk == 1)
            i += ba;
        else
            i += ba + bb;
        if (i > n)
            goto done;
    }
    rc = npicks;
done:
    free(nmatch);
    free(mcls);
    free(mlen);
    free(mdist);
    free(dp.cost);
    free(dp.kind);
    free(dp.ca);
    free(dp.cb);
    free(dp.cc);
    return rc;
}

/* Optimal encoding. out sized >= 5 + n + n/4 + 8.
 * Returns bytes written, or -1 on error. */
int rp_optimal_encode(const unsigned char *data, int n, int candidates,
                      int skip_threshold, unsigned char *out, int cap) {
    unsigned char *nmatch;
    unsigned char *mcls;
    int *mlen, *mdist;
    rp_dp dp;
    int rc = -1;
    if (!data || n < 0 || n > RP_MAX24 || !out)
        return -1;
    nmatch = (unsigned char *)calloc((size_t)n + 1, 1);
    mcls = (unsigned char *)calloc((size_t)n * 3 + 1, 1);
    mlen = (int *)calloc((size_t)n * 3 + 1, sizeof(int));
    mdist = (int *)calloc((size_t)n * 3 + 1, sizeof(int));
    dp.cost = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.kind = (char *)malloc((size_t)n + 1);
    dp.ca = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.cb = (int *)malloc(((size_t)n + 1) * sizeof(int));
    dp.cc = (int *)malloc(((size_t)n + 1) * sizeof(int));
    if (!nmatch || !mcls || !mlen || !mdist || !dp.cost || !dp.kind || !dp.ca ||
        !dp.cb || !dp.cc)
        goto done;
    if (n > 0 &&
        rp_match_search(data, n, candidates, skip_threshold, nmatch, mcls,
                        mlen, mdist) != 0)
        goto done;
    if (rp_dp_run(nmatch, mlen, mdist, n, &dp) != 0)
        goto done;
    rc = rp_emit_optimal(data, n, &dp, out, cap);
done:
    free(nmatch);
    free(mcls);
    free(mlen);
    free(mdist);
    free(dp.cost);
    free(dp.kind);
    free(dp.ca);
    free(dp.cb);
    free(dp.cc);
    return rc;
}

/* Greedy encoding (refpack_encode.encode). out sized >= rp_greedy_cap(n).
 * candidates must be >= 1. Returns bytes written, or -1 on error. */
int rp_greedy_encode(const unsigned char *data, int n, int candidates,
                     unsigned char *out, int cap) {
    if (!data || n < 0 || n > RP_MAX24 || !out || candidates < 1)
        return -1;
    return rp_greedy_run(data, n, candidates, out, cap);
}

/* Worst-case output size for n input bytes (either encoder). */
int rp_encode_cap(int n) {
    if (n < 0 || n > RP_MAX24)
        return -1;
    return rp_greedy_cap(n);
}
