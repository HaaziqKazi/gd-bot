// The GDRL environment transport: shared memory, a synchronous step protocol,
// and tick-exact action placement.
//
// -------------------------------------------------------------------------
// WHY THIS FILE EXISTS
// -------------------------------------------------------------------------
//
// Everything before this printed text log lines. A plain-text log is not a
// training transport: a few hundred objects x ~80 bytes each, at 240 Hz, is a
// data channel that happens to be formatted for humans, and the Python side has
// no way to *answer*. conditioning.py and trajectory.py were pure nn.Modules
// with no data source and nothing connecting them to the game. This file is the
// connection.
//
// -------------------------------------------------------------------------
// EMISSION POINT: prepareMoveActions, NOT update
// -------------------------------------------------------------------------
//
// GJBaseGameLayer::update is per-RENDER-FRAME. Sampling a per-tick quantity
// once per frame is exactly the bug that made maxX a function of the frame rate
// (README, "Two probe bugs worth not repeating"): it agreed with itself
// perfectly for hundreds of attempts, right up until the respawn animation was
// skipped, and then silently became a different measurement.
//
// GJEffectManager::prepareMoveActions(float dt, bool) runs per PHYSICS STEP.
// Verified from the shipped arm64 slice rather than assumed -- it has exactly
// two bl call sites:
//
//   0x10012086c   inside GJBaseGameLayer::loadUpToPosition  (level SEEK path)
//   0x1001233e0   inside GJBaseGameLayer::update at +0x9f8
//
// and update's fixed-step loop is: latch at 0x122e98 (add w22,w22,#1; cmp
// w22,w20; b.ge 0x123d9c), body from 0x122ea4, back-edges at 0x123d4c /
// 0x123d5c / 0x123d94, exit 0x123d9c. 0x1001233e0 sits inside [0x122ea4,
// 0x123d94], so the gameplay call is inside the loop. One call per step.
//
// The seek call has to be excluded, and PlayLayer::get() != nullptr does NOT
// exclude it -- loadUpToPosition runs with a live PlayLayer on the checkpoint
// seek path. g_inGameplayUpdate below is set around the delegate inside our own
// update hook, which is precise: only the update-side call can observe it set.
//
// -------------------------------------------------------------------------
// THE ORDERING RESULT THAT MAKES TICK-EXACT PLACEMENT WORK AT LARGE dt
// -------------------------------------------------------------------------
//
// GJBaseGameLayer::processQueuedButtons is called at update+0x7b8 = 0x1001231a0
// -- also INSIDE the step loop, and 0x240 bytes BEFORE prepareMoveActions in
// the same iteration. That is a new result and it changes the design:
//
//   * The button queue is drained once per PHYSICS STEP, not once per render
//     frame. experiments.cpp queues from update() before delegating, which
//     means every input it places lands on the frame's FIRST step -- which is
//     why its 8-tick placement granularity at dt=8/240 was "a sampling artifact
//     of injecting on frame boundaries" and why it needed GDRL_ADAPTIVE to get
//     tick resolution back.
//   * Queuing from this per-step hook instead removes that entirely. dt can
//     stay large (few render frames, high throughput) while an action still
//     lands on any chosen tick.
//   * Because processQueuedButtons precedes prepareMoveActions within one
//     iteration, a button queued from here is drained by the NEXT step. So an
//     action for targetTick is queued at the step whose tick is targetTick - 1.
//
// That one-step offset is arithmetic on a verified instruction order, but the
// claim that the observed tick advances by exactly one per prepareMoveActions
// call is NOT verified -- it depends on where GD updates m_attemptTime within
// the step, which was not established. See UNVERIFIED (2) at the bottom, and
// note that header.stepIndex is emitted precisely so this shows up in the data
// instead of being assumed.
//
// -------------------------------------------------------------------------
// TRANSPORT: POSIX shared memory + atomics, no OS synchronisation primitives
// -------------------------------------------------------------------------
//
// GD has no App Sandbox entitlement (README, "Environment facts"), so
// shm_open/mmap works from inside the process with no entitlement work.
//
// The synchronisation is deliberately built from atomics and a bounded poll
// rather than from a semaphore or a shared mutex, for two reasons:
//
//   1. macOS does not implement unnamed POSIX semaphores in shared memory
//      (sem_init returns ENOSYS), and a PTHREAD_PROCESS_SHARED condvar is not
//      something to bet a determinism result on.
//   2. The wait MUST be bounded and the timeout MUST be countable. That is
//      trivial with a poll loop and awkward with a blocking primitive. A silent
//      timeout that fell back to "no input" would look exactly like a policy
//      that chose not to jump, which is the single most dangerous failure this
//      channel can have.
//
// Atomic access goes through the __atomic_* builtins rather than std::atomic_ref
// (libc++ support for atomic_ref is too recent to rely on at deployment target
// 11.0) and rather than reinterpret_cast to std::atomic<T>* (type punning a POD
// wire field). The builtins are available on every clang that can build this.
//
// -------------------------------------------------------------------------
// DETERMINISM: OFF BY DEFAULT, AND OFF MEANS OFF
// -------------------------------------------------------------------------
//
// ~550 attempts of determinism evidence rest on the mod's behaviour being
// reproducible. With GDRL_ENV unset, every hook in this file delegates on its
// first statement and touches nothing -- no allocation, no reads of game state,
// no shared memory. That is not a nicety; every existing measurement has to
// remain reproducible after this file lands.
//
// WHICH GUARANTEE THE 2026-08-15 CAMERA-WINDOW CHANGE TOUCHES, precisely,
// because there are two and they are easy to conflate:
//
//   * "GDRL_* unset == unmodded run" -- NOT touched. scanObjects is reachable
//     only from fillObservation, which is reachable only from stepProtocol,
//     which runs only under g_envOn. With GDRL_ENV unset nothing below reads
//     the camera, and gdrl::cameraWorldRect() is never called from this file.
//     The viewport probe's own hooks are separately gated on
//     GDRL_PROBE_VIEWPORT.
//   * "GDRL_ENV=1 perturbs nothing" -- WEAKENED, and it was always an
//     UNVERIFIED claim (see (4) at the bottom of this file). Under GDRL_ENV
//     the scan now additionally walks m_objectLayer's parent chain and calls
//     nodeToWorldTransform() once per physics step. viewport.cpp restores
//     every cocos cache field around that call, so it is a pure read by
//     construction -- but by construction is not by measurement. New
//     UNVERIFIED (7), with the GDRL_VERIFY_XFORM run that settles it.

#include <Geode/Geode.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/GJBaseGameLayer.hpp>
#include <Geode/modify/GJEffectManager.hpp>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

#include "gdrl_schema.hpp"
#include "telemetry.hpp"
#include "viewport.hpp"

using namespace geode::prelude;

// Defined in main.cpp. Marks input we injected deliberately so the null-input
// guard passes it through instead of counting it as a leak. There is exactly
// ONE injection route in this project on purpose -- the leak detector's whole
// premise is that queueButton with g_gdrlInjecting set is ours and everything
// else is a stray keypress. Do not add a second.
extern bool g_gdrlInjecting;
extern long g_gdrlInjectPending;

// Attempt bookkeeping owned by main.cpp.
extern long g_gdrlBlocked;
extern long g_gdrlLeaked;
extern long g_gdrlUiEvents;
extern int  g_gdrlLevelID;

namespace gdrl {

long g_gdrlEnvSteps    = 0;
long g_gdrlEnvTimeouts = 0;
long g_gdrlEnvProtoErr = 0;

}  // namespace gdrl

