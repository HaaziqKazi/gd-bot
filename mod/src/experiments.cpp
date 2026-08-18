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
#include <climits>
#include <string>
#include <vector>

using namespace geode::prelude;

// Defined in main.cpp. Marks input this file injected deliberately, so the
// null-input guard there passes it through instead of counting it as a leak.
extern bool g_gdrlInjecting;
extern long g_gdrlInjectPending;

// The attempt outcome, also from main.cpp, so the SEQ line below is a complete
// self-checking record: sequence, outcome and input verdict on one line.
extern float g_gdrlMaxX;
extern long  g_gdrlBlocked;
extern long  g_gdrlLeaked;
extern int   g_gdrlLevelID;
extern long  g_gdrlUiEvents;
extern long  g_gdrlUiTotal;
extern void  gdrlSnapshotMaxX();

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

    // ---------------------------------------------------------------------
    // Experiment 5: actually inject an input.
    //
    // Everything measured so far has been the null-input trajectory -- the cube
    // runs into the first spike at x=507.6 and dies at tick 391, ~550 times.
    // That establishes the environment is deterministic; it does NOT establish
    // that GD is a forward model, which is what search-and-replay needs. That
    // requires a *given* input sequence to replay identically, and no input has
    // ever been injected.
    //
    // Injection goes through queueButton (10 live call sites, the 4-arg form
    // with a double timestamp), flagged via g_gdrlInjecting so the null-input
    // guard lets it past while still blocking stray keypresses.
    //
    // GDRL_INJECT_SPAN sweeps the tick across attempts rather than fixing it, so
    // one launch scans a window instead of one value -- the jump window that
    // clears the spike is not known ahead of time and guessing it one process
    // launch at a time is slow.
    const int g_injectTick = envInt("GDRL_INJECT_TICK", -1);
    const int g_injectHold = envInt("GDRL_INJECT_HOLD", 8);
    const int g_injectSpan = envInt("GDRL_INJECT_SPAN", 1);

    long g_expAttempt   = 0;
    bool g_injPushed    = false;
    bool g_injReleased  = false;
    long g_injTickUsed  = -1;

    // ---------------------------------------------------------------------
    // Experiment 6: multi-input sequences.
    //
    // One jump proves placement is tick-exact. It does not prove GD replays a
    // *sequence* -- the thing search-and-replay actually needs -- because with a
    // single input there is no opportunity for error to compound. Divergence in
    // a forward model shows up as drift between inputs, so the test only starts
    // being informative at n >= 2.
    //
    // GDRL_INJECT_SEQ="325,410:12,455"  jump ticks, optional :hold in ticks.
    //   The button is pushed on the frame whose START tick equals the entry and
    //   released `hold` ticks later. Hold defaults to GDRL_INJECT_HOLD.
    //
    // GDRL_SWEEP_START/SPAN/STEP appends one more jump whose tick walks across
    // attempts: tick = START + (attempt % SPAN) * STEP. That is the greedy
    // search step -- fix the prefix that is known to work, scan for the next
    // jump. Cycling rather than stopping at SPAN is deliberate: every value gets
    // revisited, so a sweep doubles as a repeat-determinism check for free.
    //
    // GDRL_PERTURB_IDX/DELTAS shifts ONE element of the fixed sequence by a
    // per-attempt delta drawn cyclically from the list. Interleaving -1/0/+1
    // within a single process is stronger than three separate launches: any
    // drift that is a function of process state cancels.
    struct SeqItem { long push; long release; };

    std::vector<SeqItem> g_seqBase;      // parsed from GDRL_INJECT_SEQ
    std::vector<SeqItem> g_seq;          // this attempt's sequence (perturbed/swept)
    std::vector<char>    g_seqFired;
    std::vector<char>    g_seqReleased;
    long g_seqPushes  = 0;               // pushes actually queued this attempt
    long g_seqRels    = 0;
    long g_seqSwept   = -1;              // the swept tick used this attempt

    const int  g_sweepStart = envInt("GDRL_SWEEP_START", -1);
    const int  g_sweepSpan  = envInt("GDRL_SWEEP_SPAN", 1);
    const int  g_sweepStep  = envInt("GDRL_SWEEP_STEP", 1);
    const int  g_sweepHold  = envInt("GDRL_SWEEP_HOLD", envInt("GDRL_INJECT_HOLD", 8));
    const int  g_perturbIdx = envInt("GDRL_PERTURB_IDX", -1);

    // Adaptive dt: run coarse between events, land exactly on each event tick.
    //
    // Tick-exact injection needs the frame boundary to coincide with the target
    // tick, which at a fixed dt means dt = 1/240 -- and a 700-tick attempt then
    // costs 700 rendered frames. Since the event ticks are known in advance, the
    // frame size can instead be chosen per frame as the distance to the next
    // event, capped at GDRL_DELTA_TICKS. Every frame still consumes an exact
    // whole number of 1/240 steps, and every event still lands on a frame
    // boundary. Whether that is *equivalent* to a uniform 1/240 is an empirical
    // question and is checked against the known single-jump outcome, not assumed.
    const bool g_adaptive = envInt("GDRL_ADAPTIVE", 0) == 1;

    std::vector<int> g_perturbDeltas;

    std::vector<long> parseLongList(const char* env) {
        std::vector<long> out;
        const char* v = std::getenv(env);
        if (!v || !*v) return out;
        const char* p = v;
        while (*p) {
            char* end = nullptr;
            const long n = std::strtol(p, &end, 10);
            if (end == p) { p++; continue; }
            out.push_back(n);
            p = end;
        }
        return out;
    }

    // "325,410:12,455" -> {{325,333},{410,422},{455,463}} with hold 8.
    void parseSeq() {
        const char* v = std::getenv("GDRL_INJECT_SEQ");
        if (!v || !*v) return;
        const char* p = v;
        while (*p) {
            char* end = nullptr;
            const long push = std::strtol(p, &end, 10);
            if (end == p) { p++; continue; }
            p = end;
            long hold = g_injectHold;
            if (*p == ':') {
                p++;
                char* e2 = nullptr;
                const long h = std::strtol(p, &e2, 10);
                if (e2 != p) { hold = h; p = e2; }
            }
            g_seqBase.push_back({push, push + hold});
        }
    }

    const bool g_seqMode = [] {
        parseSeq();
        g_perturbDeltas = [] {
            auto v = parseLongList("GDRL_PERTURB_DELTAS");
            std::vector<int> out;
            for (long n : v) out.push_back((int)n);
            if (out.empty()) out = {-1, 0, 1};
            return out;
        }();
        return !g_seqBase.empty() || envInt("GDRL_SWEEP_START", -1) >= 0;
    }();

    std::string seqToString(const std::vector<SeqItem>& s) {
        std::string out;
        for (size_t i = 0; i < s.size(); i++) {
            if (i) out += ",";
            out += fmt::format("{}:{}", s[i].push, s[i].release - s[i].push);
        }
        return out;
    }

    // Build the sequence for the attempt about to start. Called at the attempt
    // boundary so the whole attempt uses one fixed plan -- deciding per frame
    // would make the plan a function of the frame rate.
    void buildSeqForAttempt(long attempt) {
        g_seq       = g_seqBase;
        g_seqSwept  = -1;

        if (g_perturbIdx >= 0 && g_perturbIdx < (int)g_seq.size()
            && !g_perturbDeltas.empty()) {
            const int d = g_perturbDeltas[attempt % (long)g_perturbDeltas.size()];
            g_seq[g_perturbIdx].push    += d;
            g_seq[g_perturbIdx].release += d;
        }

        if (g_sweepStart >= 0) {
            const long span = g_sweepSpan > 0 ? g_sweepSpan : 1;
            g_seqSwept = g_sweepStart + (attempt % span) * g_sweepStep;
            g_seq.push_back({g_seqSwept, g_seqSwept + g_sweepHold});
        }

        g_seqFired.assign(g_seq.size(), 0);
        g_seqReleased.assign(g_seq.size(), 0);
        g_seqPushes = 0;
        g_seqRels   = 0;
    }

    // Next tick at which anything must happen, or LONG_MAX when the sequence is
    // exhausted. Drives the adaptive frame size.
    long nextEventTick() {
        long best = LONG_MAX;
        for (size_t i = 0; i < g_seq.size(); i++) {
            if (!g_seqFired[i])         best = std::min(best, g_seq[i].push);
            else if (!g_seqReleased[i]) best = std::min(best, g_seq[i].release);
        }
        return best;
    }

    void resetInjectState() {
        g_injPushed   = false;
        g_injReleased = false;
        g_injTickUsed = -1;
    }

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

        // Inject before delegating: queueButton only enqueues, and the queue is
        // drained by processQueuedButtons inside update(). Queuing after the
        // original would push the input a whole frame late.
        long curTick = -1;
        if (g_seqMode) {
            if (auto* pl = PlayLayer::get()) {
                curTick = std::lround(pl->m_attemptTime * kTicksPerSec);
                for (size_t i = 0; i < g_seq.size(); i++) {
                    if (!g_seqFired[i] && curTick >= g_seq[i].push) {
                        g_gdrlInjecting = true;
                        this->queueButton(1, true, false, pl->m_attemptTime);
                        g_gdrlInjecting = false;
                        g_seqFired[i] = 1;
                        g_seqPushes++;
                    } else if (g_seqFired[i] && !g_seqReleased[i]
                               && curTick >= g_seq[i].release) {
                        g_gdrlInjecting = true;
                        this->queueButton(1, false, false, pl->m_attemptTime);
                        g_gdrlInjecting = false;
                        g_seqReleased[i] = 1;
                        g_seqRels++;
                    }
                }
            }
        }

        if (g_injectTick >= 0) {
            if (auto* pl = PlayLayer::get()) {
                const long tick   = std::lround(pl->m_attemptTime * kTicksPerSec);
                const long target = g_injectTick
                                  + (g_injectSpan > 1 ? g_expAttempt % g_injectSpan : 0);

                if (!g_injPushed && tick >= target) {
                    g_gdrlInjecting = true;
                    this->queueButton(1, true, false, pl->m_attemptTime);
                    g_gdrlInjecting = false;
                    g_injPushed   = true;
                    g_injTickUsed = tick;
                } else if (g_injPushed && !g_injReleased
                           && tick >= g_injTickUsed + g_injectHold) {
                    g_gdrlInjecting = true;
                    this->queueButton(1, false, false, pl->m_attemptTime);
                    g_gdrlInjecting = false;
                    g_injReleased = true;
                }
            }
        }

        const int before = m_currentStep;

        // Frame size. Adaptive mode shrinks it to land exactly on the next event
        // tick; between events it runs at the full GDRL_DELTA_TICKS.
        int ticksThisFrame = g_deltaTicks;
        if (g_adaptive && g_deltaTicks > 0 && curTick >= 0) {
            const long ev = nextEventTick();
            if (ev != LONG_MAX) {
                const long gap = ev - curTick;
                ticksThisFrame = gap <= 0 ? 1
                                          : (int)std::min<long>(gap, g_deltaTicks);
            }
        }
        const float useDt = ticksThisFrame > 0
                                ? static_cast<float>(ticksThisFrame * kTimestep)
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

        if (g_expOn && g_injectTick >= 0) {
            log::info("[gdrl] INJECT tick={} hold={} pushed={} released={} pending={}",
                      g_injTickUsed, g_injectHold, (int)g_injPushed,
                      (int)g_injReleased, g_gdrlInjectPending);
        }

        // The sequence outcome, as ONE self-contained record: what was played,
        // where it ended, and whether the attempt is admissible at all. Printed
        // at full precision because the claim under test is bit-identity, not
        // proximity. leaked>0 voids the attempt exactly as it does for the
        // null-input measurements.
        if (g_expOn && g_seqMode && g_updCalls > 0) {
            gdrlSnapshotMaxX();
            const double t     = this->m_attemptTime;
            const long   dtick = std::lround(t * kTicksPerSec);
            log::info("[gdrl] SEQ a={:<4} lvl={} n={} swept={:<5} maxX={:.9f} "
                      "deathTick={:<6} t={:.9f} push={} rel={} "
                      "input[{} blocked={} leaked={} ui={} uiTot={}] seq=[{}]",
                      g_expAttempt, g_gdrlLevelID, (int)g_seq.size(), g_seqSwept,
                      g_gdrlMaxX, dtick, t, g_seqPushes, g_seqRels,
                      g_gdrlLeaked > 0 ? "INVALID" : "clean",
                      g_gdrlBlocked, g_gdrlLeaked, g_gdrlUiEvents, g_gdrlUiTotal,
                      seqToString(g_seq));
        }
        g_expAttempt++;

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
        resetInjectState();
        if (g_seqMode) buildSeqForAttempt(g_expAttempt);
    }
};

