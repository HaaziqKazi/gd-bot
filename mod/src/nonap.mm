#import <Foundation/Foundation.h>

#include "nonap.hpp"

// Compiled with -fno-objc-arc (see CMakeLists), so the activity token is
// retained manually. It is deliberately never released: the suppression should
// last for the lifetime of the process, and endActivity would hand control back
// to App Nap mid-run.

namespace {
    id g_activityToken = nil;
}

namespace gdrl {

bool disableAppNap(const char* reason) {
    if (g_activityToken != nil) return true;

    @autoreleasepool {
        NSProcessInfo* info = [NSProcessInfo processInfo];
        if (![info respondsToSelector:@selector(beginActivityWithOptions:reason:)]) {
            return false;
        }

        // NSActivityUserInitiated is what actually takes the process out of App
        // Nap. NSActivityLatencyCritical additionally opts out of timer
        // coalescing, which matters because the game's step accumulator is
        // driven off the run loop -- coalesced timers would show up as coarser,
        // burstier dt.
        NSActivityOptions options =
            NSActivityUserInitiated | NSActivityLatencyCritical;

        NSString* why = [NSString stringWithUTF8String:(reason ? reason : "gdrl")];
        id token = [info beginActivityWithOptions:options reason:why];
        g_activityToken = [token retain];
    }

    return g_activityToken != nil;
}

bool appNapSuppressed() {
    return g_activityToken != nil;
}

}