namespace {

// ---------------------------------------------------------------------------
// Configuration, read once.
//
// getenv on every physics step is a linear scan of environ and is not
// thread-safe against setenv. main.cpp already establishes this convention.
// ---------------------------------------------------------------------------

bool envFlag(const char* name) {
    const char* v = std::getenv(name);
    return v && *v == '1';
}

int envInt(const char* name, int fallback) {
    const char* v = std::getenv(name);
    if (!v || !*v) return fallback;
    return std::atoi(v);
}

const char* envStr(const char* name, const char* fallback) {
    const char* v = std::getenv(name);
    return (v && *v) ? v : fallback;
}

const bool  g_envOn       = envFlag("GDRL_ENV");
const char* g_shmSuffix   = envStr("GDRL_ENV_NAME", "gdrl.env");
const int   g_waitBudget  = envInt("GDRL_ENV_WAIT_US", 250000);   // 250 ms
const int   g_envDeltaT   = envInt("GDRL_ENV_DELTA_TICKS", 0);    // 0 = passthrough

// ---------------------------------------------------------------------------
// Observation window OVERRIDES. Unset -- the default -- means the window is
// taken from the LIVE CAMERA every physics step (gdrl::cameraWorldRect; see
// scanObjects). These exist only so an ablation or the Benchmark B oracle can
// pin an axis to a constant.
//
// THESE USED TO BE THE WINDOW, at 400 / 1400 / 600, and those numbers were
// picked for convenience before Benchmark A existed -- sized to cover
// TrajectoryRaster's 48x32 cells at 30 units/cell rather than to match a
// screen. Measured 2026-08-15 the screen shows 359.5 ahead, 209.5 behind, and
// +215 / -105 vertically, so the old constants were 3.9x too wide ahead and
// 3.75x too tall, and symmetric where the real field is not.
//
// **SETTING ANY OF THESE MAKES THE RUN NOT A BENCHMARK A RUN.** Under A the
// sensor definition IS the benchmark: a wider-than-screen window is lookahead
// the player does not have, and a narrower one is a different (harder)
// benchmark. Either way the result is not comparable with a camera-derived
// run. The values are visible on the wire -- windowMinX/MaxX/MinY/MaxY are
// whatever was actually used -- so Python sees the geometry, but it cannot see
// the INTENT, which is why this is stated here and logged once at first scan.
//
// Each axis is independent: setting _AHEAD alone leaves behind and vertical
// camera-derived. If and only if all three are set is the camera not needed at
// all, and only then does an unreadable camera stop being fatal.
//
// Semantics when set: behind/ahead are distances from the player (minX =
// px - behind, maxX = px + ahead) and _VERT is a HALF-extent applied
// symmetrically (minY = py - vert, maxY = py + vert), all exactly as before,
// so an old ablation command line still means what it used to mean.
// ---------------------------------------------------------------------------
bool envHas(const char* name) {
    const char* v = std::getenv(name);
    return v && *v;
}

const bool g_winBehindSet = envHas("GDRL_ENV_WIN_BEHIND");
const bool g_winAheadSet  = envHas("GDRL_ENV_WIN_AHEAD");
const bool g_winVertSet   = envHas("GDRL_ENV_WIN_VERT");

// No meaningful fallback: these are read only when the corresponding *Set flag
// is true. The 0 is deliberately NOT the old default -- a stale 400 sitting
// here is exactly the kind of value that comes back to life during a later
// refactor and silently reinstates a non-A sensor.
const int g_winBehind = envInt("GDRL_ENV_WIN_BEHIND", 0);
const int g_winAhead  = envInt("GDRL_ENV_WIN_AHEAD", 0);
const int g_winVert   = envInt("GDRL_ENV_WIN_VERT", 0);

const bool g_winAnyOverride = g_winBehindSet || g_winAheadSet || g_winVertSet;
const bool g_winAllOverride = g_winBehindSet && g_winAheadSet && g_winVertSet;

// These duplicate switches that live in main.cpp's anonymous namespace. They
// are re-read rather than shared because main.cpp is owned elsewhere; the cost
// is that the two could disagree if one of them were ever renamed, so the
// values are also *emitted* on every observation (validity.blockInput,
// validity.levelPinned) where Python can see them rather than assume them.
const bool g_blockInput = envFlag("GDRL_BLOCK_INPUT");
const bool g_pinLevel   = envFlag("GDRL_PIN_LEVEL");
const int  g_pinnedID   = 1;   // Stereo Madness; matches main.cpp

// How many consecutive expired waits before the mod declares Python gone and
// resumes free-running. One timeout is a slow step; three in a row is a dead
// peer. Without this a crashed trainer would cost 250 ms of stall on every
// physics step forever, which is a hang in everything but name.
constexpr int kDetachAfterTimeouts = 3;

// ---------------------------------------------------------------------------
// Atomic helpers over the POD wire fields.
// ---------------------------------------------------------------------------

inline uint64_t loadAcquire(const uint64_t* p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}
inline void storeRelease(uint64_t* p, uint64_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}
inline uint32_t loadAcquire32(const uint32_t* p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}
inline void storeRelease32(uint32_t* p, uint32_t v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

// ---------------------------------------------------------------------------
// The shared segment.
// ---------------------------------------------------------------------------

GdrlShared* g_shm      = nullptr;
char        g_shmName[64] = {};
uint64_t    g_obsSeq   = 0;      // last completed (even) observation sequence
int         g_consecutiveTimeouts = 0;

// Per-attempt / per-step state.
bool  g_inGameplayUpdate = false;
long  g_stepIndex        = 0;    // physics steps this attempt, counted by us
long  g_frame            = 0;    // render frames this attempt
long  g_attempt          = 0;
long  g_stepsUntilPublish = 0;   // countdown driven by the action block
bool  g_wasAttached      = false; // edge-detects Python attaching mid-attempt

float g_dtIn   = 0.f;
float g_dtUsed = 0.f;

// Scheduled inputs, keyed by physics tick. Fixed capacity: a variable-length
// container here would allocate on the game thread inside the step loop.
struct Scheduled {
    long tick   = 0;
    int  button = 1;
    bool push   = false;
    bool player2 = false;
    bool live   = false;
};
constexpr int kMaxScheduled = 64;
Scheduled g_sched[kMaxScheduled];

void clearSchedule() {
    for (auto& s : g_sched) s.live = false;
}

bool schedule(long tick, int button, bool push, bool player2) {
    for (auto& s : g_sched) {
        if (s.live) continue;
        s = Scheduled{tick, button, push, player2, true};
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Segment lifetime.
//
// The segment is unlinked and recreated on every launch. A GD that crashed
// leaves the object behind, and reusing it would hand a fresh process a control
// block full of a dead process's counters -- which would read as a working
// channel. O_EXCL after an unlink makes "someone else already owns this name"
// an error rather than a silent adoption.
// ---------------------------------------------------------------------------

bool openSegment() {
    // macOS caps POSIX shm names at 31 bytes including the leading slash
    // (PSHMNAMLEN). Longer names fail with ENAMETOOLONG, which is a confusing
    // error to debug from the Python side, so it is caught here.
    const int n = std::snprintf(g_shmName, sizeof(g_shmName), "/%s", g_shmSuffix);
    if (n <= 0 || n > 31) {
        log::error("[gdrl] ENV shm name '{}' is {} bytes; macOS allows 31 including "
                   "the leading slash. Set GDRL_ENV_NAME shorter.", g_shmName, n);
        return false;
    }

    shm_unlink(g_shmName);
    const int fd = shm_open(g_shmName, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0) {
        log::error("[gdrl] ENV shm_open('{}') failed: {}", g_shmName, std::strerror(errno));
        return false;
    }
    if (ftruncate(fd, (off_t)sizeof(GdrlShared)) != 0) {
        log::error("[gdrl] ENV ftruncate({}) failed: {}",
                   sizeof(GdrlShared), std::strerror(errno));
        close(fd);
        shm_unlink(g_shmName);
        return false;
    }
    void* p = mmap(nullptr, sizeof(GdrlShared), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);   // the mapping keeps the object alive; the fd is not needed
    if (p == MAP_FAILED) {
        log::error("[gdrl] ENV mmap failed: {}", std::strerror(errno));
        shm_unlink(g_shmName);
        return false;
    }

    g_shm = static_cast<GdrlShared*>(p);
    std::memset(g_shm, 0, sizeof(GdrlShared));

    auto& c = g_shm->control;
    c.obsOffset    = (uint32_t)offsetof(GdrlShared, obs);
    c.obsSize      = (uint32_t)sizeof(GdrlObservation);
    c.actOffset    = (uint32_t)offsetof(GdrlShared, action);
    c.actSize      = (uint32_t)sizeof(GdrlActionBlock);
    c.totalSize    = (uint32_t)sizeof(GdrlShared);
    c.maxObjects   = GDRL_MAX_OBJECTS;
    c.maxCommands  = GDRL_MAX_COMMANDS;
    c.maxPending   = GDRL_MAX_PENDING;
    c.maxSpeedSegs = GDRL_MAX_SPEED_SEGS;
    c.maxActions   = GDRL_MAX_ACTIONS;
    c.coverageCols = GDRL_COVERAGE_COLS;
    c.tickHz       = GDRL_TICK_HZ;
    c.modPid       = (uint32_t)getpid();
    c.waitBudgetUs = (uint32_t)g_waitBudget;
    c.headerSize   = (uint16_t)sizeof(GdrlControl);
    c.schemaHash   = GDRL_SCHEMA_HASH;
    c.wireVersion  = (uint16_t)GDRL_WIRE_VERSION;

    // magic LAST, with release ordering. Everything above it must be visible to
    // a reader that has seen the magic; publishing the magic first would let a
    // Python that polls for it map a control block whose sizes are still zero.
    storeRelease32(&c.magic, GDRL_MAGIC);
    storeRelease32(&c.modAlive, 1u);

    log::info("[gdrl] ENV segment '{}' ready: {} bytes, wire v{}, schema 0x{:016X}, "
              "pid {}, waitBudget {}us",
              g_shmName, sizeof(GdrlShared), GDRL_WIRE_VERSION,
              (unsigned long long)GDRL_SCHEMA_HASH, getpid(), g_waitBudget);
    return true;
}

// Torn down through a static destructor rather than a Geode unload callback:
// a destructor is guaranteed by the language, needs no Geode API surface, and
// runs on the normal-exit path where leaving modAlive=1 behind would tell the
// next Python that a dead process is still serving.
struct SegmentCloser {
    ~SegmentCloser() {
        if (!g_shm) return;
        storeRelease32(&g_shm->control.modAlive, 0u);
        munmap(g_shm, sizeof(GdrlShared));
        g_shm = nullptr;
        shm_unlink(g_shmName);
    }
} g_segmentCloser;

// ---------------------------------------------------------------------------
// The step protocol.
// ---------------------------------------------------------------------------

bool pythonAttached() {
    return g_shm && loadAcquire32(&g_shm->control.pyAttached) != 0;
}

// Wait for Python to answer observation `seq`. Bounded, and every expiry is
// counted -- both per attempt (for the ENV log line) and lifetime (in the
// control block, where Python can assert it is zero before believing a run).
//
// Returns true if an answer arrived. False means "act as if no action was
// requested", which is a legitimate outcome and a suspicious one, and the
// counters are what keep those two readings apart.
bool awaitAction(uint64_t seq) {
    const auto start = std::chrono::steady_clock::now();
    for (;;) {
        if (loadAcquire(&g_shm->control.actSeq) == seq) {
            g_consecutiveTimeouts = 0;
            return true;
        }
        // A clean detach mid-wait is not a timeout. Python setting pyAttached=0
        // is an explicit "stop waiting for me"; counting it as a timeout would
        // put a spurious INVALID-shaped signal on an otherwise fine attempt.
        if (loadAcquire32(&g_shm->control.pyAttached) == 0) return false;

        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - start).count();
        if (elapsed >= g_waitBudget) {
            gdrl::g_gdrlEnvTimeouts++;
            g_consecutiveTimeouts++;
            __atomic_fetch_add(&g_shm->control.stepTimeouts, 1ull, __ATOMIC_RELAXED);
            log::error("[gdrl] ENV action wait expired after {}us at seq={} "
                       "(consecutive={}). This step ran with NO action -- which is "
                       "indistinguishable from a policy that chose not to act, so "
                       "the attempt is not usable as evidence.",
                       (long long)elapsed, (unsigned long long)seq,
                       g_consecutiveTimeouts);
            if (g_consecutiveTimeouts >= kDetachAfterTimeouts) {
                storeRelease32(&g_shm->control.pyAttached, 0u);
                __atomic_fetch_add(&g_shm->control.detachedByTimeout, 1u, __ATOMIC_RELAXED);
                log::error("[gdrl] ENV giving up on Python after {} consecutive "
                           "timeouts; resuming free-running. Re-attach to resume "
                           "stepping.", g_consecutiveTimeouts);
            }
            return false;
        }
        // Spin briefly, then sleep. The first few hundred microseconds are the
        // common case (a policy forward pass), and a syscall per poll there
        // would cost more than it saves. Stalling the renderer past that is a
        // feature -- synchronous env.step() semantics are the whole point.
        if (elapsed < 300) {
            std::this_thread::yield();
        } else {
            std::this_thread::sleep_for(std::chrono::microseconds(50));
        }
    }
}

// Apply an answered action block. Returns the requested observation stride.
int consumeActions(long curTick) {
    const auto& a = g_shm->action;

    if (a.seq != g_obsSeq) {
        // A stale or mismatched reply is refused rather than executed: it would
        // otherwise place a plan computed for one tick against a different one,
        // and the resulting trajectory would be wrong in a way that looks like
        // a policy decision.
        gdrl::g_gdrlEnvProtoErr++;
        __atomic_fetch_add(&g_shm->control.protocolErrors, 1ull, __ATOMIC_RELAXED);
        log::error("[gdrl] ENV action seq mismatch: got {} want {} -- refused",
                   (unsigned long long)a.seq, (unsigned long long)g_obsSeq);
        return 1;
    }

    if (a.detach) {
        storeRelease32(&g_shm->control.pyAttached, 0u);
        log::info("[gdrl] ENV Python detached by request at tick {}", curTick);
    }

    const uint16_t n = std::min<uint16_t>(a.count, GDRL_MAX_ACTIONS);
    for (uint16_t i = 0; i < n; i++) {
        const GdrlAction& act = a.actions[i];
        const bool p2 = act.player != 0;
        bool ok = true;
        switch ((GdrlActionKind)act.kind) {
            case GdrlActionKind::NOOP:
                break;
            case GdrlActionKind::PRESS:
                ok = schedule(act.targetTick, act.button, true, p2);
                break;
            case GdrlActionKind::RELEASE:
                ok = schedule(act.targetTick, act.button, false, p2);
                break;
            case GdrlActionKind::HOLD: {
                // (start_tick, hold_ticks) expanded here rather than in Python,
                // because the release has to survive the many steps between two
                // observations -- Objective C's whole point is that a hold is
                // one decision, not a decision repeated every tick.
                const long hold = std::max<long>(1, act.holdTicks);
                ok = schedule(act.targetTick, act.button, true, p2)
                  && schedule(act.targetTick + hold, act.button, false, p2);
                break;
            }
            default:
                log::error("[gdrl] ENV unknown action kind {} -- ignored", (int)act.kind);
                break;
        }
        if (!ok) {
            gdrl::g_gdrlEnvProtoErr++;
            __atomic_fetch_add(&g_shm->control.protocolErrors, 1ull, __ATOMIC_RELAXED);
            log::error("[gdrl] ENV schedule full ({} slots); action at tick {} "
                       "DROPPED. The policy asked for an input that will not "
                       "happen.", kMaxScheduled, (long long)act.targetTick);
        }
    }

    return a.advanceSteps > 0 ? a.advanceSteps : 1;
}

// Queue every scheduled input whose target step is the NEXT one.
//
// The +1 is not a fudge. processQueuedButtons runs at update+0x7b8 and this
// hook at update+0x9f8, both inside the same step-loop iteration, so a button
// queued here is drained at the start of the next step. Queuing at
// curTick + 1 == targetTick therefore makes the input take effect ON
// targetTick. Whether the tick read here advances by exactly one per call is
// UNVERIFIED -- see the bottom of this file for the falsification test.
void fireDueInputs(GJBaseGameLayer* layer, long curTick, double attemptTime) {
    for (auto& s : g_sched) {
        if (!s.live || curTick + 1 < s.tick) continue;

        // Late is not the same as on time, and the difference is invisible in
        // the trajectory: a jump that lands three ticks late just looks like a
        // policy that jumped three ticks late. Python asking for a tick that has
        // already gone by is a scheduling bug on its side, and it says so here
        // rather than being silently absorbed.
        if (s.tick <= curTick) {
            gdrl::g_gdrlEnvProtoErr++;
            if (g_shm) {
                __atomic_fetch_add(&g_shm->control.protocolErrors, 1ull, __ATOMIC_RELAXED);
            }
            log::error("[gdrl] ENV action for tick {} fired LATE at tick {} "
                       "(button={} push={}). The input happened, but not when it "
                       "was asked for.",
                       s.tick, curTick, s.button, (int)s.push);
        }

        // The timestamp argument mirrors experiments.cpp's verified injection
        // path exactly (it passes m_attemptTime). What GD does with it is not
        // established, so this is not the place to start differing from the
        // configuration the tick-exactness result was measured in.
        g_gdrlInjecting = true;
        layer->queueButton(s.button, s.push, s.player2, attemptTime);
        g_gdrlInjecting = false;
        s.live = false;
    }
}

// ---------------------------------------------------------------------------
// Filling the observation.
// ---------------------------------------------------------------------------

uint16_t vehicleFlagBits(PlayerObject* p) {
    // Bit order is load-bearing and is fixed by GdrlVehicleFlag in the generated
    // schema, which in turn matches main.cpp's modeFlagBits(). Reordering it
    // silently relabels every observation, so it is pinned by the schema hash.
    return (uint16_t)(
        (p->m_isShip   ? (uint16_t)GdrlVehicleFlag::SHIP   : 0) |
        (p->m_isBall   ? (uint16_t)GdrlVehicleFlag::BALL   : 0) |
        (p->m_isBird   ? (uint16_t)GdrlVehicleFlag::BIRD   : 0) |
        (p->m_isDart   ? (uint16_t)GdrlVehicleFlag::DART   : 0) |
        (p->m_isRobot  ? (uint16_t)GdrlVehicleFlag::ROBOT  : 0) |
        (p->m_isSpider ? (uint16_t)GdrlVehicleFlag::SPIDER : 0) |
        (p->m_isSwing  ? (uint16_t)GdrlVehicleFlag::SWING  : 0));
}

void fillPlayer(GdrlPlayerState& out, PlayerObject* p) {
    std::memset(&out, 0, sizeof(out));
    if (!p) return;
    out.present      = 1;
    out.x            = p->getPositionX();
    out.y            = p->getPositionY();
    out.yVelocity    = p->m_yVelocity;
    out.gravity      = p->m_gravity;
    out.rotation     = p->getRotation();
    out.vehicleSize  = p->m_vehicleSize;
    out.playerSpeed  = p->m_playerSpeed;
    out.vehicleFlags = vehicleFlagBits(p);
    out.isUpsideDown = p->m_isUpsideDown ? 1 : 0;
    out.isSideways   = p->m_isSideways ? 1 : 0;
    out.isOnGround   = p->m_isOnGround ? 1 : 0;
    out.isDashing    = p->m_isDashing ? 1 : 0;
    // Deliberately absent: `mini`. m_vehicleSize is the measurement; the
    // threshold that turns 0.6 into "mini" is a semantic decision and lives in
    // trainer/conditioning.py where it can be unit-tested.
}

// The one interpretation this file performs, and only because the README
// telemetry spec put `kind` on the wire. The raw GameObjectType and
// m_slopeIsHazard travel in the same record, and env.py re-derives this and
// asserts agreement -- so the collapse is checked rather than trusted.
uint8_t collapseKind(GameObjectType t, bool slopeIsHazard) {
    if (slopeIsHazard) return (uint8_t)GdrlObjectKind::HAZARD;
    switch (t) {
        case GameObjectType::Hazard:
        case GameObjectType::AnimatedHazard:
            return (uint8_t)GdrlObjectKind::HAZARD;
        case GameObjectType::Solid:
        case GameObjectType::Breakable:
        case GameObjectType::Slope:
        case GameObjectType::CollisionObject:
            return (uint8_t)GdrlObjectKind::SOLID;
        case GameObjectType::Decoration:
        case GameObjectType::Modifier:
        case GameObjectType::Special:
            return (uint8_t)GdrlObjectKind::OTHER;
        default:
            // Pads, rings, portals, collectibles. Defaulting the unknown tail
            // to INTERACTIVE rather than OTHER is the conservative choice: a
            // new object type that actually does something is more dangerous
            // filed as scenery than scenery is filed as interactive.
            return (uint8_t)GdrlObjectKind::INTERACTIVE;
    }
}

// Claim NOTHING for this step: zero-area window at the player, every column
// UNKNOWN, no objects, OBJECTS_UNAVAILABLE set so Python reads objectCount == 0
// as "not looked at" rather than "looked and found none".
//
// Extracted because there are now TWO reasons to refuse -- an unusable
// m_sectionXFactor and an unreadable camera -- and they must produce the
// IDENTICAL frame. A second hand-written copy is how one of them ends up
// forgetting to clear the object array and shipping last step's geometry.
void refuseScan(GJBaseGameLayer* layer, GdrlObservation* o, double px, double py) {
    o->header.sectionXFactor   = layer->m_sectionXFactor;
    o->header.sectionYFactor   = layer->m_sectionYFactor;
    o->header.levelLength      = layer->m_levelLength;
    o->header.coverageStartCol = 0;
    o->header.sectionColumns   = (int)layer->m_sections.size();
    o->header.windowMinX = (float)px;
    o->header.windowMaxX = (float)px;
    o->header.windowMinY = (float)py;
    o->header.windowMaxY = (float)py;
    for (int c = 0; c < GDRL_COVERAGE_COLS; c++) {
        o->coverage[c] = (uint8_t)GdrlCoverage::UNKNOWN;
    }
    for (int i = 0; i < GDRL_MAX_OBJECTS; i++) o->objects[i].known = 0;
    o->header.objectCount    = 0;
    o->header.objectsDropped = 0;
    o->header.flags |= (uint16_t)GdrlHeaderFlag::OBJECTS_UNAVAILABLE;
    o->validity.objectsTruncated = 0;
}

// Walk the section grid, not m_objects. The grid is the whole point of the
// grid: m_objects is every object in the level, and re-filtering ~2400 of them
// every physics step would put the scan cost on the critical path.
void scanObjects(GJBaseGameLayer* layer, GdrlObservation* o, double px, double py) {
    // m_sectionXFactor is a MULTIPLIER (1 / section width), NOT a divisor.
    // Column index is floor(x * sxf).
    //
    // Measured, not assumed -- `[gdrl] sectionXFactor` from the section-grid
    // dump in main.cpp, identical in all 52 surviving logs and re-measured this
    // session:  sectionXFactor = 0.01, sectionYFactor = 0.
    //
    // Cross-checked two independent ways, as required before touching this:
    //   (a) against README's documented sectionIndex = floor(x / 100):
    //       1 / 0.01 = 100, so floor(x * 0.01) == floor(x / 100). Agrees.
    //   (b) against m_sections.size() vs m_levelLength, per level:
    //         Stereo Madness  26724 * 0.01 = 267.24  ->  268 columns (measured 268)
    //         synth            6340 * 0.01 =  63.40  ->   64 columns (measured  64)
    //       i.e. nCols == floor(levelLength * sxf) + 1, exact on both. Under the
    //       old divisor reading the same levels would need 2,672,400 and 634,000
    //       columns, which is off by 1e4.
    //
    // The previous code divided. With col1 clamped to col0 + 63 that made the
    // advertised right edge 64 * 0.01 = 0.64 units, i.e. a window of
    // [px - 400, 0.64], and objectCount was 0 on 4,344 of 4,653 gameplay ticks
    // on a level spanning x = 0..6340.
    //
    // There is deliberately NO fallback constant. The old `: 100.f` default was
    // at least the right order of magnitude under the divisor reading; under the
    // multiplier reading it is wrong by 1e4 and would advertise a 6.4-million-
    // unit window as scanned. A section factor we cannot use means we did not
    // look, and saying "did not look" is the whole point of this channel.
    const float sxf = layer->m_sectionXFactor;
    if (!(sxf > 0.f) || !std::isfinite(sxf)) {
        refuseScan(layer, o, px, py);

        static bool warned = false;
        if (!warned) {
            warned = true;
            log::error("[gdrl] ENV m_sectionXFactor = {} is unusable (expected a "
                       "positive multiplier, measured 0.01). The object window is "
                       "reported as NOT SCANNED for every step of this run rather "
                       "than guessed at.", sxf);
        }
        return;
    }
    // Units per section column. Named so the two readings cannot be confused
    // again: sxf converts x -> column, secW converts column -> x.
    const double secW = 1.0 / (double)sxf;

    // -----------------------------------------------------------------------
    // THE WINDOW IS THE CAMERA. This is the Benchmark A sensor definition, and
    // it is derived per physics step rather than configured.
    //
    // It used to be px +- three constants (400 behind / 1400 ahead / +-600).
    // Measured 2026-08-15 (backups/reference-logs/viewport-probe-20260815-171406.log,
    // Stereo Madness, 1x, zoom 1.0) the screen actually shows 569.0 x 320.0
    // world units: 359.5 ahead, 209.5 behind, +215 up, -105 DOWN. So the old
    // sensor was 3.9x too wide ahead, 3.75x too tall, and symmetric about the
    // player where the real field is not -- the player sits low on screen. No
    // run before that fix landed is a Benchmark A run.
    //
    // RETUNING THE CONSTANTS WOULD HAVE BEEN WRONG, not merely worse: camera
    // zoom varies within and across levels and no constant tracks it, and
    // under kResolutionFixedHeight the visible WIDTH is a function of the OS
    // window's aspect ratio, so a constant width is not imprecise but
    // undefined until the launch geometry is pinned (TODO item 1b).
    //
    // gdrl::cameraWorldRect() inverts m_objectLayer's render transform over
    // the visible design rect, so zoom, pan and any ancestor scaling enter
    // structurally rather than by remembering to multiply. It restores every
    // cocos transform-cache field it could perturb, so calling it here is a
    // pure read by construction -- see viewport.hpp.
    //
    // WHAT THIS WINDOW STILL DOES NOT MODEL, and both understate rather than
    // overstate, which is the safe direction under A:
    //   * Perceptibility. The rect is geometry. Fades, effects and draw order
    //     are not modelled, so it is an UPPER bound on what a human sees.
    //   * Edge objects. The filters below are m_positionX/m_positionY CENTRE
    //     tests, so an object whose centre is just outside the rect is dropped
    //     even though part of it is on screen -- up to half an object, ~15
    //     units. That was invisible at +-600 vertical and is not at +215/-105.
    //     Deliberately left alone: a rect-overlap test would report objects
    //     outside the advertised window, and Python intersects the object list
    //     with that window (env.py known_mask). Changing both at once is how
    //     the two descriptions drift. UNVERIFIED how many objects per step
    //     this actually drops.
    // -----------------------------------------------------------------------
    if (g_winAnyOverride) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            log::warn("[gdrl] ENV window OVERRIDE active: behindSet={} behind={} "
                      "aheadSet={} ahead={} vertSet={} vert={}. An axis whose "
                      "Set flag is 0 stays camera-derived and its value above "
                      "is unused. THIS RUN IS NOT A BENCHMARK A RUN -- the "
                      "sensor is no longer what the screen shows. Intended "
                      "only for ablations and the B oracle.",
                      (int)g_winBehindSet, g_winBehind,
                      (int)g_winAheadSet,  g_winAhead,
                      (int)g_winVertSet,   g_winVert);
        }
    }

    // Only read the camera if some axis actually needs it. With all three
    // overridden the camera is not an input, so an unreadable one must not
    // kill a run that never asked about it.
    const gdrl::CameraRect cam =
        g_winAllOverride ? gdrl::CameraRect{} : gdrl::cameraWorldRect(layer);

    if (!g_winAllOverride && (!cam.valid || cam.rotated)) {
        // NO FALLBACK TO THE OLD CONSTANTS, EVER. Falling back would make some
        // steps of some runs secretly non-A while every byte on the wire still
        // looked ordinary -- the exact failure mode the deleted `: 100.f`
        // section-factor default used to have. An unreadable camera means we
        // do not know what was on screen, and "did not look" is a reportable
        // answer. A ROTATED camera is refused for a different reason: the rect
        // is then the axis-aligned BOUNDING BOX of the visible region and
        // OVERSTATES it, and an overstating sensor under A is lookahead the
        // player does not have. Deriving the inscribed rect is unmeasured
        // maths; refusing is honest and loud. m_cameraAngle has never been
        // observed nonzero (TODO 1c), so this firing is itself news.
        refuseScan(layer, o, px, py);

        static bool warned = false;
        if (!warned) {
            warned = true;
            log::error("[gdrl] ENV camera window unusable (valid={} rotated={} "
                       "angle={} zoom={} rect=[{:.3f},{:.3f}]x[{:.3f},{:.3f}]). "
                       "Every step of this run is reported as NOT SCANNED "
                       "rather than falling back to the pre-2026-08-15 "
                       "constants, which were 3.9x wider than the screen.",
                       (int)cam.valid, (int)cam.rotated, cam.angle, cam.zoom,
                       cam.left, cam.right, cam.bottom, cam.top);
        }
        return;
    }

    if (cam.sizeMismatch) {
        // NOT a refusal, and the distinction is substantive. The rect comes
        // from the render transform, which is correct under zoom by
        // construction; m_cameraWidth/Height is the cross-check, and it is the
        // cross-check that is disagreeing. Most likely cause is a zoom this
        // repo has never observed, under which m_cameraWidth stops tracking.
        // Reported once rather than absorbed -- it agreed on 6146/6146 samples
        // of the 2026-08-15 log, so any firing at all is news.
        static bool warned = false;
        if (!warned) {
            warned = true;
            log::warn("[gdrl] ENV camera size cross-check DISAGREES: derived "
                      "{:.4f}x{:.4f} vs m_cameraWidth/Height {:.4f}x{:.4f} "
                      "(zoom={:.6f}, tolerance {}). Using the transform-derived "
                      "rect; this is a cross-check failing, not the rect.",
                      cam.width(), cam.height(), cam.camWidth, cam.camHeight,
                      cam.zoom, gdrl::kSizeCheckTolerance);
        }
    }

    const double minX = g_winBehindSet ? px - (double)g_winBehind : cam.left;
    const double maxXRequested =
                       g_winAheadSet  ? px + (double)g_winAhead  : cam.right;
    const double minY = g_winVertSet  ? py - (double)g_winVert   : cam.bottom;
    const double maxY = g_winVertSet  ? py + (double)g_winVert   : cam.top;

    // m_sectionYFactor is measured 0 on every level dumped so far, and the
    // middle vector of m_sections is 1 deep in every dump ("max column (y) = 1"
    // on both Stereo Madness's 2399 objects and the synth level's 13). So GD's
    // grid is effectively 1-D and there is no y index to invert. Nothing here
    // uses it: the vertical filter below is a direct m_positionY compare
    // against [minY, maxY], which is correct whatever the y bucketing does. The
    // raw value still travels in the header so Python can see the 0 too.
    const int nCols = (int)layer->m_sections.size();
    int col0 = (int)std::floor(minX * (double)sxf);
    if (col0 < 0) col0 = 0;
    int col1 = (int)std::floor(maxXRequested * (double)sxf);

    // Never claim a window wider than the coverage mask can describe. A column
    // the mask cannot speak for must not be inside the region Python is told
    // was scanned, or "unknown" silently becomes "empty".
    //
    // THIS CLAMP JUST WENT SLACK, AND IT STAYS ANYWAY. Under the old 1400-ahead
    // constant the window spanned ~19 of the 64 columns; under the measured
    // 569-unit camera it spans ~7 at the measured secW of 100. So on Stereo
    // Madness at zoom 1 this min() now never binds, and a clamp that never
    // binds is exactly the shape of TODO N2 -- a constraint that goes slack
    // unnoticed and takes a test's meaning with it. Kept deliberately, for
    // reasons that are structural rather than defensive:
    //
    //   * secW is READ FROM THE GAME, not assumed. It is 0.01 -> 100 units on
    //     both levels ever dumped, but a level with a finer section grid, or
    //     a zoomed-OUT camera, needs more columns for the same world width.
    //     64 * secW is the mask's reach and nothing else bounds it.
    //   * Without it the advertised window would include columns the mask has
    //     no entry for. env.py clips the section index and returns UNKNOWN
    //     there, so it would not currently lie -- but that is a property of a
    //     second file's defensive clip, and this file must not be relying on
    //     it.
    //
    // What DID change is that the clamp binding is no longer routine. It now
    // means the camera is wider than the mask can describe, i.e. the sensor is
    // being silently narrowed below what the screen shows -- conservative
    // under A (the agent sees LESS than a human, never more) but a real loss
    // of fidelity, so it is reported instead of absorbed.
    const int col1Requested = col1;
    col1 = std::min(col1, col0 + GDRL_COVERAGE_COLS - 1);
    if (col1 < col1Requested) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            log::warn("[gdrl] ENV coverage clamp BOUND: window wanted columns "
                      "{}..{} ({} of them) but the mask holds {}. The right "
                      "edge is cut to {:.3f}; the agent sees LESS than the "
                      "screen shows. secW={:.4f}, window={:.3f}..{:.3f}.",
                      col0, col1Requested, col1Requested - col0 + 1,
                      GDRL_COVERAGE_COLS, (double)(col1 + 1) * secW,
                      secW, minX, maxXRequested);
        }
    }

    // Right edge of column col1 in world x, one section width past its left
    // edge. sxf is a float, so 1/0.01f is 100.0000022 and this edge sits a
    // fraction of a ULP past the column it names.
    //
    // NOTHING CORRECTS FOR THAT, DELIBERATELY. A `std::nextafter` back-off used
    // to sit here, justified by the claim that it kept the advertised window and
    // the coverage mask from disagreeing by a rounding step at the boundary. It
    // was measured dead on 2026-08-14 and deleted. Two reasons, and the second
    // is the one that matters:
    //
    // 1. It could not survive the store. windowMaxX below is an f32; at
    //    colEdge = 6400.0001430511511 (col1 = 63) one double ULP is 9.095e-13
    //    against an f32 ULP of 4.8828e-4, i.e. the correction was 1.9e-9 of the
    //    truncation that immediately follows it. Swept over EVERY float32 px in
    //    [-2048, 131072) at sxf = 0.01f -- 2,365,587,456 values, and the loop
    //    ran on all of them -- the stored float is bit-identical with and
    //    without the back-off. Same result over 1.31e9 (sxf, col1) pairs with
    //    sxf spanning [0.001, 1), and with a window wide enough
    //    (GDRL_ENV_WIN_AHEAD=20000, which clamps at :874) that colEdge rather
    //    than maxXRequested is what wins the min() below, on all 2.37e9.
    // 2. The guarantee it claimed to provide is false, and the real one does not
    //    need it. Window and mask DO disagree, at the LEFT edge, on every
    //    nonzero start column: windowMinX is the camera's left edge (:928,
    //    :828) while col0 is floor(minX * sxf) (:842), so the window starts
    //    partway INTO column col0 -- by up to a full section. The reason that
    //    cannot lie is
    //    structural: Python looks `state` up from the per-cell section index,
    //    not from the window, so knowledge is the INTERSECTION of the two and
    //    widening either alone can only remove cells, never add them. See the
    //    "WHY A WINDOW THAT IS TOO WIDE CANNOT LIE" comment in env.py's
    //    known_mask(), which is the correct statement of it.
    const double colEdge = (double)(col1 + 1) * secW;
    const double maxX = std::min(maxXRequested, colEdge);

    o->header.sectionXFactor   = layer->m_sectionXFactor;
    o->header.sectionYFactor   = layer->m_sectionYFactor;
    o->header.levelLength      = layer->m_levelLength;
    o->header.coverageStartCol = col0;
    o->header.sectionColumns   = nCols;
    o->header.windowMinX = (float)minX;
    o->header.windowMaxX = (float)maxX;
    o->header.windowMinY = (float)minY;
    o->header.windowMaxY = (float)maxY;

    uint16_t n = 0;
    uint16_t dropped = 0;

    for (int c = 0; c < GDRL_COVERAGE_COLS; c++) {
        const int col = col0 + c;
        if (col > col1) {
            // Outside the requested window. Not scanned, and saying so is the
            // point: an off-screen pit and an empty floor are the same bytes
            // without this.
            o->coverage[c] = (uint8_t)GdrlCoverage::UNKNOWN;
            continue;
        }
        if (col >= nCols) {
            // Past the end of GD's own section grid: the engine has no geometry
            // here, so this is known-empty rather than unknown.
            o->coverage[c] = (uint8_t)GdrlCoverage::ABSENT;
            continue;
        }
        auto* column = layer->m_sections[col];
        if (!column) {
            o->coverage[c] = (uint8_t)GdrlCoverage::ABSENT;
            continue;
        }

        bool truncated = false;
        for (auto* bucket : *column) {
            if (!bucket) continue;
            for (auto* obj : *bucket) {
                if (!obj) continue;

                // The two objects GD injects into the section grid that are not
                // level geometry: its own per-player collision proxies
                // (GJBaseGameLayer::m_player1CollisionBlock /
                // m_player2CollisionBlock, created by
                // createPlayerCollisionBlock()). Identified by POINTER, not by
                // object id -- see the measurement in
                // backups/reference-logs/phantom-synth-behind400.log:
                //
                //   PH-CB tick=1320 p1 ptr=0xac89c1000 uid=25 oid=1816
                //       otype=39 pos=(1864.222778320,105.0) rect=30x30
                //       inObjects=-1
                //   PH tick=1320 px=1866.172729492 prevPx=1864.222778320
                //       hits=5 ptrEqP1=5 atCol=14x1,15x1,16x1,17x1,18x1
                //
                // i.e. a 30x30 box carrying the player's own position from the
                // PREVIOUS physics step (bit-exact: the block's m_positionX at
                // tick t equals the player's getPositionX() at t-1 to all nine
                // decimals), reachable through the grid but NOT through
                // m_objects (inObjects=-1, which is why the census never saw
                // it). collapseKind maps its GameObjectType::CollisionObject to
                // SOLID, so every consumer was being shown a solid block
                // standing on the player.
                //
                // It also appears once per column it has ever entered and those
                // registrations are never removed, so it was emitted once per
                // behind-column: measured 5 per step at GDRL_ENV_WIN_BEHIND=400
                // and 10 at 900 (phantom-synth-behind900.log). Both of those
                // were taken when 400 was the DEFAULT behind-window; it is now
                // an override and the camera's measured 209.5 behind would give
                // ~3. The count scales with the window, the defect does not, and
                // this filter is a pointer identity that does not care either
                // way. Dropping it by pointer removes
                // all of those at once and touches nothing else: in every
                // sample of both runs, hits == ptrEqP1 + ptrEqP2, so no third
                // object was ever involved.
                //
                // Deliberately NOT filtered by m_objectID == 1816: 1816 is the
                // editor's Collision Block, a real authorable object that a
                // level may legitimately contain, and that one lives in
                // m_objects and should be reported.
                if (obj == layer->m_player1CollisionBlock ||
                    obj == layer->m_player2CollisionBlock) continue;

                // The THIRD object GD injects that is not level geometry:
                // GJBaseGameLayer::m_anticheatSpike. Measured on Stereo
                // Madness (GDRL_PROBE_PHANTOM=1, 2026-08-16):
                //
                //   PH-AC tick=1 GRID-HIT col=0 ptr=0x95ebcd200 uid=2410 oid=8
                //       pos=(0.000000000,105.000000000)
                //   PH-CB tag=acspike uid=2410 oid=8 otype=2
                //       rect=(-3.000,99.000,6.000,12.000) inObjects=2398
                //
                // i.e. a real objectID=8 spike with a hazard rect, parked at
                // the origin forever, appended to m_objects as its LAST element
                // (2398 of 2399 -- so unlike the collision blocks the census
                // does see it: id 8 counts 167 in m_objects against 166 in the
                // level string) and registered in section column 0. It is
                // GameObjectType::Hazard, so collapseKind maps it to HAZARD and
                // every consumer was told there is a spike at x=0 for the
                // ~210 ticks the camera's behind-window still contains the
                // origin. The player starts at (0,105), inside that rect, and
                // does not die -- it is not a hazard in play.
                //
                // Filtered by POINTER for the same reason as the blocks: id 8
                // is the ordinary spike and every other one of the 166 must be
                // reported.
                if (obj == layer->m_anticheatSpike) continue;

                if (obj->m_positionY < minY || obj->m_positionY > maxY) continue;
                if (obj->m_positionX < minX || obj->m_positionX > maxX) continue;
                if (n >= GDRL_MAX_OBJECTS) {
                    dropped++;
                    truncated = true;
                    continue;
                }
                GdrlObject& s = o->objects[n];
                std::memset(&s, 0, sizeof(s));
                s.known           = 1;
                s.uniqueID        = obj->m_uniqueID;
                s.objectID        = (int16_t)obj->m_objectID;
                s.objectType      = (int16_t)obj->m_objectType;
                s.slopeIsHazard   = obj->m_slopeIsHazard ? 1 : 0;
                s.isGroupDisabled = obj->m_isGroupDisabled ? 1 : 0;
                s.kind            = collapseKind(obj->m_objectType, obj->m_slopeIsHazard);
                s.isHazard        = (s.kind == (uint8_t)GdrlObjectKind::HAZARD) ? 1 : 0;
                s.x               = obj->m_positionX;
                s.y               = obj->m_positionY;
                s.rotation        = obj->getRotation();
                s.scaleX          = obj->m_scaleX;
                s.scaleY          = obj->m_scaleY;

                // getObjectRect(), not m_width/m_height: the latter are the
                // untransformed sprite extents, the rect is what collision
                // uses. Whether calling it per step perturbs the simulation is
                // UNVERIFIED -- see the falsification test at the bottom.
                const auto& r = obj->getObjectRect();
                s.halfW = r.size.width * 0.5f;
                s.halfH = r.size.height * 0.5f;

                // m_groups is a pointer to std::array<short,10>. m_groupCount
                // can exceed 10 in principle; clamped, and the clamp is visible
                // because groupCount is what is emitted.
                const int gc = std::clamp<int>(obj->m_groupCount, 0, GDRL_MAX_GROUPS);
                s.groupCount = (uint8_t)gc;
                if (obj->m_groups) {
                    for (int gi = 0; gi < gc; gi++) {
                        s.groups[gi] = (*obj->m_groups)[gi];
                    }
                }
                n++;
            }
        }
        o->coverage[c] = truncated ? (uint8_t)GdrlCoverage::TRUNCATED
                                   : (uint8_t)GdrlCoverage::SCANNED;
    }

    // Clear every slot the scan did not fill. Without this a slot keeps the
    // PREVIOUS step's object with known=1 still set, and Python decodes a ghost
    // that is one step stale and sitting wherever the object used to be. That
    // is the worst possible failure for this channel: a plausible object in a
    // plausible place, wrong only in time.
    for (int i = n; i < GDRL_MAX_OBJECTS; i++) {
        o->objects[i].known = 0;
    }

    o->header.objectCount    = n;
    o->header.objectsDropped = dropped;
    o->validity.objectsTruncated = dropped ? 1 : 0;
    if (dropped) {
        log::warn("[gdrl] ENV object window truncated: {} dropped at tick {}. "
                  "Some columns are marked TRUNCATED; the world Python sees is "
                  "incomplete, not empty.", dropped, (long long)o->header.tick);
    }
}

