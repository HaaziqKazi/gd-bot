"""Recorded ground truth: one move trigger, measured off the running game.

EVIDENTIARY TIER (iii) -- recorded game data. Every number in this module was
read out of a Geode log produced by a real Geometry Dash 2.2081 process. None
of it was produced by ``trainer/trajectory.py``, by any other Python in this
repo, or by reasoning about what GD "should" do. That is the whole point of the
file: it exists so that a test can compare the predictor against the game
rather than against itself.

PROVENANCE
----------
Capture command::

    GDRL_SYNTH=1 GDRL_AUTOPLAY=1 GDRL_PROBE_MOVE=1 GDRL_BLOCK_INPUT=1 \
        ./scripts/run_sandbox.sh

Source log::

    sandbox/Geometry Dash.app/Contents/geode/logs/Geode 2026-08-11 18.41.23.log

Cross-check log, a **separate GD launch** whose 480 ``MOVE`` lines are
byte-identical to the above (``diff`` clean)::

    sandbox/Geometry Dash.app/Contents/geode/logs/Geode 2026-08-11 18.37.10.log

Both are copied to ``backups/reference-logs/``. ``sandbox/`` and ``backups/``
are both gitignored, and ``.gitignore`` also carries a bare ``*.log`` rule, so
**neither log is durable and neither can be committed**. This module is the
durable form of that measurement; it is plain Python so that no ignore rule can
silently unhook it.

THE RECORDED RUN
----------------
Synthetic level (``GDRL_SYNTH=1``). One move trigger, one target block:

    MOVE-TRIGGER tick=1 id=901 target=1 offX=0 offY=90 duration=2.0 easing=0

    trigger object  id=901  at x=300, y=135, group 0   (x-activated)
    target block    id=1    at x=600, y=435, group 1

The probe samples ``GameObject::m_positionX/m_positionY`` (doubles at +0x3b0 /
+0x3b8) after every gameplay ``processMoveActions`` call and emits one ``MOVE``
line per object whose position changed. It also logs ``cx``/``cy``, the CCNode
shadow, which stayed pinned at ``435.000000000`` for all 480 records -- the
move pipeline never calls ``CCNode::setPosition``.

Tick clock is ``lround(PlayLayer::m_attemptTime * 240.0)``.

WHAT IS MEASURED AND WHAT IS INFERRED
-------------------------------------
Measured, directly in the log:

  * 480 displacement records, ticks 234 .. 713, no gaps, no duplicates. All
    480 are carried below in ``RECORDS`` / ``RECORDS_BY_TICK``, so a truth
    lookup at any integer tick in that span is a recorded number and never an
    interpolated one. ``SAMPLES`` is a fifteen-point readability subset.
  * Total displacement 435 -> 525 = exactly 90.000000000.
  * ``ACTIVATION_TICK = 233`` -- two independent lines of evidence:
      (a) ``GDRL_PROBE_CMDVEC=1``, log ``Geode 2026-08-11 18.25.25.log``, a
          *different launch*: ``m_unkVector560`` size goes 0 -> 1 on tick 233
          and 1 -> 0 on tick 715. That probe is edge-triggered per physics
          step, so 233 is the first step at which the command existed.
      (b) The displacement record alone, with no reference to (a): inverting
          ``offset = 90 * (t - a) / 480`` for ``a`` at each of the 480 records
          gives a = 232.99934 mean, range [232.99699, 233.00020]. So the tick
          at which the command's elapsed time was zero is 233.000 +/- 0.003.
    (a) and (b) agree, and (b) is independent of the probe that produced (a).

Measured, but in a DIFFERENT RUN and with weaker provenance -- see
``PLAYER_X_RECORDS`` below and read its warning before quoting it:

  * The player's x on this synth level, at the two ticks that bracket the
    trigger. Measured 2026-08-12 off the ``GDRL_ENV`` channel (57,009
    observations, one attempt to completion, ticks 1..4653). That stream was
    consumed in-process by Python and never written to a log, so unlike every
    other number in this file it cannot be re-extracted from a file on disk --
    it is transcribed from the session record. It is corroborated rather than
    trusted: an independent float32 accumulator reproduces both values bit for
    bit (``test_projection_groundtruth``), which two 24-bit mantissas do not do
    by accident.

Inferred, NOT measured -- do not treat these as ground truth:

  * The player's x at any tick OTHER than the two carried below. The MOVE probe
    logs no player position, so anything needing x at, say, tick 462 uses the
    law rather than a record.
  * Why the activation step itself displaces nothing. ``m_alreadyUpdated``
    (GroupCommandObject2 +0x1b0) is the obvious candidate and is UNVERIFIED.

SCOPE
-----
One trigger, ``ActionType`` 2 (y-offset), linear easing, duration 2.0 s, 1x
speed, cube, single group, no lock flags, no delay. Rotation, transform,
non-zero easing, non-unit ``m_moveMod*``, spawn delays, multiple commands on
one group, and every non-1x speed bucket are all unmeasured.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Trigger configuration, as GD loaded it (MOVE-TRIGGER line, verbatim)
# --------------------------------------------------------------------------

TRIGGER_OBJECT_ID = 901
TRIGGER_X = 300.0
TRIGGER_Y = 135.0

TARGET_GROUP_ID = 1
MOVE_OFFSET_X = 0.0
MOVE_OFFSET_Y = 90.0
DURATION = 2.0
EASING_TYPE = 0          # EasingType.NONE -- linear
EASING_RATE = 2.0        # not authored; GD's default. Irrelevant at easing 0.

TARGET_OBJECT_ID = 1
TARGET_X = 600.0
TARGET_Y_START = 435.0
TARGET_Y_END = 525.0

# --------------------------------------------------------------------------
# Timing, in ticks
# --------------------------------------------------------------------------

# The step at which the GroupCommandObject2 existed and its elapsed time was
# zero. See "WHAT IS MEASURED" above -- two independent lines of evidence.
ACTIVATION_TICK = 233

# The first step that displaced the block, and the last.
FIRST_MOVE_TICK = 234
LAST_MOVE_TICK = 713

# The step at which m_unkVector560 emptied (CMDVEC, separate launch).
COMMAND_REMOVED_TICK = 715

# ACTIVATION_TICK produces zero displacement. A predictor that starts the
# motion on the activation tick leads the game by exactly one tick.
ACTIVATION_DEAD_TIME_TICKS = FIRST_MOVE_TICK - ACTIVATION_TICK   # == 1

NUM_RECORDS = 480        # == LAST_MOVE_TICK - FIRST_MOVE_TICK + 1

# --------------------------------------------------------------------------
# Aggregate statistics over all 480 records
# --------------------------------------------------------------------------

# Sum of the logged per-step dy, and the endpoint-to-endpoint displacement.
TOTAL_DISPLACEMENT = 90.0

# Mean of the logged dy over all 480 records. Exactly 90/480 to nine places.
DY_MEAN_ALL = 0.187500000

# Mean over the 479 records excluding the final short step. This is the figure
# the README quotes as "per-tick dy mean"; it is NOT the mean over all 480, and
# it is NOT equal to 90/480. Both are recorded here so the discrepancy is not
# rediscovered as a bug.
DY_MEAN_EXCLUDING_FINAL = 0.187501179

# Largest and smallest logged dy. The minimum is the final step, which is short
# because p = clamp(elapsed/duration, 0, 1) lands the endpoint exactly on 90.
DY_MAX = 0.187507629
DY_MIN_FINAL_STEP = 0.186935425

# Residual against the *theoretical* line y = 435 + 90*(t - 233)/480, in units.
# This is float32 accumulation in m_deltaTimeInFloat, not curvature: genuine
# easing would swing per-step dy by tens of percent.
LINEARITY_RESIDUAL_MAX_THEORETICAL = 5.646e-4
LINEARITY_RESIDUAL_RMS_THEORETICAL = 2.224e-4

# Residual against a least-squares fit line, which absorbs the constant part of
# the float32 drift. These are the README's numbers; they are a different
# quantity from the two above and are ~4x smaller for that reason alone.
LINEARITY_RESIDUAL_MAX_LSQ = 3.988e-4
LINEARITY_RESIDUAL_RMS_LSQ = 9.395e-5

# Least-squares slope over all 480 records, units per tick. Exceeds 90/480 =
# 0.1875 by 6.1 ppm: the game's float32 m_deltaTimeInFloat accumulates very
# slightly fast, so a float64 predictor of the same law lags by that fraction.
LSQ_SLOPE = 0.187501148182


# --------------------------------------------------------------------------
# THE COMPLETE RECORD: all 480 ``MOVE`` lines, (tick, m_positionY)
# --------------------------------------------------------------------------
# Every one of the 480 records, verbatim to the nine decimal places the probe
# printed, ticks 234..713 contiguous with no gaps and no duplicates. On every
# record m_positionX was 600.000000000, dx was 0.000000000, and the CCNode
# shadow (cx, cy) was (600.000000000, 435.000000000).
#
# WHY THE WHOLE RECORD IS CARRIED, NOT A SUBSAMPLE
# ------------------------------------------------
# This module previously carried only the fifteen ``SAMPLES`` below. A consumer
# that needed the truth at a tick between two samples had to interpolate, and
# the only interpolant available is the linear motion law -- which is precisely
# the predictor's own model. Comparing the predictor against an interpolant
# built from the predictor's model is self-consistency, not measurement: it
# demotes any such assertion from tier (iii) to tier (ii) without saying so.
# The concrete case was ``test_pending_path_leads_the_game_by_the_measured_
# amount``, whose arrival tick 462.16 fell in the 73-tick hole between the
# samples at 400 and 473, so BOTH bracketing values were modelled rather than
# recorded.
#
# With the full record present, a truth lookup at any integer tick in
# [234, 713] is a recorded number. Only sub-tick interpolation between two
# ADJACENT recorded steps remains, which is unavoidable -- the game only exists
# at integer ticks -- and spans at most one 0.1875-unit step.
RECORDS: tuple[tuple[int, float], ...] = (
    (234, 435.187502503),
    (235, 435.374999642),
    (236, 435.562502146),
    (237, 435.749999285),
    (238, 435.937501788),
    (239, 436.124998927),
    (240, 436.312501431),
    (241, 436.499998569),
    (242, 436.687501073),
    (243, 436.874998212),
    (244, 437.062500715),
    (245, 437.249997854),
    (246, 437.437500477),
    (247, 437.624997616),
    (248, 437.812500000),
    (249, 438.000002384),
    (250, 438.187499523),
    (251, 438.375002146),
    (252, 438.562499285),
    (253, 438.750001907),
    (254, 438.937499046),
    (255, 439.125001431),
    (256, 439.312498569),
    (257, 439.500000954),
    (258, 439.687503815),
    (259, 439.875000954),
    (260, 440.062503338),
    (261, 440.250000477),
    (262, 440.437502861),
    (263, 440.625000000),
    (264, 440.812502384),
    (265, 440.999999523),
    (266, 441.187501907),
    (267, 441.374999046),
    (268, 441.562501907),
    (269, 441.749999046),
    (270, 441.937501431),
    (271, 442.124998569),
    (272, 442.312500954),
    (273, 442.499998093),
    (274, 442.687500954),
    (275, 442.874998093),
    (276, 443.062500000),
    (277, 443.249997139),
    (278, 443.437500000),
    (279, 443.624997139),
    (280, 443.812500000),
    (281, 443.999997139),
    (282, 444.187499046),
    (283, 444.374996185),
    (284, 444.562499046),
    (285, 444.749996185),
    (286, 444.937498093),
    (287, 445.124996185),
    (288, 445.312498093),
    (289, 445.499995232),
    (290, 445.687498093),
    (291, 445.874995232),
    (292, 446.062497139),
    (293, 446.249994278),
    (294, 446.437497139),
    (295, 446.624994278),
    (296, 446.812497139),
    (297, 446.999999046),
    (298, 447.187496185),
    (299, 447.374993324),
    (300, 447.562496185),
    (301, 447.749998093),
    (302, 447.937496185),
    (303, 448.124992371),
    (304, 448.312495232),
    (305, 448.499998093),
    (306, 448.687495232),
    (307, 448.874992371),
    (308, 449.062494278),
    (309, 449.249997139),
    (310, 449.437494278),
    (311, 449.624991417),
    (312, 449.812494278),
    (313, 449.999996185),
    (314, 450.187493324),
    (315, 450.374990463),
    (316, 450.562493324),
    (317, 450.749996185),
    (318, 450.937492371),
    (319, 451.124990463),
    (320, 451.312492371),
    (321, 451.499994278),
    (322, 451.687492371),
    (323, 451.874988556),
    (324, 452.062492371),
    (325, 452.249994278),
    (326, 452.437492371),
    (327, 452.624988556),
    (328, 452.812490463),
    (329, 452.999994278),
    (330, 453.187490463),
    (331, 453.374988556),
    (332, 453.562490463),
    (333, 453.749992371),
    (334, 453.937490463),
    (335, 454.124986649),
    (336, 454.312490463),
    (337, 454.499992371),
    (338, 454.687488556),
    (339, 454.874986649),
    (340, 455.062488556),
    (341, 455.249992371),
    (342, 455.437488556),
    (343, 455.624984741),
    (344, 455.812488556),
    (345, 455.999990463),
    (346, 456.187488556),
    (347, 456.374984741),
    (348, 456.562486649),
    (349, 456.749990463),
    (350, 456.937486649),
    (351, 457.124984741),
    (352, 457.312486649),
    (353, 457.499988556),
    (354, 457.687486649),
    (355, 457.874982834),
    (356, 458.062486649),
    (357, 458.249988556),
    (358, 458.437484741),
    (359, 458.624982834),
    (360, 458.812484741),
    (361, 458.999988556),
    (362, 459.187484741),
    (363, 459.374982834),
    (364, 459.562484741),
    (365, 459.749986649),
    (366, 459.937484741),
    (367, 460.124980927),
    (368, 460.312484741),
    (369, 460.499986649),
    (370, 460.687482834),
    (371, 460.874980927),
    (372, 461.062482834),
    (373, 461.249984741),
    (374, 461.437482834),
    (375, 461.624980927),
    (376, 461.812482834),
    (377, 461.999984741),
    (378, 462.187482834),
    (379, 462.374979019),
    (380, 462.562480927),
    (381, 462.749984741),
    (382, 462.937480927),
    (383, 463.124979019),
    (384, 463.312480927),
    (385, 463.499982834),
    (386, 463.687480927),
    (387, 463.874977112),
    (388, 464.062480927),
    (389, 464.249982834),
    (390, 464.437480927),
    (391, 464.624977112),
    (392, 464.812479019),
    (393, 464.999982834),
    (394, 465.187479019),
    (395, 465.374977112),
    (396, 465.562479019),
    (397, 465.749980927),
    (398, 465.937479019),
    (399, 466.124975204),
    (400, 466.312479019),
    (401, 466.499980927),
    (402, 466.687477112),
    (403, 466.874975204),
    (404, 467.062477112),
    (405, 467.249980927),
    (406, 467.437477112),
    (407, 467.624973297),
    (408, 467.812477112),
    (409, 467.999980927),
    (410, 468.187477112),
    (411, 468.374973297),
    (412, 468.562477112),
    (413, 468.749977112),
    (414, 468.937477112),
    (415, 469.124973297),
    (416, 469.312477112),
    (417, 469.499977112),
    (418, 469.687473297),
    (419, 469.874973297),
    (420, 470.062473297),
    (421, 470.249977112),
    (422, 470.437473297),
    (423, 470.624969482),
    (424, 470.812473297),
    (425, 470.999977112),
    (426, 471.187473297),
    (427, 471.374969482),
    (428, 471.562473297),
    (429, 471.749977112),
    (430, 471.937473297),
    (431, 472.124969482),
    (432, 472.312473297),
    (433, 472.499973297),
    (434, 472.687473297),
    (435, 472.874969482),
    (436, 473.062473297),
    (437, 473.249973297),
    (438, 473.437469482),
    (439, 473.624969482),
    (440, 473.812469482),
    (441, 473.999973297),
    (442, 474.187469482),
    (443, 474.374969482),
    (444, 474.562469482),
    (445, 474.749973297),
    (446, 474.937469482),
    (447, 475.124965668),
    (448, 475.312469482),
    (449, 475.499973297),
    (450, 475.687469482),
    (451, 475.874965668),
    (452, 476.062469482),
    (453, 476.249969482),
    (454, 476.437469482),
    (455, 476.624965668),
    (456, 476.812469482),
    (457, 476.999969482),
    (458, 477.187469482),
    (459, 477.374965668),
    (460, 477.562465668),
    (461, 477.749969482),
    (462, 477.937465668),
    (463, 478.124965668),
    (464, 478.312465668),
    (465, 478.499969482),
    (466, 478.687465668),
    (467, 478.874961853),
    (468, 479.062465668),
    (469, 479.249969482),
    (470, 479.437465668),
    (471, 479.624961853),
    (472, 479.812465668),
    (473, 479.999969482),
    (474, 480.187465668),
    (475, 480.374965668),
    (476, 480.562469482),
    (477, 480.749973297),
    (478, 480.937473297),
    (479, 481.124977112),
    (480, 481.312480927),
    (481, 481.499980927),
    (482, 481.687484741),
    (483, 481.874988556),
    (484, 482.062488556),
    (485, 482.249992371),
    (486, 482.437496185),
    (487, 482.624996185),
    (488, 482.812500000),
    (489, 483.000003815),
    (490, 483.187503815),
    (491, 483.375007629),
    (492, 483.562511444),
    (493, 483.750011444),
    (494, 483.937515259),
    (495, 484.125019073),
    (496, 484.312519073),
    (497, 484.500022888),
    (498, 484.687526703),
    (499, 484.875026703),
    (500, 485.062530518),
    (501, 485.250034332),
    (502, 485.437534332),
    (503, 485.625038147),
    (504, 485.812538147),
    (505, 486.000041962),
    (506, 486.187545776),
    (507, 486.375045776),
    (508, 486.562549591),
    (509, 486.750053406),
    (510, 486.937553406),
    (511, 487.125057220),
    (512, 487.312561035),
    (513, 487.500061035),
    (514, 487.687564850),
    (515, 487.875068665),
    (516, 488.062568665),
    (517, 488.250072479),
    (518, 488.437576294),
    (519, 488.625076294),
    (520, 488.812580109),
    (521, 489.000083923),
    (522, 489.187583923),
    (523, 489.375087738),
    (524, 489.562591553),
    (525, 489.750091553),
    (526, 489.937595367),
    (527, 490.125099182),
    (528, 490.312599182),
    (529, 490.500102997),
    (530, 490.687606812),
    (531, 490.875106812),
    (532, 491.062610626),
    (533, 491.250114441),
    (534, 491.437614441),
    (535, 491.625118256),
    (536, 491.812622070),
    (537, 492.000122070),
    (538, 492.187625885),
    (539, 492.375125885),
    (540, 492.562629700),
    (541, 492.750133514),
    (542, 492.937633514),
    (543, 493.125137329),
    (544, 493.312641144),
    (545, 493.500141144),
    (546, 493.687644958),
    (547, 493.875148773),
    (548, 494.062648773),
    (549, 494.250152588),
    (550, 494.437656403),
    (551, 494.625156403),
    (552, 494.812660217),
    (553, 495.000164032),
    (554, 495.187664032),
    (555, 495.375167847),
    (556, 495.562671661),
    (557, 495.750171661),
    (558, 495.937675476),
    (559, 496.125179291),
    (560, 496.312679291),
    (561, 496.500183105),
    (562, 496.687686920),
    (563, 496.875186920),
    (564, 497.062690735),
    (565, 497.250194550),
    (566, 497.437694550),
    (567, 497.625198364),
    (568, 497.812698364),
    (569, 498.000202179),
    (570, 498.187705994),
    (571, 498.375205994),
    (572, 498.562709808),
    (573, 498.750213623),
    (574, 498.937713623),
    (575, 499.125221252),
    (576, 499.312721252),
    (577, 499.500221252),
    (578, 499.687728882),
    (579, 499.875228882),
    (580, 500.062728882),
    (581, 500.250236511),
    (582, 500.437736511),
    (583, 500.625236511),
    (584, 500.812744141),
    (585, 501.000244141),
    (586, 501.187744141),
    (587, 501.375244141),
    (588, 501.562751770),
    (589, 501.750251770),
    (590, 501.937751770),
    (591, 502.125259399),
    (592, 502.312759399),
    (593, 502.500259399),
    (594, 502.687767029),
    (595, 502.875267029),
    (596, 503.062767029),
    (597, 503.250274658),
    (598, 503.437774658),
    (599, 503.625274658),
    (600, 503.812782288),
    (601, 504.000282288),
    (602, 504.187782288),
    (603, 504.375289917),
    (604, 504.562789917),
    (605, 504.750289917),
    (606, 504.937797546),
    (607, 505.125297546),
    (608, 505.312797546),
    (609, 505.500305176),
    (610, 505.687805176),
    (611, 505.875305176),
    (612, 506.062812805),
    (613, 506.250312805),
    (614, 506.437812805),
    (615, 506.625320435),
    (616, 506.812820435),
    (617, 507.000320435),
    (618, 507.187828064),
    (619, 507.375328064),
    (620, 507.562828064),
    (621, 507.750335693),
    (622, 507.937835693),
    (623, 508.125335693),
    (624, 508.312843323),
    (625, 508.500343323),
    (626, 508.687843323),
    (627, 508.875350952),
    (628, 509.062850952),
    (629, 509.250350952),
    (630, 509.437858582),
    (631, 509.625358582),
    (632, 509.812858582),
    (633, 510.000366211),
    (634, 510.187866211),
    (635, 510.375366211),
    (636, 510.562873840),
    (637, 510.750373840),
    (638, 510.937873840),
    (639, 511.125381470),
    (640, 511.312881470),
    (641, 511.500381470),
    (642, 511.687889099),
    (643, 511.875389099),
    (644, 512.062889099),
    (645, 512.250396729),
    (646, 512.437896729),
    (647, 512.625396729),
    (648, 512.812896729),
    (649, 513.000404358),
    (650, 513.187904358),
    (651, 513.375404358),
    (652, 513.562911987),
    (653, 513.750411987),
    (654, 513.937911987),
    (655, 514.125419617),
    (656, 514.312919617),
    (657, 514.500419617),
    (658, 514.687927246),
    (659, 514.875427246),
    (660, 515.062927246),
    (661, 515.250434875),
    (662, 515.437934875),
    (663, 515.625434875),
    (664, 515.812942505),
    (665, 516.000442505),
    (666, 516.187942505),
    (667, 516.375450134),
    (668, 516.562950134),
    (669, 516.750450134),
    (670, 516.937957764),
    (671, 517.125457764),
    (672, 517.312957764),
    (673, 517.500465393),
    (674, 517.687965393),
    (675, 517.875465393),
    (676, 518.062973022),
    (677, 518.250473022),
    (678, 518.437973022),
    (679, 518.625480652),
    (680, 518.812980652),
    (681, 519.000480652),
    (682, 519.187988281),
    (683, 519.375488281),
    (684, 519.562988281),
    (685, 519.750495911),
    (686, 519.937995911),
    (687, 520.125495911),
    (688, 520.313003540),
    (689, 520.500503540),
    (690, 520.688003540),
    (691, 520.875511169),
    (692, 521.063011169),
    (693, 521.250511169),
    (694, 521.438018799),
    (695, 521.625518799),
    (696, 521.813018799),
    (697, 522.000526428),
    (698, 522.188026428),
    (699, 522.375526428),
    (700, 522.563034058),
    (701, 522.750534058),
    (702, 522.938034058),
    (703, 523.125541687),
    (704, 523.313041687),
    (705, 523.500541687),
    (706, 523.688049316),
    (707, 523.875549316),
    (708, 524.063049316),
    (709, 524.250556946),
    (710, 524.438056946),
    (711, 524.625556946),
    (712, 524.813064575),
    (713, 525.000000000),
)

RECORDS_BY_TICK: dict[int, float] = dict(RECORDS)

# --------------------------------------------------------------------------
# Sampled records: (tick, m_positionY) -- a readability SUBSET of RECORDS
# --------------------------------------------------------------------------
# Fifteen of the 480 ``MOVE`` lines above, chosen to cover the first three
# steps, both endpoints, the quarter/half/three-quarter points, and the final
# clamped step. Values are the absolute m_positionY, not the delta.
#
# This tuple exists so that per-record parametrised tests stay readable at 15
# cases rather than 480. It is a strict subset of ``RECORDS`` and
# ``test_samples_are_a_subset_of_the_full_record`` enforces that.
#
# DO NOT INTERPOLATE BETWEEN THESE. The gaps run up to 100 ticks and the only
# interpolant is the predictor's own linear law. Use ``RECORDS_BY_TICK`` for a
# truth lookup at an arbitrary tick.

SAMPLES: tuple[tuple[int, float], ...] = (
    (234, 435.187502503),
    (235, 435.374999642),
    (236, 435.562502146),
    (240, 436.312501431),
    (300, 447.562496185),
    (353, 457.499988556),
    (400, 466.312479019),
    (473, 479.999969482),
    (500, 485.062530518),
    (592, 502.312759399),
    (600, 503.812782288),
    (700, 522.563034058),
    (711, 524.625556946),
    (712, 524.813064575),
    (713, 525.000000000),
)

# The probe emits a line only when a position changed, so there is no record at
# tick 233 (or at 714). Both of those steps left m_positionY untouched, which
# for 233 is the dead time and for 714 is the command sitting finished in
# m_unkVector560 until it is removed at 715.
IMPLIED_Y_AT_ACTIVATION = TARGET_Y_START     # 435.0, by absence of a record

# --------------------------------------------------------------------------
# The player's own x -- measured, but from a different run
# --------------------------------------------------------------------------
#
# PROVENANCE WARNING, read before quoting these two numbers. Every other value
# in this module was read out of a Geode log that is still on disk in
# backups/reference-logs/. These two were not: they come from the 2026-08-12
# GDRL_ENV run, whose 57,009 observations went over a shared-memory channel into
# a Python consumer and were never written to a log. The surviving Geode log for
# that session (``Geode 2026-08-12 12.14.21.log``) records only that the channel
# opened and that Python attached at tick 1. So these are transcribed from the
# session record in README/TODO, one step weaker than the MOVE records.
#
# What makes them usable anyway is that they are self-evidencing. Running the
# game's own accumulator, x[n+1] = float32(x[n] + float32(1.298250437)) from
# x = 0.0 at tick 1, reproduces BOTH values bit-exactly -- two 24-bit mantissas
# hit exactly by a model with no free parameters. A transcription error, a wrong
# origin, or a wrong per-tick advance would each break that.
# ``test_projection_groundtruth`` asserts it, so a corrupted paste fails loudly.
#
# These are the two ticks that bracket the trigger at x = 300:
PLAYER_X_RECORDS = (
    (232, 299.8955078125),          # short of the trigger
    (233, 301.1937561035156),       # past it -- and this is the activation tick
)
PLAYER_X_BY_TICK = dict(PLAYER_X_RECORDS)

# The origin convention, MEASURED over the same 4,653-tick run: player x is 0.0
# on tick 1, so x(t) = U * (t - 1). The alternative x(t) = U * t is wrong by a
# whole tick, and these are the two residuals that say so.
PLAYER_X_TICK_ORIGIN = 1
PLAYER_X_AT_ORIGIN_TICK = 0.0
PLAYER_X_MAX_DEV_FROM_LINE_T_MINUS_1 = 0.2597    # max|x - U*(t-1)|, 4653 ticks
PLAYER_X_MAX_DEV_FROM_LINE_T = 1.3135            # max|x - U*t|, i.e. one tick

# THE ACTIVATION RULE, and what the two records above settle.
#
# GD activates on the first INTEGER tick at which the player's own sampled x is
# at or past the trigger's x. Under x(t) = U*(t-1) the continuous crossing of
# x = 300 is at tick 232.080, so:
#
#   continuous crossing tick                = 232.080
#   ceil                                    = 233   <- matches the measurement
#   round                                   = 232   <- FALSIFIED
#   activation tick (measured, two ways)    = 233
#   first displacement (measured)           = 234
#
# The ceil-vs-round ambiguity recorded here previously was an artifact of the
# wrong origin: under x(t) = U*t the crossing is at 231.080 and "ceil+1",
# "round+2" and "floor+2" all land on 233, so nothing could be told apart. With
# the origin corrected the rule is not inferred at all -- the player's sampled x
# on both sides of the crossing is in the record above, and it is read directly.
#
# CROSSING_TO_ACTIVATION_TICKS is therefore 0, not 1. The 1 was the origin error
# double-counted as a latency. The one-tick DEAD TIME above
# (ACTIVATION_DEAD_TIME_TICKS: live at 233, first displacement at 234) is a
# different, downstream thing and is unaffected.
CROSSING_TO_ACTIVATION_TICKS = 0

# Still UNVERIFIED, and neither is decidable from this run:
#   * whether the comparison is >= or >; the recorded x never equals 300.
#   * whether GD quantises against the float32-accumulated x or the continuous
#     line. They differ by at most 0.028 ticks over a 400-tick lookahead, and
#     the recorded crossing sits at frac 0.08 -- nowhere near a boundary.
ACTIVATION_TIE_BREAK_UNVERIFIED = True
