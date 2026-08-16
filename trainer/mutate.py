#!/usr/bin/env python3
"""Mutation grading for the env decoder and its loopback fixture.

WHY THIS EXISTS
---------------

On 2026-08-13 this repo graded ``test_env.py`` by mutation and got **7 of 25
mutants killed** -- while every one of those tests was green. Among the
survivors: deleting the ``OBJECTS_UNAVAILABLE`` refusal outright, a refusal
coming back as an all-*True* mask, and ``column_span()`` substituting 100.0
instead of returning None. "The tests pass" was worth nothing as evidence, and
the number that replaced it came from this harness. It is the instrument that
made the gap visible, so it lives in the repo rather than in a scratch
directory.

It is also the only check that catches the failure mode this suite is most prone
to: a test that recomputes its expectation from the same header field the code
under test reads, and therefore passes against any self-consistent answer. Those
tests are green forever and pin nothing. A mutant is how you find out.

WHAT A RESULT MEANS
-------------------

KILLED means at least one test failed, i.e. some test was actually looking at
that line. It does NOT mean the line is correct -- the mutants are all in this
repo's own Python, so this is a **tier (i)** instrument throughout: it grades the
test suite, never the game. A mutant of ``publish()`` that no test kills means
the fixture can drift from ``mod/src/telemetry.cpp`` unnoticed; it cannot tell
you whether the fixture matches the mod today. Only reading the C does that.

SURVIVED is the interesting outcome and is a defect in the tests unless the
mutant carries an ``expect="SURVIVED"`` with a measured reason (there is one:
the right-edge back-off, measured inert in L5).

USAGE
-----

    python3 trainer/mutate.py                 # grade test_env.py, all mutants
    python3 trainer/mutate.py --list
    python3 trainer/mutate.py --only left-edge
    python3 trainer/mutate.py --tests test_env.py test_trajectory.py
    python3 trainer/mutate.py --keep          # leave the mutated copies on disk

Exit status is 0 only when every mutant matched its expectation. Each mutant is
applied to a fresh throwaway copy of ``trainer/``; the checkout is never
modified. A mutant whose anchor text no longer appears exactly the expected
number of times is reported as PATCH-FAILED -- loudly, and counted as a failure,
because a silently skipped mutant is a grade you did not earn.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

TRAINER = pathlib.Path(__file__).resolve().parent
DEFAULT_TESTS = ["test_env.py"]


class Mutant:
    """One textual edit to one file, plus what it is supposed to prove."""

    def __init__(self, name, group, why, patches, target="env.py",
                 expect="KILLED", expect_why=None):
        self.name = name
        self.group = group
        self.why = why
        # (old, new, count) -- count is asserted, so a mutant that stops
        # anchoring is a reported failure rather than a quiet no-op.
        self.patches = [(o, n, c) for o, n, *rest in patches
                        for c in [rest[0] if rest else 1]]
        self.target = target
        self.expect = expect
        self.expect_why = expect_why


M = []


def mut(*a, **kw):
    M.append(Mutant(*a, **kw))


# ---------------------------------------------------------------------------
# A. known_mask(): the x -> coverage-column indexing expression
#
# This is where the original defect lived: sxf is a MULTIPLIER (0.01), and the
# code divided by it, putting every index 1e4 too high so the mask read "nothing
# is known" everywhere and no test noticed.
# ---------------------------------------------------------------------------

SECTION_LINE = "section = np.floor(cols_x * sxf).astype(np.int64) - start_col"
VALID_LINE = "valid = (section >= 0) & (section < GDRL_COVERAGE_COLS)"
COL_KNOWN = """col_known = in_x & (
            (state == int(GdrlCoverage.SCANNED)) | (state == int(GdrlCoverage.ABSENT))
        )"""

mut("A1-divide-instead-of-multiply", "indexing",
    "THE ORIGINAL DEFECT. sxf is 1/width, not width.",
    [(SECTION_LINE, "section = np.floor(cols_x / sxf).astype(np.int64) - start_col")])
mut("A2-start-col-sign-flip", "indexing",
    "Invisible while every test stood on coverageStartCol == 0.",
    [(SECTION_LINE, "section = np.floor(cols_x * sxf).astype(np.int64) + start_col")])
mut("A3-start-col-dropped", "indexing",
    "Same blind spot: dropping the term is a no-op when the term is 0.",
    [(SECTION_LINE, "section = np.floor(cols_x * sxf).astype(np.int64)")])
mut("A4-floor-to-ceil", "indexing",
    "Which column an x belongs to, off by one for every non-boundary x.",
    [(SECTION_LINE, "section = np.ceil(cols_x * sxf).astype(np.int64) - start_col")])
mut("A5-section-plus-one", "indexing",
    "A whole-section shift of the mask.",
    [(SECTION_LINE, SECTION_LINE + " + 1")])
mut("A6-lower-fencepost", "indexing",
    "coverage[0] is a column the mod walked, not a fencepost.",
    [(VALID_LINE, "valid = (section > 0) & (section < GDRL_COVERAGE_COLS)")])
mut("A7-upper-fencepost", "indexing",
    "Index 64 must not read as a copy of index 63.",
    [(VALID_LINE, "valid = (section >= 0) & (section <= GDRL_COVERAGE_COLS)")])
mut("A8-invalid-reads-as-scanned", "indexing",
    "Out-of-range columns filled with SCANNED: unknown becomes known.",
    [("""state = np.where(valid, cov[np.clip(section, 0, GDRL_COVERAGE_COLS - 1)],
                         int(GdrlCoverage.UNKNOWN))""",
      """state = np.where(valid, cov[np.clip(section, 0, GDRL_COVERAGE_COLS - 1)],
                         int(GdrlCoverage.SCANNED))""")])
mut("A9-drop-the-window-AND", "indexing",
    "Knowledge is window INTERSECT coverage; dropping either half widens it.",
    [(COL_KNOWN, """col_known = (
            (state == int(GdrlCoverage.SCANNED)) | (state == int(GdrlCoverage.ABSENT))
        )""")])
mut("A10-absent-is-not-known", "indexing",
    "Past the end of m_sections GD has no geometry: known-empty, not unknown.",
    [(COL_KNOWN, "col_known = in_x & (state == int(GdrlCoverage.SCANNED))")])
mut("A11-truncated-is-known", "indexing",
    "The array filled mid-column, so what is missing is genuinely unknown.",
    [(COL_KNOWN, """col_known = in_x & (
            (state == int(GdrlCoverage.SCANNED)) | (state == int(GdrlCoverage.ABSENT))
            | (state == int(GdrlCoverage.TRUNCATED))
        )""")])
mut("A12-cell-edges-not-centres", "indexing",
    "Which x represents a raster cell; shifts every column by half a cell.",
    [("cols_x = origin_x + (np.arange(width) + 0.5) * cell_size",
      "cols_x = origin_x + np.arange(width) * cell_size")])
mut("A13-row-origin-off-by-one", "indexing",
    "The player row convention shared with TrajectoryRaster.",
    [("origin_y = player_y - (height // 2) * cell_size",
      "origin_y = player_y - (height // 2 - 1) * cell_size")])
mut("A14-window-x-becomes-exclusive", "indexing",
    "The advertised window is read inclusive on both edges; pin that.",
    [('in_x = (cols_x >= float(h["windowMinX"])) & (cols_x <= float(h["windowMaxX"]))',
      'in_x = (cols_x > float(h["windowMinX"])) & (cols_x < float(h["windowMaxX"]))')])
mut("A15-window-y-becomes-exclusive", "indexing",
    "Same for the vertical filter, which is a direct y compare in the mod too.",
    [('in_y = (rows_y >= float(h["windowMinY"])) & (rows_y <= float(h["windowMaxY"]))',
      'in_y = (rows_y > float(h["windowMinY"])) & (rows_y < float(h["windowMaxY"]))')])

# ---------------------------------------------------------------------------
# B. The refusals (L4). "We did not look" must not be spendable as "nothing is
#    there", by any route -- including np.asarray() and truthiness.
# ---------------------------------------------------------------------------

FACTOR_GUARD = """        sxf = float(self.header["sectionXFactor"])
        if not (sxf > 0.0) or not np.isfinite(sxf):
            return None"""
REFUSE_OBJ = "            return KnownMask((height, width), MaskRefusal.OBJECTS_UNAVAILABLE)"
REFUSE_MAP = "            return KnownMask((height, width), MaskRefusal.NO_SECTION_MAPPING)"

mut("B1-substitute-a-constant", "refusal",
    "A substituted section width does not give a wrong answer, it gives a "
    "confident one. This is the original defect wearing a different hat.",
    [(FACTOR_GUARD, """        sxf = float(self.header["sectionXFactor"])
        if not (sxf > 0.0) or not np.isfinite(sxf):
            return MEASURED_SECTION_X_FACTOR""")])
mut("B2-drop-the-objects-unavailable-refusal", "refusal",
    "Reads last frame's window and coverage as this frame's observation.",
    [("""        if self.has_flag(GdrlHeaderFlag.OBJECTS_UNAVAILABLE):
            return KnownMask((height, width), MaskRefusal.OBJECTS_UNAVAILABLE)