void fillObservation(GJBaseGameLayer* layer, PlayLayer* pl, float dtPerStep) {
    GdrlObservation* o = &g_shm->obs;

    // Seqlock: odd means a write is in flight. The ping-pong protocol already
    // stops the writer from starting n+1 before n is answered -- but that
    // guarantee dies the moment a wait times out and the game runs on, and a
    // torn read there would be misdiagnosed as a physics anomaly.
    const uint64_t writing = g_obsSeq + 1;
    storeRelease(&o->seq, writing);

    std::memset(&o->header, 0, sizeof(o->header));
    std::memset(&o->validity, 0, sizeof(o->validity));
    std::memset(o->coverage, 0, sizeof(o->coverage));

    PlayerObject* p1 = layer->m_player1;
    PlayerObject* p2 = layer->m_player2;

    auto& h = o->header;
    h.magic   = GDRL_OBS_MAGIC;
    h.version = (uint16_t)GDRL_WIRE_VERSION;

    const double t = pl ? pl->m_attemptTime : 0.0;
    // lround, never t == n/240.0. GD accumulates a float32 1/240 into a double,
    // so t*240 is not an exact integer: the residual is 2.039e-05 ticks at 391
    // ticks and stays unambiguous until ~11 hours of attempt time. attemptTime
    // rides alongside so this derivation is auditable from Python.
    h.tick        = (int32_t)std::lround(t * (double)GDRL_TICK_HZ);
    h.attemptTime = t;
    h.dtPerStep   = dtPerStep;
    h.timeWarp    = layer->m_gameState.m_timeWarp;
    h.playerX     = p1 ? p1->getPositionX() : 0.0;
    h.playerY     = p1 ? p1->getPositionY() : 0.0;
    h.playerSpeed = p1 ? p1->m_playerSpeed : 0.f;
    h.attempt     = g_attempt;
    h.frame       = g_frame;
    h.stepIndex   = g_stepIndex;
    h.dtIn        = g_dtIn;
    h.dtUsed      = g_dtUsed;
    h.isDualMode  = layer->m_gameState.m_isDualMode ? 1 : 0;
    h.isPaused    = (pl && pl->m_isPaused) ? 1 : 0;
    h.inResetDelay = (pl && pl->m_inResetDelay) ? 1 : 0;

    uint16_t flags = (uint16_t)GdrlHeaderFlag::GAMEPLAY_STEP;
    if (g_blockInput && g_gdrlLeaked == 0) flags |= (uint16_t)GdrlHeaderFlag::INPUT_CLEAN;
    if (h.isDualMode) flags |= (uint16_t)GdrlHeaderFlag::DUAL;
    // The three tables this wire version does not populate. count==0 with the
    // bit SET means "not looked at", which is a different claim from "looked
    // and found none" -- and the difference is exactly what stops the projector
    // from asserting a static world it never observed.
    //
    // commands: blocked on measuring WHICH GJEffectManager container holds live
    //   GroupCommandObject2 (m_unkVector518/530/560/5b0/600, m_unkMap5c8,
    //   m_unkMap770). See GDRL_PROBE_CMDVEC. Populating it from a guess would
    //   put a confident wrong position on every moving hazard.
    // pending: needs the trigger-objectID -> kind mapping and
    //   m_spawnRemapTriggers membership, neither established.
    // speedSegs: needs the four unmeasured entries of UNITS_PER_TICK.
    flags |= (uint16_t)GdrlHeaderFlag::COMMANDS_UNAVAILABLE;
    flags |= (uint16_t)GdrlHeaderFlag::PENDING_UNAVAILABLE;
    flags |= (uint16_t)GdrlHeaderFlag::SPEEDSEGS_UNAVAILABLE;
    h.flags = flags;

    h.commandCount  = 0;
    h.pendingCount  = 0;
    h.speedSegCount = 0;

    fillPlayer(o->players[0], p1);
    fillPlayer(o->players[1], p2);

    auto& v = o->validity;
    v.blocked       = g_gdrlBlocked;
    v.leaked        = g_gdrlLeaked;
    v.uiEvents      = g_gdrlUiEvents;
    v.timeouts      = gdrl::g_gdrlEnvTimeouts;
    v.levelID       = g_gdrlLevelID;
    v.pinnedLevelID = g_pinLevel ? g_pinnedID : -1;
    v.levelPinned   = g_pinLevel ? 1 : 0;
    v.blockInput    = g_blockInput ? 1 : 0;
    v.inputVerdict  = !g_blockInput   ? (uint32_t)GdrlInputVerdict::UNGUARDED
                    : (g_gdrlLeaked > 0) ? (uint32_t)GdrlInputVerdict::INVALID
                                         : (uint32_t)GdrlInputVerdict::CLEAN;

    const int objTotal = layer->m_objects ? (int)layer->m_objects->count() : 0;
    v.objectCountTotal = objTotal;

    // Status, most-severe first. Every one of these is a reason Python should
    // refuse the frame on the frame's own evidence rather than on a log line.
    uint32_t status = (uint32_t)GdrlStatus::OK;
    if (!pl)                                    status = (uint32_t)GdrlStatus::NO_PLAYLAYER;
    else if (!p1)                               status = (uint32_t)GdrlStatus::NO_PLAYER;
    else if (objTotal < 10)                     status = (uint32_t)GdrlStatus::LEVEL_EMPTY;
    else if (g_pinLevel && g_gdrlLevelID != g_pinnedID)
                                                status = (uint32_t)GdrlStatus::WRONG_LEVEL;
    else if (h.isPaused)                        status = (uint32_t)GdrlStatus::PAUSED;
    else if (h.inResetDelay)                    status = (uint32_t)GdrlStatus::RESET_PENDING;
    v.status = status;

    if (p1) {
        scanObjects(layer, o, p1->getPositionX(), p1->getPositionY());
    } else {
        h.flags |= (uint16_t)GdrlHeaderFlag::OBJECTS_UNAVAILABLE;
    }

    storeRelease(&o->seq, writing + 1);
    g_obsSeq = writing + 1;
    storeRelease(&g_shm->control.obsSeq, g_obsSeq);
}