// Provenance stamp -- TODO #23, unconditional (see telemetry.cpp's copy of
// this comment for why "off" must be logged, not just "on"). These are the
// same consts EXPBaseGameLayer::update branches on, read here rather than
// re-fetched from getenv.
$execute {
    log::info("[gdrl] STAMP experiments GDRL_EXP={} GDRL_DELTA_TICKS={} "
              "GDRL_FAST_RESET={} GDRL_ADAPTIVE={} GDRL_INJECT_TICK={} "
              "GDRL_INJECT_HOLD={} GDRL_INJECT_SPAN={} GDRL_INJECT_SEQ_ITEMS={} "
              "GDRL_SWEEP_START={} GDRL_SWEEP_SPAN={} GDRL_SWEEP_STEP={} "
              "GDRL_SWEEP_HOLD={} GDRL_PERTURB_IDX={} GDRL_PERTURB_DELTAS_N={}",
              (int)g_expOn, g_deltaTicks, (int)g_fastReset, (int)g_adaptive,
              g_injectTick, g_injectHold, g_injectSpan, (int)g_seqBase.size(),
              g_sweepStart, g_sweepSpan, g_sweepStep, g_sweepHold, g_perturbIdx,
              (int)g_perturbDeltas.size());
}

$execute {
    if (g_expOn) {
        log::info("[gdrl] EXP instrumentation ON (GDRL_DELTA_TICKS={} "
                  "GDRL_FAST_RESET={} GDRL_ADAPTIVE={})",
                  g_deltaTicks, (int)g_fastReset, (int)g_adaptive);
    }
    if (g_expOn && g_seqMode) {
        log::info("[gdrl] SEQCFG base=[{}] sweep[start={} span={} step={} hold={}] "
                  "perturbIdx={} adaptive={}",
                  seqToString(g_seqBase), g_sweepStart, g_sweepSpan, g_sweepStep,
                  g_sweepHold, g_perturbIdx, (int)g_adaptive);
    }
}
