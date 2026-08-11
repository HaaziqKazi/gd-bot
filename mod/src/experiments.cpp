// Temporary probe experiments. Delete once their questions are answered.
//
// Kept out of main.cpp deliberately: these hooks exist to settle specific
// architectural questions, and once settled the answers belong in README.md
// while this file goes away. Nothing here should grow into the real transport.
//
// The null-input guard that made these measurements trustworthy started life
// here and has since moved to main.cpp, where it is permanent -- it is not an
// experiment, it is a precondition for every measurement this repo takes.
//
// -------------------------------------------------------------------------
// Result 1: processCommands and getModifiedDelta are inlined on macOS arm64.
//
// Geode reports "Enabled ... hook" at addresses that match the 2.2081 bindings
// exactly, and the detours then never execute. Verified by disassembling the
// shipped arm64 slice and counting call sites:
//
//   bl 0x124490 (processCommands)  -> 0 in the entire binary
//   bl 0x122958 (getModifiedDelta) -> 1, inside LevelEditorLayer::updateEditor
//
// So processCommands is not a callable function in gameplay at all, and
// getModifiedDelta survives only on the editor path. GJBaseGameLayer::update
// does the delta accumulation inline (visible at 0x100122acc: fdiv by the
// timewarp field, then fadd into the double accumulators at +0x398/+0x3a8).
//
// A binding carrying an m1 address means the function EXISTS, not that anything
// CALLS it. The PCHITS counters below stay as standing evidence: if a future GD
// build stops inlining them, they go nonzero and this claim needs revisiting.
//
// -------------------------------------------------------------------------
// Result 2: the simulation rate is ours to set, via update()'s dt argument.
//
// Since the accumulator is inlined into update and consumes that dt, rewriting
// dt on the way in controls how much simulated time each rendered frame eats.
// Measured over 91 attempts in three regimes, all bit-identical at
// maxX=507.615234375 t=1.629166752:
//
//   passthrough   ~160 frames/attempt   0.35 attempts/sec
//   8 ticks/frame ~112 frames/attempt   0.56 attempts/sec
//   32 ticks/frame ~76 frames/attempt   0.88 attempts/sec
//
// Frame cost fits frames = 96/k + 64 exactly at k = 1, 2, 8: 96 frames of
// simulation that scale with dt, and 64 frames (~1.07s) of fixed death/respawn
// animation that does not. That fixed cost, not dt, is the throughput ceiling.
//
// Env:
//   GDRL_EXP=1           enable instrumentation (default off)
//   GDRL_DELTA_TICKS=N   feed update() exactly N/240s instead of real frame time
//                        (0 or unset = observe only, do not override)

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>

#include <cstdlib>
#include <cmath>

using namespace geode::prelude;

namespace {
    // The measured physics timestep. Not a guess: t advances in exact 1/240
    // increments across 37 attempts (README, "Physics is fixed-step at 240Hz").
    constexpr double kTimestep = 1.0 / 240.0;

    int envInt(const char* name, int fallback) {
        const char* v = std::getenv(name);
        if (!v || !*v) return fallback;
        return std::atoi(v);
    }

    const bool g_expOn      = envInt("GDRL_EXP", 0) == 1;
    const int  g_deltaTicks = envInt("GDRL_DELTA_TICKS", 0);

    // Experiment 3: cut the fixed 64-frame floor.
    //
    // frames = 96/k + 64 says the residue is death/respawn animation, which does
    // not scale with dt because it is driven by the cocos scheduler in real time
    // rather than by update()'s dt. destroyPlayer sets m_inResetDelay and arms a
    // delayed reset -- there is an `fmov s0, #1.0` immediately before a call in
    // its body, consistent with the ~1.07s measured.
    //
    // delayedResetLevel has no bl call sites, so it is invoked through a
    // scheduled selector and the delay is not a constant worth patching. Forcing
    // resetLevel() as soon as m_inResetDelay is observed skips the wait instead.
    //
    // The risk is a double reset: GD's own delayed callback may still fire after
    // ours and start a second attempt. That needs no special detection -- a
    // spurious reset produces an attempt whose maxX is not 507.615234375, and
    // the determinism invariant is already checked on every line.
    const bool g_fastReset = envInt("GDRL_FAST_RESET", 0) == 1;