// One synchronous env step: publish, block for the answer, schedule what came
// back. Called once per physics step from the prepareMoveActions hook.
void stepProtocol(GJBaseGameLayer* layer, PlayLayer* pl, float dtPerStep, long curTick) {
    fillObservation(layer, pl, dtPerStep);
    gdrl::g_gdrlEnvSteps++;
    __atomic_fetch_add(&g_shm->control.stepsServed, 1ull, __ATOMIC_RELAXED);

    if (awaitAction(g_obsSeq)) {
        g_stepsUntilPublish = consumeActions(curTick);
    } else {
        // No answer. The game runs on with whatever was already scheduled --
        // which is the honest fallback, because inventing a release here would
        // change an in-flight hold. The timeout is counted and logged; it is
        // never silently equivalent to "the policy did nothing".
        g_stepsUntilPublish = 1;
    }
}

}  // namespace

namespace gdrl {
bool envActive()   { return g_envOn && g_shm != nullptr; }
bool envAttached() { return pythonAttached(); }
}  // namespace gdrl

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

// The per-render-frame hook. It does NOT publish observations -- it only marks
// the window during which a prepareMoveActions call is the gameplay one, and
// optionally rewrites dt.
class $modify(GDRLTelemetryBaseGameLayer, GJBaseGameLayer) {
    void update(float dt) {
        if (!g_envOn) { GJBaseGameLayer::update(dt); return; }

        g_dtIn = dt;
        // Feeding update a dt of exactly N/240 controls how much simulated time
        // each rendered frame consumes, and the outcome is invariant under it
        // (91 attempts, three regimes, all bit-identical). Unlike
        // experiments.cpp's GDRL_ADAPTIVE this does NOT need to shrink around
        // event ticks, because actions are now placed per physics step rather
        // than per frame.
        const float useDt = g_envDeltaT > 0
            ? (float)((double)g_envDeltaT / (double)GDRL_TICK_HZ)
            : dt;
        g_dtUsed = useDt;

        // Precise, and PlayLayer::get() is not a substitute: loadUpToPosition
        // also calls prepareMoveActions and also runs with a live PlayLayer
        // (checkpoint seek), so only "we are inside update right now"
        // distinguishes the gameplay step from the seek step.
        g_inGameplayUpdate = true;
        GJBaseGameLayer::update(useDt);
        g_inGameplayUpdate = false;

        g_frame++;
    }
};

