/* video_test.c - the presented width (cyc_video.h, common/nes_video_geometry.h):
 * the widths of every mode, Fit's clamp to the window, request queuing after
 * the window exists and the one apply point (CTest cyc_video_test). */
#include "cyc_video.h"
#include "cyc_ring.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

int main(void)
{
    /* the numbers: square pixels, 240 rows, even widths, STOCK explicit */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_STOCK, 0, 0) == 256);
    CHECK(nes_video_geometry_width_for(NES_VIDEO_16_9, 0, 0) == 426);
    CHECK(nes_video_geometry_width_for(NES_VIDEO_21_9, 0, 0) == 560);
    CHECK(nes_video_geometry_width_for(NES_VIDEO_32_9, 0, 0) == 854);
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 0, 0) == 256);          /* no window: 16:15 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 1024, 960) == 256);     /* 16:15 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 640, 480) == 320);      /* 4:3 fills */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 1920, 1080) == 426);    /* 16:9 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 3440, 1440) == 574);    /* 43:18 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 5120, 1440) == 854);    /* 32:9 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 8000, 1000) == 854);    /* clamped at 32:9 */
    CHECK(nes_video_geometry_width_for(NES_VIDEO_FIT, 500, 1000) == 256);     /* clamped at 16:15 */
    for (int w = 100; w < 3000; w += 7)
        for (int h = 100; h < 2000; h += 13) {
            int x = nes_video_geometry_width_for(NES_VIDEO_FIT, w, h);
            CHECK(x >= 256 && x <= NES_VIDEO_MAX_WIDTH && !(x & 1));
        }
    int m;
    CHECK(nes_video_geometry_parse("16-9", &m) && m == NES_VIDEO_16_9);
    CHECK(nes_video_geometry_parse("21x9", &m) && m == NES_VIDEO_21_9);
    CHECK(nes_video_geometry_parse("32_9", &m) && m == NES_VIDEO_32_9);
    CHECK(nes_video_geometry_parse("Adaptive", &m) && m == NES_VIDEO_FIT);
    CHECK(nes_video_geometry_parse("off", &m) && m == NES_VIDEO_STOCK);
    CHECK(!nes_video_geometry_parse("17:9", &m));

    /* the state: stock by default; before the window a request applies at once */
    CHECK(cyc_video_width() == 256 && cyc_video_mode() == NES_VIDEO_STOCK && cyc_video_native_x0() == 0);
    cyc_video_set_mode(NES_VIDEO_21_9);
    CHECK(cyc_video_width() == 560 && cyc_video_native_x0() == 152);
    CHECK(!cyc_video_apply_pending());
    /* with a window a request waits for the apply point */
    cyc_video_window_ready();
    cyc_video_window_resized(1920, 1080);
    CHECK(cyc_video_width() == 560);                  /* not Fit: the window does not matter */
    cyc_video_set_mode(NES_VIDEO_FIT);
    CHECK(cyc_video_width() == 560);                  /* queued */
    CHECK(cyc_video_apply_pending() && cyc_video_width() == 426);
    CHECK(!cyc_video_apply_pending());
    cyc_video_window_resized(5120, 1440);
    CHECK(cyc_video_width() == 426 && cyc_video_apply_pending() && cyc_video_width() == 854);
    cyc_video_window_resized(5120, 1440);             /* the same size: nothing queued */
    CHECK(!cyc_video_apply_pending());
    cyc_video_set_mode(NES_VIDEO_FIT);                /* idempotent */
    CHECK(!cyc_video_apply_pending());
    /* a resize and back before the apply point queues nothing net */
    cyc_video_window_resized(1920, 1080);
    cyc_video_window_resized(5120, 1440);
    CHECK(!cyc_video_apply_pending() && cyc_video_width() == 854);
    cyc_video_set_mode(NES_VIDEO_STOCK);
    CHECK(cyc_video_apply_pending() && cyc_video_width() == 256);
    cyc_video_request_width(301);                     /* even, fixed */
    CHECK(cyc_video_apply_pending() && cyc_video_width() == 300 && cyc_video_mode() == NES_VIDEO_STOCK);
    /* every width change is in the ring */
    CHECK(cyc_ring_kind_total(CYC_EV_VIDEO) == 5);
    printf("%s (%d failures)\n", failures ? "FAILED" : "cyc_video_test passed", failures);
    return failures != 0;
}