    long g_fastResets = 0;

    // ---------------------------------------------------------------------
    // Experiment 4: is m_attemptTime a usable input-placement clock?
    //
    // m_currentStep was supposed to be the tick counter and never moves, so
    // tick-exact input placement needs a different clock. PlayLayer::m_attemptTime
    // is a double (not the SeedValueRSV of the same name on GJGameState) and the
    // final value has always been an exact multiple of 1/240 -- 1.629166752 =
    // 391/240. But "the endpoint is a multiple" is much weaker than what a
    // placement key has to satisfy, and assuming the stronger property from the
    // weaker observation is the mistake that produced the m_currentStep claim.
    //
    // Four properties, each measured rather than inferred:
    //   1. Quantised   -- t*240 lands on integers, at every sample, not just at
    //                     the end. Measured as max |t*240 - round(t*240)|.
    //   2. Monotonic   -- never runs backwards within an attempt.
    //   3. Commandable -- the per-frame tick delta equals the dt we feed, so a
    //                     target tick can be scheduled a known distance ahead.
    //   4. Reproducible-- the same attempt yields the same final tick every time.
    //
    // Property 1 is the load-bearing one: if the residual is nonzero, rounding
    // t*240 to a tick index is a lossy guess and inputs will land off-by-one.
    constexpr double kTicksPerSec = 240.0;

    double g_clkMaxResid = 0.0;
    long   g_clkFrames   = 0;
    long   g_clkLastTick = -1;
    long   g_clkFinal    = -1;
    int    g_clkNonMono  = 0;
    int    g_clkDeltaHist[40] = {};
    int    g_clkDeltaBig = 0;
    int    g_clkTraced   = 0;

    void resetClockStats() {
        g_clkMaxResid = 0.0;
        g_clkFrames   = 0;
        g_clkLastTick = -1;
        g_clkNonMono  = 0;
        for (int& v : g_clkDeltaHist) v = 0;
        g_clkDeltaBig = 0;
    }

    // Ungated lifetime hit counts for the two inlined functions. Kept so that
    // "never called" stays distinguishable from "the detour never ran" -- that
    // ambiguity made the first run of this experiment uninterpretable despite
    // Geode reporting both hooks enabled.
    long g_gmdHits = 0;
    long g_pcHits  = 0;

    int  g_updCalls     = 0;
    long g_stepAdv      = 0;   // total m_currentStep advance across the attempt
    int  g_stepHist[17] = {};  // ticks consumed per update call, 0..16
    int  g_stepHistBig  = 0;
    int  g_updTraced    = 0;

    void resetExpStats() {
        g_updCalls = 0;
        g_stepAdv  = 0;
        for (int& v : g_stepHist) v = 0;
        g_stepHistBig = 0;
    }
}

class $modify(GDRLExpBaseGameLayer, GJBaseGameLayer) {
    // Retained purely as evidence that these are never reached in gameplay.
    double getModifiedDelta(float dt) {
        g_gmdHits++;
        return GJBaseGameLayer::getModifiedDelta(dt);
    }

    void processCommands(float dt, bool isHalfTick, bool isLastTick) {
        g_pcHits++;
        GJBaseGameLayer::processCommands(dt, isHalfTick, isLastTick);
    }