// The per-PHYSICS-STEP hook. This is where the environment loop lives.
class $modify(GDRLTelemetryEffectManager, GJEffectManager) {
    void prepareMoveActions(float dt, bool intermediate) {
        if (!g_envOn || !g_shm || !g_inGameplayUpdate) {
            GJEffectManager::prepareMoveActions(dt, intermediate);
            return;
        }

        auto* layer = GJBaseGameLayer::get();
        auto* pl    = PlayLayer::get();
        if (!layer) {
            GJEffectManager::prepareMoveActions(dt, intermediate);
            return;
        }

        g_stepIndex++;

        const long curTick = pl
            ? std::lround(pl->m_attemptTime * (double)GDRL_TICK_HZ)
            : -1;

        // Everything happens BEFORE delegating, so the observation describes a
        // fixed point inside the step -- after this step's processQueuedButtons
        // (update+0x7b8) and before its motion pipeline (update+0x9f8). Fixed
        // is what matters; the same point every step is what makes consecutive
        // observations comparable. Which side of the player's own integration
        // that lands on is UNVERIFIED (see the bottom of this file).
        const bool attached = pythonAttached();
        if (attached && !g_wasAttached) {
            // Fresh attach. Publish on this very step rather than finishing a
            // stride countdown left over from a previous session -- a policy
            // that attaches and then waits an unexplained N steps for its first
            // observation would look like a stalled environment.
            g_stepsUntilPublish = 0;
            log::info("[gdrl] ENV Python attached at tick {}", curTick);
        }
        g_wasAttached = attached;

        if (attached) {
            if (g_stepsUntilPublish <= 0) stepProtocol(layer, pl, dt, curTick);
            g_stepsUntilPublish--;
        }

        // Scheduled inputs fire whether or not an observation was published
        // this step. That is the entire reason a stride > 1 is usable: the
        // policy can observe every 8 ticks and still place an input on any one
        // of them.
        if (pl && curTick >= 0) fireDueInputs(layer, curTick, pl->m_attemptTime);

        GJEffectManager::prepareMoveActions(dt, intermediate);
    }
};