""", "")])
mut("B3-refusal-returns-an-all-True-grid", "refusal",
    "A refusal handing out a grid at all, claiming everything is known.",
    [(REFUSE_OBJ,
      "            return KnownMask((height, width), None, _grid=np.ones((height, width), dtype=bool))"),
     (REFUSE_MAP,
      "            return KnownMask((height, width), None, _grid=np.ones((height, width), dtype=bool))")])
mut("B4-refusal-returns-an-all-False-grid", "refusal",
    "The defect L4 closed: a refusal that looks exactly like 'looked, knows "
    "nothing'. The plausible polarity, and the dangerous one.",
    [(REFUSE_OBJ,
      "            return KnownMask((height, width), None, _grid=np.zeros((height, width), dtype=bool))"),
     (REFUSE_MAP,
      "            return KnownMask((height, width), None, _grid=np.zeros((height, width), dtype=bool))")])
mut("B5-one-reason-for-both-refusals", "refusal",
    "'did not scan' and 'no x->column mapping' collapse into one word.",
    [(REFUSE_MAP, REFUSE_OBJ)])
mut("B6-looked-and-knew-nothing-called-a-refusal", "refusal",
    "The other polarity: a real observation reported as a refusal.",
    [("""        return KnownMask((height, width), None,
                         _grid=col_known[None, :] & in_y[:, None])""",
      """        _g = col_known[None, :] & in_y[:, None]
        if not _g.any():
            return KnownMask((height, width), MaskRefusal.NO_SECTION_MAPPING)
        return KnownMask((height, width), None, _grid=_g)""")])
mut("B7-array-launders-a-refusal", "refusal",
    "np.asarray(km) is the other route to a grid; it must hit the same gate.",
    [("""    def __array__(self, dtype=None, copy=None):
        g = self.grid()""",
      """    def __array__(self, dtype=None, copy=None):
        g = self._grid if self._grid is not None else np.zeros(self.shape, dtype=bool)""")])
mut("B8-refusal-is-silently-truthy", "refusal",
    "A dataclass is truthy by default, so `if km:` would be a silent lie.",
    [("""    def __bool__(self) -> bool:
        raise TypeError(""",
      """    def __bool__(self) -> bool:
        return True
        raise TypeError(""")])

# ---------------------------------------------------------------------------
# C. section_factor / section_width / column_span / the levelLength diagnostic
# ---------------------------------------------------------------------------

mut("C1-accept-a-zero-factor", "factor",
    "0 is a field the mod never wrote; it maps every x onto column 0.",
    [(FACTOR_GUARD, FACTOR_GUARD.replace("sxf > 0.0", "sxf >= 0.0"))])
mut("C2-drop-the-isfinite-guard", "factor",
    "inf/nan arrive from a 1/0 or 0/0 upstream and index nowhere.",
    [(FACTOR_GUARD, """        sxf = float(self.header["sectionXFactor"])
        if not (sxf > 0.0):
            return None""")])
mut("C3-abs-a-negative-factor", "factor",
    "Repairing a sign flip instead of reporting it.",
    [(FACTOR_GUARD, """        sxf = abs(float(self.header["sectionXFactor"]))
        if not (sxf > 0.0) or not np.isfinite(sxf):
            return None""")])
mut("C4-section-width-is-the-factor", "factor",
    "The two readings confused again: width 0.01 instead of 100.",
    [("        return 1.0 / sxf\n", "        return sxf\n")])
mut("C5-column-span-substitutes-100", "factor",
    "A numeric span from a refusal is a claim about columns nobody indexed.",
    [("""        sec_w = self.section_width()
        if sec_w is None:
            return None""",
      """        sec_w = self.section_width()
        if sec_w is None:
            sec_w = 100.0""")])
mut("C6-column-span-off-by-one-column", "factor",
    "The span's right end is the first x of the column past the array.",
    [("return start * sec_w, (start + GDRL_COVERAGE_COLS) * sec_w",
      "return start * sec_w, (start + GDRL_COVERAGE_COLS - 1) * sec_w")])
mut("C7-level-length-diagnostic-drops-the-plus-one", "factor",
    "L6's diagnostic: floor(length * sxf) + 1 == sectionColumns, on two levels.",
    [("        return math.floor(length * sxf) + 1 == cols",
      "        return math.floor(length * sxf) == cols")])
mut("C8-level-length-diagnostic-always-agrees", "factor",
    "The diagnostic's whole job is saying no to 1e-30 and to the divisor "
    "reading. Returning True unconditionally still type-checks.",
    [("        return math.floor(length * sxf) + 1 == cols", "        return True")])

# ---------------------------------------------------------------------------
# D. publish(): the loopback fixture's transcription of scanObjects().
#
# The left-edge arithmetic here is one session old (L5) and has never been
# graded. A survivor in this group means the fixture can drift away from
# telemetry.cpp again without a single test going red -- which is exactly how
# the 100-unit phantom window got in.
# ---------------------------------------------------------------------------

mut("D1-left-edge-snaps-to-the-column", "left-edge",
    "THE L5 DEFECT ITSELF: window derived from the column instead of the "
    "reverse. Advertised up to a full 100-unit section that was never scanned.",
    [('            h["windowMinX"] = min_x                              # :928',
      '            h["windowMinX"] = col0 * sec_w')])
mut("D2-window-behind-sign-flip", "left-edge",
    "minX = px + g_winBehind: the window starts ahead of the player.",
    [("            min_x = player_x - win_behind                        # :828",
      "            min_x = player_x + win_behind                        # :828")])
mut("D3-drop-the-col0-clamp", "left-edge",
    "telemetry.cpp:843. Near the level start minX is negative and col0 would "
    "index before the array.",
    [("            col0 = max(0, col0)                                  # :843\n", "")])
mut("D4-col0-from-the-player-not-the-window", "left-edge",
    "Column derived from px rather than minX -- a four-column shift at the "
    "fixture's 400 behind.",
    [("            col0 = int(math.floor(min_x * sxf))                  # :842",
      "            col0 = int(math.floor(player_x * sxf))               # :842")])
mut("D5-drop-the-coverage-clamp-on-col1", "left-edge",
    "telemetry.cpp:874. The window then claims columns the mask cannot speak "
    "for, and unknown silently becomes empty.",
    [("            col1 = min(col1, col0 + coverage_cols - 1)           # :874",
      "            col1 = col0 + coverage_cols - 1                      # :874")])
mut("D6-right-edge-ignores-the-requested-window", "left-edge",
    "telemetry.cpp:921. maxX must be the nearer of the requested edge and the "
    "column edge.",
    [("            max_x = min(max_x_requested, col_edge)               # :921",
      "            max_x = col_edge                                     # :921")])
mut("D7-col1-off-by-one", "left-edge",
    "One column too many claimed on the right.",
    [("            col1 = int(math.floor(max_x_requested * sxf))        # :844",
      "            col1 = int(math.floor(max_x_requested * sxf)) + 1    # :844")])
# D8-delete-the-right-edge-backoff retired 2026-08-14. It existed only to
# record that the nextafter back-off did nothing (an expected SURVIVED). The mod
# deleted its copy in efc32e9 and publish() no longer transcribes it, so there is
# no code left to mutate.
mut("D9-publish-ignores-the-injected-factor", "fixture",
    "The knob every refusal test depends on. Inert => those tests are vacuous.",
    [('        h["sectionXFactor"] = section_x_factor',
      '        h["sectionXFactor"] = MEASURED_SECTION_X_FACTOR')])
mut("D10-publish-ignores-layout-x-factor", "fixture",
    "The knob that lets a refusal test stand on a real window; if it is inert "
    "the refusal tests prove nothing (L3).",
    [("""        sxf = float(np.float32(section_x_factor if layout_x_factor is None
                               else layout_x_factor))""",
      "        sxf = float(np.float32(section_x_factor))")])
mut("D11-measured-factor-reverts-to-100", "fixture",
    "The fabricated constant the divisor reading needed. A test built on a "
    "header the game never produces is testing a world that does not exist.",
    [("MEASURED_SECTION_X_FACTOR = float(np.float32(0.01))",
      "MEASURED_SECTION_X_FACTOR = 100.0")])
mut("D12-publish-default-factor-fabricated", "fixture",
    "Same, injected at the call rather than the constant.",
    [("                section_x_factor: float = MEASURED_SECTION_X_FACTOR,",
      "                section_x_factor: float = 100.0,")])
mut("D13-absent-columns-published-as-scanned", "fixture",
    "Past m_sections.size() the fixture must say ABSENT, not SCANNED.",
    [("""                elif col >= n_cols:
                    cov[c] = int(GdrlCoverage.ABSENT)""",
      """                elif col >= n_cols:
                    cov[c] = int(GdrlCoverage.SCANNED)""")])
mut("D14-scanned-cols-miscounted", "fixture",
    "self.scanned_cols is what tests use to check the case is not vacuous; if "
    "it lies, the vacuity guards do too.",
    [("            self.scanned_cols = scanned", "            self.scanned_cols = coverage_cols")])
mut("D15-refusal-frame-keeps-a-real-window", "fixture",
    "The mod's bad-factor path claims nothing (refuseScan, telemetry.cpp:"
    "651-669). A "
    "fixture that left a real window there would make the refusal tests test "
    "the window instead of the refusal.",
    [("""            h["windowMinX"] = player_x
            h["windowMaxX"] = player_x""",
      """            h["windowMinX"] = player_x - 400.0
            h["windowMaxX"] = player_x + 1400.0""")])
mut("D17-vertical-window-forced-symmetric-above", "fixture",
    "The real window is +215 above the player and -105 below (measured "
    "2026-08-15). Collapsing win_up back onto win_vert re-symmetrises it, "
    "which is what every constant this repo used to ship did.",
    [("            above = win_vert if win_up is None else win_up",
      "            above = win_vert")])
mut("D18-vertical-window-forced-symmetric-below", "fixture",
    "Same, downward. Kept as a separate row because a decoder can lose one "
    "half of the asymmetry without losing the other.",
    [("            below = win_vert if win_down is None else win_down",
      "            below = win_vert")])
mut("D19-vertical-window-halves-swapped", "fixture",
    "Up and down exchanged: the player sits HIGH on screen instead of low. "
    "Same window height, so anything checking only the extent agrees.",
    [("            min_y = player_y - below                             # :831\n"
      "            max_y = player_y + above                             # :832",
      "            min_y = player_y - above                             # :831\n"
      "            max_y = player_y + below                             # :832")])
mut("D16-refusal-frame-drops-objects-unavailable", "fixture",
    "Without the flag a count of 0 reads as 'looked and found none'.",
    [('            h["flags"] = int(h["flags"]) | int(GdrlHeaderFlag.OBJECTS_UNAVAILABLE)\n',
      "")])

# ---------------------------------------------------------------------------
# Control. No behavioural change; must SURVIVE. If it does not, the suite is
# failing for a reason that has nothing to do with mutation and every verdict
# below it is meaningless.
# ---------------------------------------------------------------------------

mut("Z0-control-no-behavioural-change", "control",
    "Instrument check.",
    [("class MaskRefused(RuntimeError):",
      "# mutation control: this comment is the whole mutation.\nclass MaskRefused(RuntimeError):")],
    expect="SURVIVED",
    expect_why="It changes a comment. A KILLED here means the suite is red "
               "before any mutation and nothing else in this table can be read.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def apply_and_run(m: Mutant, tests: list[str], workroot: pathlib.Path) -> tuple[str, str, list[str]]:
    work = workroot / m.name
    shutil.copytree(TRAINER, work,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache",
                                                  "*.pyc", ".DS_Store"))
    path = work / m.target
    text = path.read_text()
    for old, new, count in m.patches:
        found = text.count(old)
        if found != count:
            return "PATCH-FAILED", f"anchor matched {found}x, expected {count}x", []
        text = text.replace(old, new)
    path.write_text(text)

    r = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--tb=no", "-rf",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=work, capture_output=True, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    tail = (r.stdout.strip().splitlines() or ["<no output>"])[-1]
    killers = sorted({mm.group(1) for mm in
                      re.finditer(r"^FAILED \S*?::?(test_\w+)", r.stdout, re.M)})
    return ("KILLED" if r.returncode != 0 else "SURVIVED"), tail, killers


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tests", nargs="+", default=DEFAULT_TESTS,
                    help="test files to grade, relative to trainer/ (default: test_env.py)")
    ap.add_argument("--only", default=None,
                    help="substring filter on mutant name or group")
    ap.add_argument("--list", action="store_true", help="list mutants and exit")
    ap.add_argument("--keep", action="store_true",
                    help="keep the mutated copies for inspection")
    ap.add_argument("--killers", action="store_true",
                    help="print which tests killed each mutant")
    args = ap.parse_args()

    chosen = [m for m in M
              if args.only is None or args.only in m.name or args.only == m.group]
    if args.list:
        for m in chosen:
            print(f"{m.group:9s} {m.name:46s} expect {m.expect}")
        print(f"\n{len(chosen)} mutants")
        return 0
    if not chosen:
        print(f"no mutant matches --only {args.only!r}")
        return 2

    workroot = pathlib.Path(tempfile.mkdtemp(prefix="gdrl-mutate-"))
    rows = []
    try:
        for m in chosen:
            verdict, detail, killers = apply_and_run(m, args.tests, workroot)
            ok = verdict == m.expect
            rows.append((m, verdict, detail, killers, ok))
            flag = "   " if ok else ">>>"
            print(f"{flag} {verdict:12s} {m.name:46s} {detail}")
            if not ok and verdict != "PATCH-FAILED":
                print(f"      expected {m.expect}: {m.expect_why or m.why}")
            if args.killers and killers:
                for k in killers:
                    print(f"        killed by {k}")
    finally:
        if args.keep:
            print(f"\nmutated copies left in {workroot}")
        else:
            shutil.rmtree(workroot, ignore_errors=True)

    bad = [r for r in rows if not r[4]]
    killed = sum(1 for m, v, *_ in rows if v == "KILLED")
    graded = [r for r in rows if r[0].expect == "KILLED"]
    killed_graded = sum(1 for m, v, *_ in graded if v == "KILLED")
    print(f"\n{killed_graded} of {len(graded)} killable mutants killed "
          f"({killed} KILLED overall, {len(rows)} run, tests={' '.join(args.tests)})")
    if bad:
        print("\nNOT AS EXPECTED -- read these before believing any green suite:")
        for m, v, detail, _k, _ok in bad:
            print(f"  {v:12s} {m.name}  (expected {m.expect})\n      {m.why}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