    // m_currentStep is snapshotted around the original so the histogram answers
    // "how many physics ticks did this call consume" directly rather than by
    // inference from timings. It answered something else instead: the counter
    // never moves at all. See README.
    void update(float dt) {
        if (!g_expOn) { GJBaseGameLayer::update(dt); return; }

        const int   before = m_currentStep;
        const float useDt  = g_deltaTicks > 0
                                 ? static_cast<float>(g_deltaTicks * kTimestep)
                                 : dt;

        GJBaseGameLayer::update(useDt);

        const int d = m_currentStep - before;
        g_updCalls++;
        g_stepAdv += d;
        if (d >= 0 && d <= 16) g_stepHist[d]++;
        else                   g_stepHistBig++;

        if (g_updTraced < 20) {
            g_updTraced++;
            log::info("[gdrl] UPD #{:<4} dtIn={:.9f} dtUsed={:.9f} "
                      "step {}->{} (ticks={})",
                      g_updCalls, dt, useDt, before, m_currentStep, d);
        }

        // Clock sampling, after delegating so it reflects the tick(s) this frame
        // actually simulated.
        if (auto* pl = PlayLayer::get()) {
            const double t     = pl->m_attemptTime;
            const double ticks = t * kTicksPerSec;
            const double resid = std::fabs(ticks - std::nearbyint(ticks));
            const long   tick  = std::lround(ticks);

            g_clkMaxResid = std::max(g_clkMaxResid, resid);
            g_clkFrames++;

            if (g_clkLastTick >= 0) {
                const long d = tick - g_clkLastTick;
                if (d < 0) g_clkNonMono++;
                if (d >= 0 && d < 40) g_clkDeltaHist[d]++;
                else                  g_clkDeltaBig++;
            }
            g_clkLastTick = tick;
            g_clkFinal    = tick;

            if (g_clkTraced < 24) {
                g_clkTraced++;
                log::info("[gdrl] CLK t={:.12f} t*240={:.9f} tick={} resid={:.3e}",
                          t, ticks, tick, resid);
            }
        }

        // Skip the post-death wait. Done after delegating so the attempt's final
        // frame is fully simulated before the reset is forced.
        if (g_fastReset) {
            if (auto* pl = PlayLayer::get(); pl && pl->m_inResetDelay) {
                g_fastResets++;
                pl->resetLevel();
            }
        }
    }
};

class $modify(GDRLExpPlayLayer, PlayLayer) {
    void resetLevel() {
        if (g_expOn) {
            log::info("[gdrl] PCHITS gmd={} pc={} upd={} fastResets={}",
                      g_gmdHits, g_pcHits, g_updCalls, g_fastResets);

            if (g_updCalls > 0) {
                std::string hist;
                for (int i = 0; i <= 16; i++) {
                    if (g_stepHist[i]) {
                        hist += fmt::format("{}:{} ", i, g_stepHist[i]);
                    }
                }
                if (g_stepHistBig) hist += fmt::format(">16:{}", g_stepHistBig);
                log::info("[gdrl] UPDSUM calls={:<5} stepAdv={:<6} "
                          "ticksPerCall[{}] deltaTicks={}",
                          g_updCalls, g_stepAdv, hist, g_deltaTicks);
            }
        }

        if (g_expOn && g_clkFrames > 0) {
            std::string dh;
            for (int i = 0; i < 40; i++) {
                if (g_clkDeltaHist[i]) dh += fmt::format("{}:{} ", i, g_clkDeltaHist[i]);
            }
            if (g_clkDeltaBig) dh += fmt::format(">=40:{}", g_clkDeltaBig);
            log::info("[gdrl] CLKSUM finalTick={:<6} frames={:<5} maxResid={:.3e} "
                      "nonMono={} tickDeltas[{}]",
                      g_clkFinal, g_clkFrames, g_clkMaxResid, g_clkNonMono, dh);
        }

        PlayLayer::resetLevel();
        resetExpStats();
        resetClockStats();
    }
};

$execute {
    if (g_expOn) {
        log::info("[gdrl] EXP instrumentation ON (GDRL_DELTA_TICKS={} "
                  "GDRL_FAST_RESET={})", g_deltaTicks, (int)g_fastReset);
    }
}