class $modify(GDRLTelemetryPlayLayer, PlayLayer) {
    void resetLevel() {
        if (g_envOn && g_stepIndex > 0) {
            // The per-attempt verdict, in the shape every other line in this
            // repo uses. timeouts and protoErr are the ones that decide whether
            // the attempt is admissible: a timeout means some step ran with no
            // action, which is indistinguishable from a policy that chose not
            // to act, so an attempt with timeouts>0 is not evidence.
            log::info("[gdrl] ENV a={:<4} lvl={} steps={:<6} frames={:<5} "
                      "endTick={:<6} attached={} env[timeouts={} protoErr={}] "
                      "input[blocked={} leaked={} ui={}]",
                      g_attempt, g_gdrlLevelID, gdrl::g_gdrlEnvSteps, g_frame,
                      (long long)std::lround(this->m_attemptTime * (double)GDRL_TICK_HZ),
                      (int)pythonAttached(), gdrl::g_gdrlEnvTimeouts, gdrl::g_gdrlEnvProtoErr,
                      g_gdrlBlocked, g_gdrlLeaked, g_gdrlUiEvents);
        }

        PlayLayer::resetLevel();

        // Reset AFTER delegating and unconditionally, so this hook's state does
        // not depend on whether it runs before or after main.cpp's or
        // experiments.cpp's resetLevel hook -- that order is a linker detail.
        g_attempt++;
        g_stepIndex = 0;
        g_frame     = 0;
        gdrl::g_gdrlEnvSteps = 0;
        gdrl::g_gdrlEnvTimeouts = 0;
        gdrl::g_gdrlEnvProtoErr = 0;
        g_stepsUntilPublish = 0;
        g_wasAttached = false;
        clearSchedule();
    }
};

