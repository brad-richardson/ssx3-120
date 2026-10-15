/* N8C2 diagnostic app-local libhardware loader probe. MIT license. */
#define _GNU_SOURCE
#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdatomic.h>

struct hw_module_t;

static _Atomic unsigned int call_count;
static const char *const tag = "ps2x-hwcompat";

static const char *own_path(void) {
    Dl_info info;
    if (dladdr((const void *)&own_path, &info) && info.dli_fname)
        return info.dli_fname;
    return "(dladdr unavailable)";
}

__attribute__((constructor)) static void loaded(void) {
    __android_log_print(ANDROID_LOG_INFO, tag, "mapped path=%s", own_path());
}

__attribute__((visibility("default")))
int hw_get_module(const char *id, const struct hw_module_t **module) {
    if (module)
        *module = 0;
    unsigned int count = atomic_fetch_add_explicit(&call_count, 1, memory_order_relaxed) + 1;
    if (count <= 16)
        __android_log_print(ANDROID_LOG_INFO, tag,
                            "hw_get_module call=%u id=%s out=%s return=%d path=%s",
                            count, id ? id : "(null)", module ? "null" : "(null pointer)",
                            -ENOENT, own_path());
    else if (count == 17)
        __android_log_print(ANDROID_LOG_INFO, tag,
                            "hw_get_module call log capped after 16; return=%d path=%s",
                            -ENOENT, own_path());
    return -ENOENT;
}