$execute {
    if (!g_envOn) return;
    if (!openSegment()) {
        log::error("[gdrl] ENV requested (GDRL_ENV=1) but the transport failed to "
                   "open. Every hook in telemetry.cpp will now pass through, so "
                   "the game runs but no environment exists. This is deliberately "
                   "loud: a silently absent transport looks like a policy that "
                   "never acted.");
        return;
    }
    if (g_envDeltaT > 0 && envInt("GDRL_DELTA_TICKS", 0) > 0) {
        log::warn("[gdrl] ENV both GDRL_ENV_DELTA_TICKS={} and GDRL_DELTA_TICKS={} "
                  "are set. Two update hooks both rewrite dt and the order between "
                  "translation units is a linker detail, so which one wins is "
                  "UNVERIFIED. Set only one.",
                  g_envDeltaT, envInt("GDRL_DELTA_TICKS", 0));
    }
}

// ---------------------------------------------------------------------------
// UNVERIFIED -- each of these needs the GD slot, and each has a specific
// measurement that would falsify it. Nothing here was measured on a running
// game; the whole file is static analysis plus the existing README results.
//
// (1) prepareMoveActions really fires once per physics step in gameplay.
//     Static analysis says its gameplay call site is inside update's step loop,
//     but the processCommands episode is precisely why that is not the same as
//     knowing. TEST: run with GDRL_ENV=1 and a Python that answers NOOP, at
//     GDRL_ENV_DELTA_TICKS=1. header.stepIndex at the attempt boundary must
//     equal 391 on the null-input Stereo Madness trajectory, and every
//     consecutive pair of observations must show stepIndex and tick both
//     advancing by exactly 1.
//
// (2) The tick read here advances by exactly one per call, i.e. m_attemptTime
//     is updated once per step and before prepareMoveActions within the step.
//     TEST: the same run as (1); if m_attemptTime is updated after
//     prepareMoveActions, tick will lag stepIndex by a constant 1 and the pair
//     will still advance in lockstep -- which is why BOTH are emitted. A
//     constant offset is correctable; a varying one invalidates placement.
//
// (3) Placement is tick-exact through this path. experiments.cpp established
//     the ground truth by queuing from update(): at dt=1/240, injection tick
//     324 gives maxX=542.668640137 and 325 gives maxX=958.117858887, a hard
//     boundary with no smearing. TEST: schedule PRESS at targetTick=324 with
//     hold 8 and separately at 325, via this path, at GDRL_ENV_DELTA_TICKS=32.
//     The boundary must land in exactly the same place. If it lands at 323/324
//     or 325/326 the +1 in fireDueInputs is off by one -- which is a one-line
//     fix, but only once it is measured rather than reasoned about.
//
// (4) Enabling the environment does not perturb the simulation. The object scan
//     calls the virtual getObjectRect() on every object in the window every
//     step, and GameObject caches its rect (getObjectRectDirty exists), so it
//     is not obviously side-effect free. TEST: GDRL_ENV=1 + GDRL_BLOCK_INPUT=1
//     with a Python that answers NOOP for every step must still produce
//     maxX=507.615234375, t=1.629166752 and leaked=0, bit-identical to the
//     GDRL_ENV=0 baseline. If it does not, the scan is mutating game state and
//     every observation taken through it is suspect.
//
// (5) RESOLVED 2026-08-12, and it was a real bug. m_sectionXFactor is NOT a
//     divisor: it is a multiplier, 1/width. Measured 0.01 on Stereo Madness and
//     on the synth level (main.cpp's section-grid dump), cross-checked against
//     m_sections.size() == floor(levelLength * sxf) + 1 on both. The old
//     divisor arithmetic collapsed the advertised window to 0.64 units and
//     reported objectCount = 0 on 4,344 of 4,653 gameplay ticks. See the
//     comment in scanObjects. Still UNVERIFIED: that GD's own index expression
//     is floor(x * sxf) in float rather than some other rounding -- the two
//     cross-checks constrain the factor, not the rounding mode, so a boundary
//     object could still be one column out.
//
// (6) Hook ordering between translation units. main.cpp, experiments.cpp and
//     this file all $modify GJBaseGameLayer::update. Geode nests them in an
//     order that is a linker detail. It does not matter for correctness here
//     (g_inGameplayUpdate brackets whatever runs inside our delegate, and the
//     inner-most dt rewrite is the one the engine sees), but if both
//     GDRL_ENV_DELTA_TICKS and GDRL_DELTA_TICKS are set the effective dt is
//     whichever hook is innermost, and that is not determined here.
//
// (7) NEW 2026-08-15, and it is now on the HOT PATH rather than in a probe.
//     scanObjects calls gdrl::cameraWorldRect() every physics step, which calls
//     CCNode::nodeToWorldTransform() on m_objectLayer's whole parent chain.
//     That function RECOMPUTES and CACHES each ancestor matrix. viewport.cpp
//     snapshots and restores every CCNode cache field around the call, so the
//     chain is byte-identical afterwards BY CONSTRUCTION -- but "the field list
//     is complete on this binary" is an argument about a header, not a
//     measurement of the shipped object layout. TEST: one run with
//     GDRL_VERIFY_XFORM=1, which byte-compares the whole CCNode subobject
//     before the call against after the restore and logs every differing
//     offset. Zero VERIFY_XFORM lines over an attempt is the evidence; anything
//     else names the missing field directly. Until that run exists, the
//     state-neutrality of the camera read is UNVERIFIED on the observation
//     path, and (4)'s bit-identical maxX check is the outer guard that would
//     catch it as a symptom.
//
// (8) NEW 2026-08-15. The window is now the camera rect, and object membership
//     is a CENTRE test against it (m_positionX / m_positionY). At the old
//     +-600 vertical that slack was irrelevant; at the measured +215/-105 an
//     object whose centre is up to half its height outside the rect is dropped
//     while part of it is on screen. UNVERIFIED how many objects per step that
//     is -- it understates rather than overstates, which is the safe direction
//     under Benchmark A, but it has never been counted. TEST: log, for one
//     attempt, the count of objects rejected by the y/x filters whose
//     getObjectRect() nevertheless intersects the window.
// ---------------------------------------------------------------------------
