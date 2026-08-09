# PRD Scrutiny — 2026-08-09

> **Note:** All `PRD.md:line` references below refer to the v1 document, preserved as `PRD-v1-household.md`.

Adversarial review of `PRD.md` v1.0 against the actual prototype (`plan.py`, `serve.py`, `app.html`, `library/*.yaml`).
Seven independent review lenses + independent code re-verification + web fact-checking against FDA/USDA sources.
Every finding marked ✅ was independently re-confirmed with file:line citations; empirical claims (nondeterminism, mass floor) were additionally reproduced by running the prototype.

**91 findings: 19 critical, 47 major, 25 minor.** Overlap between sections is corroboration — several defects were found independently by multiple lenses.


## The PRD contradicts the prototype it canonizes

### [CRITICAL] ✅ build_week is nondeterministic (PYTHONHASHSEED) — M0 gate is unpassable as written

plan.py:537 seeds each day's plate search with `seed=(d * 31 + hash(pname)) % 9973`. `hash()` on a str is salted per interpreter process (PYTHONHASHSEED), so the same command with the same --seed yields a different week every run. Verified: `(3*31 + hash('jimbo')) % 9973` across three python3 invocations gave 340, 7727, 2129. This directly falsifies PRD §5.7 'Determinism: seed all randomness', §8.8 'Everything seeded', and the §12.1 property 'same inputs + seed ⇒ identical plan'. The M0 gate is '§12.1/12.3 tests pass against plan.py unmodified' — the determinism test cannot pass against plan.py unmodified, and every 'golden' regression number in §5/Appendix A was captured under an unreproducible hash salt. serve.py inherits the same flaw across server restarts (stable only within one process).

> **Question for Devon:** Does M0 get a one-line dispensation to fix this seed (making it the only allowed modification), or does the determinism property get dropped from the M0 gate? The PRD currently demands both 'unmodified' and 'deterministic', which is impossible.

### [CRITICAL] ✅ plan.py and serve.py compute different batches, cook minutes, and costs for identical inputs — 'the validated solver behavior' is two behaviors

PRD §5.8 says the double-counting bug was fixed ('batches now split by which days they feed') and §1.3 calls plan.py 'Working, validated'. But the fix exists only in serve.py. plan.py render() (line 579) computes batches = ceil(total weekly demand / yield_g) per component; serve.py (lines 101-110) computes per-session ceils and sums them (always ≥ the CLI number). Downstream, `made`, `leftover`, `bought` (menu_cost at those batches), shares, and the shopping list all diverge between the two adapters. Cook time diverges three ways: (1) plan.py's 'Hands-on total' (render line 568) is `menuinfo['active_min']` from score_menu, i.e. estimate_batches — not actual batches; (2) serve.py's `cook_minutes` is sum of per-session amortized cook_minutes (lines 120-122, 156); (3) serve.py's per-component `cook[].active = active_min × b` (line 116) is linear with no batch_time_factor, and app.html:387 uses that as its fallback. G2 says 'preserve validated solver behavior' — there is no single behavior to preserve, and §5.8's headline number (≈5h44m, 45 batches) came from serve.py while M0 tests target plan.py.

> **Question for Devon:** Which adapter's batch/cook-time math is the reference for M0 goldens — plan.py (which still has the bug §5.8 claims is fixed) or serve.py?

### [MAJOR] ✅ CLI --force is parsed but never used — pins are silently dropped

plan.py:715 defines `--force` ('components that must be on the menu'), but `a.force` is never referenced anywhere in main() (verified by grep: only a.budget, a.mass, a.exclude, a.n, a.menu, a.seed are consumed). choose_menu() supports `must=` and serve.py passes it (serve.py:66,80), but the CLI call at plan.py:745 omits it. PRD §9.1 advertises `--force a,b` on the menu command as an existing key flag. A user pinning a dish via CLI gets no error and no pin — a textbook I10 ('no silent drops') violation in the reference implementation.

### [MAJOR] ✅ score_menu/frontier mix scales: waste at 1-batch, cost at estimated batches — §5.6's 'empirical findings' are proxies, and the REG test would lock the proxy in

Inside score_menu (plan.py:341) perishable waste comes from `purchase(comps, ing, chosen)` with batches=None → 1 batch of everything; three lines later (360-363) the budget cost uses estimate_batches (calorie-scaled k). The same info dict then feeds `plan.py menu` output and frontier(). frontier() (plan.py:687-690, serve.py:184-190) never calls build_week — its 'spend' is the estimate-batches proxy and its 'waste' is the 1-batch proxy. So PRD §5.6's claims — 'actual spend stays $264–$338', 'waste at $200 ceiling: 2,988g; at $320: 2,188g' — are neither actual spend nor plan-scale waste, and the §12.3 test 'frontier is non-flat and non-increasing in waste' would enshrine a mixed-scale artifact as a regression contract. estimate_batches additionally applies one uniform k to every component including 227g cheese accents and ignores max_batches_per_component=3 (plan.py:307-318), so the estimated spend can be a spend the week builder is forbidden from producing.

> **Question for Devon:** Should frontier points be re-derived from real build_week solves before their shapes become regression tests, or is the PRD knowingly testing the search proxy?

### [MAJOR] min_lean_anchors, meals_per_day, min/max_components_per_day exist in people.yaml and PRD §7.3 but are never read by any code

Grep of plan.py and serve.py: `min_lean_anchors`, `meals_per_day`, `min_components_per_day`, `max_components_per_day` appear nowhere. PRD §7.3 presents all four as current schema ('As today: … meals_per_day, min/max_components_per_day … settings: … min_lean_anchors'). Worse, there are three conflicting sources of truth for lean anchors: people.yaml:46 says `min_lean_anchors: 1` (annotated 'this is not a preference, it is structural'), score_menu hardcodes `len(lean) >= 2` (plan.py:396), and PRD §5.3 says '≥2'. The LP also has no constraint tied to meals_per_day or components-per-day at all — nothing stops a 1-component or 12-component plate except serve_g bounds. Schema validation (M1) built from §7.3 would validate fields the engine ignores.

> **Question for Devon:** Are these four fields spec (to be wired up in M1) or dead weight to delete? The PRD's 'as today' phrasing claims they already work.

### [MAJOR] ✅ The shelf-life 'stagger' check is a broken boolean expression — it only checks that one lean keeps ≥4 days

plan.py:397-398: `pen += 0 if len({comps[i]["keeps_days"] >= 4 for i in lean}) and any(comps[i]["keeps_days"] >= 4 for i in lean) else 8000`. The first operand is `len()` of a set of booleans — truthy whenever `lean` is non-empty — so the whole condition reduces to 'lean is non-empty AND at least one lean keeps ≥4 days'. There is no check that they don't all expire together, which is what PRD §5.3 claims the rule is ('not all expiring together', 'staggered shelf life') and what the comment above the line says. The `len(...)` fragment looks like a mangled attempt at `len(...) == 2` (i.e., both short- and long-keepers present). REG-04 as scoped in §12.3 would canonize the wrong behavior.

### [MAJOR] ✅ Doctor lacks three of the §8.3 capabilities the PRD describes: starch count, carb-ceiling check, and shelf-life-stagger ablation

PRD §8.3(b) says doctor does 'count starches; carb-ceiling check vs 1.45× (§5.2); lean anchors' shelf-life stagger (§5.3)'. Actual doctor() (plan.py:208-272) does: per-person feasibility with tolerance ladder, leanest-first main ablation, lean-anchor list, and a ≥250 kcal/100g carrier count. No starch count, no 1.45× carb-ceiling check, no stagger analysis — those live (only, and partially broken, see stagger finding) in score_menu, where failures surface as opaque score penalties, not explained diagnostics. §5.3's claim that 'doctor proves the current requirement by ablation' is true only for lean-count, not stagger. §8's framing ('keep the prototype's proven logic') presents these as existing behavior to preserve; two of them would be new work and should be scoped as such.

### [MAJOR] ✅ replate has no day parameter — Rebalance ignores shelf-life availability, and locked grams bypass serve_g bounds and unit snapping

serve.py:165-173 replate() solves `plate(people[pn], comps, menu, locked=…)` against the full menu with no day index, so the LP can add any menu component to any day — including ones past shelf life on that day (guacamole, keeps 2d, onto day 7), violating the §5.4 availability rule the Eat tab's day cards imply. PRD §9.2 specs replate as 'One person, pinned portions → re-solved day' without noticing the endpoint cannot know which day it is. Separately, locked values enter `fixed` (plan.py:173) before both bound enforcement and snapping (the snap loop at plan.py:179-182 skips `i in fixed`), so a locked 1000g of a 150g-max accent, or 100g of a 71g-unit tortilla, passes through untouched. The §12.1 properties 'every portion within serve_g bounds' and 'unit_g portions are exact multiples' fail on any replate output containing such a lock — M0 must decide whether locks are exempt or clamped.

### [MAJOR] CLI `week --menu` computes hands-on time at one batch per recipe — the exact '172 min' failure §5.8 says was fixed

plan.py:743: when --menu is given, `score_menu(comps, ing, menu, settings)` is called WITHOUT the people argument, so cook_minutes runs on `{i: 1 for i in chosen}` (plan.py:343-344) — each recipe counted once — and all per-person structural checks (starch count, carb ceiling, lean anchors) are skipped. The report's 'Hands-on total' then shows the wishful single-batch number PRD §5.8 explicitly calls wrong. serve.py's explicit-menu path passes people (serve.py:71), so the two adapters disagree again on the same input. Also, budget_ceiling in per_person mode returns None when people={} (plan.py:333-334), silently disabling the budget penalty on this path.

### [MINOR] ✅ Unit snapping can violate serve_g bounds whenever min/max is not a multiple of unit_g — current library is only accidentally safe

plan.py:182: `fixed[i] = round(val / u) * u` rounds to nearest, so with min=90/unit=71 a val of 95 snaps to 71 (< min), and with max=110/unit=40 a val of 110 snaps to 120 (> max). Every discrete component currently in components.yaml happens to have min/max at or safely near unit multiples (tortilla 71/284, gordita 85/340, meatballs 80/400, wings 90/500→495), so no test today would catch it — but §12.1 promises to property-test exactly these bounds, and the first ingested discrete component with non-multiple bounds breaks the property. Snap should clamp into [ceil(min/u), floor(max/u)] units.

### [MINOR] PRD §10 claims the macro-colors-reused-for-cuisines bug is 'fixed; keep fixed' — app.html still maps cuisines to the macro palette

app.html:233: `CU = {mexican:'--s2', cn_am:'--s1', italian:'--s3', crossover:'--accent'}` colors menu-chip edges by cuisine using the same three series hues that the Eat-tab legend (lines 202-205) defines as Protein/Fat/Carb, and that the Plan-tab frontier charts use as their line color (--s1, lines 431-434) on the same screen as the cuisine-colored chips. PRD §10 says 'series colors mean macros only (P/F/C) — never reuse them for cuisines on the same screen (fixed bug; keep fixed)'. Either the fix was never applied to this copy of app.html or the PRD is describing a different artifact; as written, M0's 'validated' UI violates its own constitution.

### [MINOR] ✅ Dead code and stale artifacts inside the 'Working, validated' reference implementation

plan.py: `assign_week` (line 489) is never called by anything — an entire alternate week-assembly path left in the reference file; `itertools` (line 24) imported, never used; `doctor()` takes a `settings` parameter it never reads (line 208); `frontier()` builds and returns `seen_menus` which both callers discard (plan.py:692, main:730). None of this breaks behavior, but M0 freezes this file as the behavioral oracle, and dead alternates like assign_week invite the extractor to port the wrong function.

### [MINOR] ✅ Default drift across adapters: n=10 vs 12, cook_days fallback [0,3] vs shipped [0,4], stale docstrings

CLI default `--n 10` (plan.py:707) vs serve.py default n=12 (line 64) and app.html boot n=12 (line 503); PRD §12.5's performance contract is quoted at n=12, so CLI-default runs aren't the benchmarked configuration. Code-level cook_days fallback is [0,3] (plan.py:479, serve.py:85,160) while people.yaml ships [0,4] — and §5.4/people.yaml themselves say [0,3] strands day 7, so the hardcoded fallback is the known-bad configuration. sessions_for's docstring ('session 1 on day 4 (0-indexed 0 and 3)') describes the old setting. Frontier defaults disagree three ways: CLI 400:700:50 (7 pts), serve 250:600:25 (15 pts), app.html 220:620:25 (17 pts — the only one matching §12.5's '17 points' test).

### [MINOR] ✅ Tolerance advice contradiction shipped in three places: people.yaml and SKILL.md say 'loosen first', PRD says 'last resort'

people.yaml:4-5: 'Loosen this first when the solver says infeasible. It is the cheapest knob.' SKILL.md infeasibility list item 1: 'Loosen tolerance … Cheapest knob.' PRD §9.4 item 4: 'tolerance loosen = last resort, cheapest knob first' (where 'cheapest knob' now means something else entirely). The PRD schedules SKILL.md v2 for M4, but people.yaml's comment ships untouched through every milestone and is what a Claude operator editing the library will actually read. Related soft inconsistencies: PRD §5.5 says variety caps apply to 'mains/sauces' but build_week caps role=='main' only (plan.py:524-526, accents/sauces uncapped); score_menu's global starch check is ≥2 (plan.py:352) while the per-person rule and PRD §5.2 say ≥3; serve.py's budget override (line 29-30) can only express shared mode, so §9.2's generic override block cannot set per_person budgets via the API.


## The PRD contradicts itself

### [CRITICAL] ✅ Budget/spend numbers in §5.6 contradict Appendix A — and both feed regression tests

PRD.md:222 (§5.6) states the $200→$650 sweep shows 'actual spend stays $264–$338' with a plateau '≈ $340 (above it, money buys nothing)'. Appendix A (PRD.md:750-751) states 'current plans $322–$345 bought'. $345 exceeds both the sweep's claimed spend ceiling ($338) and the plateau ($340) — by the doc's own claim, spend above $340 should not occur. Either 'spend' and 'bought' are different quantities (never defined; §9.2 line 522 has cost{bought,eaten,...} with no 'spend'), or one set of numbers is stale. §2 G2 and §5's preamble (PRD.md:184-185) say these empirical numbers become named regression tests, so the contradiction gets baked into the test suite.

> **Question for Devon:** Which is authoritative — the sweep range ($264–$338) or Appendix A ($322–$345) — and is 'spend' identical to 'bought'?

### [CRITICAL] ISO-week plan key is undefined for a Sunday-start week — every Sunday-start week straddles two ISO weeks

The household week starts Sunday: §5.4 (PRD.md:210) has cook day 0 = Sun, and §7.3 (PRD.md:372) defines cook_days as 0-indexed day starts. But plans are keyed by ISO week — plans/2026-W32.yaml (PRD.md:297, §7.6 PRD.md:412), 'mealplan accept --week 2026-W32' (PRD.md:506), and GET /api/plan/current returns 'last accepted artifact for this ISO week' (PRD.md:529). ISO 8601 weeks run Mon–Sun, so a Sunday-start plan week always spans two ISO weeks: Sun 2026-08-09 is ISO W32 day 7, while its Mon–Sat (Aug 10–15) are W33 (verified with Python isocalendar). Consequences the PRD never resolves: (a) the key for the week cooked on Sun Aug 9 is ambiguous (W32 or W33?); (b) /api/plan/current computed on cook-day Sunday resolves to the ISO week that is ending, so the just-accepted plan for the coming week is not 'current'; (c) history.yaml weeks_ago decay (§7.5) inherits the same off-by-one. No section defines the household-week→ISO-key mapping.

> **Question for Devon:** Define the convention: is a plan keyed by the ISO week containing its Sunday cook day, or by the ISO week containing its Mon–Sat majority?

### [MAJOR] M0 gate depends on M2 features: §12.1 property tests cannot pass against the unmodified prototype

M0's 'done when' (PRD.md:711) is '§12.1/12.3 pass against plan.py unmodified'. But §12.1 (PRD.md:678-679) includes 'shopping packs cover ingredient need after pantry deduction (I7)' — and §1.3 (PRD.md:91-92) states 'history/pantry not yet implemented' in the prototype; pantry.yaml is explicitly NEW in M2 (§7.4, PRD.md:379). So the M0 acceptance criterion references behavior that does not exist until two milestones later. Either the pantry clause must be scoped out of the M0 run (vacuous with empty pantry — but then it's untested until M2 and nothing says so), or the gate is unsatisfiable as written.

> **Question for Devon:** Should §12.1's pantry clause be marked M2-only, with M0 running it against an empty/absent pantry?

### [MAJOR] §5.6 contradicts itself on whether the budget ceiling is hard: 'unsatisfiable below $264' yet waste is measured at a $200 ceiling

PRD.md:222-225: 'Structural floor ≈ $264/wk (below it, macros+exclusions+variety are unsatisfiable)' — yet the very next sentence reports 'Perishable waste at $200 ceiling: 2,988g', a measured outcome of a plan produced under a ceiling the doc just called unsatisfiable. These can only coexist if the ceiling is soft (§8.4's 'budget ceiling penalty', PRD.md:456) and spend overshoots the ceiling — but then 'unsatisfiable' is the wrong claim, and the frontier's per-point 'feasible' flag (§8.7, PRD.md:482-483) has undefined semantics (infeasible vs ceiling-violated-but-fed). §12.3's frontier regression ('non-flat and non-increasing in waste', PRD.md:691-692) inherits this ambiguity: what does the $200 point assert?

> **Question for Devon:** Is the budget ceiling a hard constraint (then the $200 waste number describes an infeasible plan) or a penalty (then delete 'unsatisfiable' and define what frontier feasible:false means)?

### [MAJOR] Variety-cap scope stated three different ways, including a role ('sauces') that does not exist in the role enum

§5.5 (PRD.md:218-219): 'Cap mains/sauces at max_days_same_component; exempt starches.' §7.3 (PRD.md:373): 'max_days_same_component (mains only, I5.5)'. §8.5 (PRD.md:465): 'mains-only day cap with relax ladder'. The role enum (§7.2, PRD.md:353) is 'main | starch | veg | accent | drink' — there is no 'sauce' role, so 'cap sauces' is unimplementable as specced; presumably sauces are 'accent', but §7.5 (PRD.md:404-405) puts accents under the *repeat penalty*, a different mechanism, and never under the day cap. REG-06 (PRD.md:690) tests 'starch-exempt variety cap' — whether accents/veg are capped or exempt changes the test and the plans. Also unstated: what the day cap does to 'veg' and 'drink' roles.

> **Question for Devon:** Exactly which roles does max_days_same_component apply to: mains only, or mains+accents? And what covers veg/drink?

### [MAJOR] ⚠️ (partially verified) Performance requirements are mutually inconsistent: ~6s per pipeline vs 17-point frontier in <25s, driven live from the UI under a <10s budget

§5.7 (PRD.md:236) fixes the full pipeline at ≈6s and UI round-trip p95 <10s. §12.5 (PRD.md:700-701) requires 'frontier 17 points < 25s' — ~1.5s/point, i.e., 4× faster than one pipeline, with no explanation of what a frontier point omits (§8.7, PRD.md:481-483, implies each point does a menu solve + costing). §10 (PRD.md:596-597) additionally requires Plan-tab frontier charts 'from live sweep' inside a UI whose stated round-trip budget is p95 <10s — a 25s sweep violates it. Separately, §12.3 (PRD.md:692) sets a third pipeline bound, '<30s in CI', vs §12.5's 'p95 <10s (…M-series or CI-equivalent)' — two different pipeline time limits for the same environment. None of the four numbers are reconciled.

> Verification note: All numbers accurately quoted (PRD.md:236-237 ≈6s/p95<10s; PRD.md:701 '17 points < 25s'; PRD.md:596-597 'from live sweep'), and the live-sweep-inside-a-<10s-UI-budget tension is real; but a frontier point in code (plan.py:676-692, serve.py:176-191) skips build_week/costing entirely, so ~1.5s/point is not strictly 'one pipeline 4× faster' — inconsistency is genuine only for the UI sweep, unstated omissions explain the rest.

> **Question for Devon:** What does a frontier point actually compute, and which pipeline bound (10s p95 or 30s) governs CI?

### [MAJOR] ✅ Day indexing mixes 0-indexed and 1-indexed in the same sentences; REG-05/REG-06 test specs are off-by-one ambiguous

§7.3 (PRD.md:372) declares cook_days 0-indexed, and §5.4 (PRD.md:210) uses [0,3]=Sun/Wed accordingly — a 7-day week is days 0–6. Yet the same sentence says cook days [0,3] 'strand day 7' (PRD.md:210-211), §5.5 says 'days 5–7 went empty' (PRD.md:217), and the regression specs codify it: REG-05 'shelf-life valley day-7 explained hole' and REG-06 'fills days 5–7' (PRD.md:689-690). Day 7 does not exist in a 0-indexed 7-day week. The availability formula '0 ≤ d − start(s) < keeps_days' (PRD.md:212) only strands the last day under [0,3] if that day is index 6 (Sat) with 3-day keeps — so the prose is 1-indexing days while the config 0-indexes them. Since these become named tests (G2), the off-by-one must be pinned before M0.

> **Question for Devon:** Confirm: 'day 7' throughout means index 6 (Saturday), and all REG specs should be restated 0-indexed?

### [MINOR] §5's preamble overclaims: 'each finding below is a named regression scenario', but 5.6–5.9 have no REG IDs and REG-03 is not a §5 finding

PRD.md:184-185 says every §5 finding is a named regression scenario per §12.3. In fact §5 assigns REG-01, 02, 04, 05, 06 to findings 5.1–5.5; REG-03 is defined only inside invariant I5 (PRD.md:161) — so the §5 numbering skips 03 and the preamble's claim is false for it. Findings 5.6 (frontier) and 5.7 (MILP timeout) are only partially covered by §12.3's trailing 'Plus:' clause (PRD.md:691-692), and 5.8 (batch-scaled cook time) and 5.9 (volume floor) have no regression test at all, despite both being 'hard requirements'/'load-bearing' claims elsewhere (§8.6, §11.5 acceptance).

> **Question for Devon:** Should 5.8 and 5.9 get REG IDs and tests (e.g., cook-minutes formula golden; binary-searched mass floor band), or be explicitly exempted?

### [MINOR] Dangling cross-reference 'I5.5' — no such invariant exists

PRD.md:373 (§7.3): 'max_days_same_component (mains only, I5.5)'. Invariants run I1–I11 (§4); there is no I5.5. Almost certainly intended is §5.5 — but note §5.5 says 'mains/sauces', not 'mains only' (see the separate variety-cap finding), so the fixed reference still points at contradicting text.

### [MINOR] Schema fields and settings declared but consumed nowhere in the engine spec (and one API/setting name mismatch)

Orphans in the data model vs §8: (a) components.batch_g 'portioning granularity when cooking' (PRD.md:358) — §8.6 computes batches from yield_g only (PRD.md:470-471); batch_g is never referenced again. (b) people meals_per_day and min/max_components_per_day (PRD.md:369-370) — the plate LP spec (§8.2, PRD.md:433-441) enforces neither; no meal-level structure exists anywhere. (c) role 'veg' (PRD.md:353) appears in the enum and nowhere else — no role minimum (§8.4 lists mains/starches/accents only, PRD.md:455), no cap treatment. (d) History-penalty parameters w_repeat=1200, decay=0.5, horizon=4 (PRD.md:404-405) live in no schema — §7.3's settings list omits them, so they are unconfigurable-by-spec while §7.3 claims budget-like things are 'never hardcoded'. (e) §9.2 response field 'active_budget' (PRD.md:522) vs setting 'active_min_budget' (PRD.md:374). (f) §8.4 hardcodes 'lean-anchor count ≥2' (PRD.md:457) while §7.3 defines a min_lean_anchors setting (PRD.md:374).

### [MINOR] ✅ §9.3's absolute 'no tool fetches URLs' is contradicted by the §11.8 USDA network flow

§9.3 safety rails (PRD.md:554-555): 'no tool shells out or fetches URLs — recipe research happens in the Claude session, only structured data crosses the boundary (I9).' §11.8 (PRD.md:658-661) specs a USDA FDC helper that is 'network-permitted only in this flow' and fetches candidate FDC entries — but never says which interface performs the fetch. If it is a CLI command or MCP tool (the natural M6 reading, and §9.4 step 3 says 'add_ingredient with USDA FDC lookup preferred'), it violates §9.3's blanket rule. The exception needs to be carved into §9.3 or the fetch explicitly pushed to the Claude session side.

> **Question for Devon:** Does the FDC lookup happen inside a mealplan tool/CLI (requiring a §9.3 exception) or only in the Claude session?

### [MINOR] Arithmetic slip in §5.2: the starch-mass swing is 4.31 lb, stated as 4.4 lb

PRD.md:196-198: tortillas 1,153g vs refried pintos 3,108g per 588g carbs → swing 1,955g = 4.31 lb, but the bolded claim says '4.4 lb/day swing'. Trivial in isolation, but this exact sentence seeds REG-02 and Appendix A repeats the same figures (PRD.md:753), so the golden numbers should be recomputed once from one source. (Related nit: §5.8's '≈5h44m/week (two ~2h50m sessions)' — 2×2h50m = 5h40m.)

### [MINOR] §7.6 reproducibility guarantee omits the seed from the inputs hash

§7.6 (PRD.md:413-416) promises 'same inputs hash ⇒ same plan' with the hash defined as 'library file hashes + overrides'. But menu search and week building are seed-dependent (§8.4 signature takes seed, PRD.md:450; §8.5 uses 'randomized weights, seeded', PRD.md:464), and seed is a sibling of the overrides in the API block (§9.2, PRD.md:518) — it is not stated to be an 'override'. Unless the seed (and n) are explicitly included in the hash, the M2 acceptance criterion ''plans/' round-trip reproducible by inputs hash' (PRD.md:713) is unverifiable.


## The physical model of food has holes

### [CRITICAL] ✅ Every raw meat is past its keeps_days when session 2 cooks it — nothing in code or PRD enforces raw-ingredient freshness at cook time

The PRD commits to one shopping trip on day 0 (PRD.md:44 'shopping done by Devon in one trip') and cook sessions on days 0 and 4 (people.yaml:47 `cook_days: [0, 4]`). Session-1 cooking therefore uses raw ingredients that are 4 days old. Check ingredients.yaml against that: shrimp_raw keeps 2 (line 25), chicken_breast 3 (22), chicken_thigh 3 (21), chicken_wings 3 (23), ground_beef_85 3 (15), flank_steak 3 (17), ground_turkey_93 3 (24) — all dead by day 4. Even beef_chuck (16), pork_shoulder (18), and italian_sausage_hot (20) at keeps_days 4 are expired under the code's own convention (`age < keeps_days`, plan.py:484). The only meats that survive to day 4 are eggs and chorizo_pork — and chorizo appears in zero components. Yet the system actively schedules session-1 cooking of these: shrimp_al_pastor (cooked keeps 3) can only reach days 4–6 via a day-4 cook of 2-day shrimp bought on day 0; salsa_verde_chicken — added specifically to 'span the gap' (components.yaml:499-502) — needs a session-1 batch of 4-day-old raw chicken breast. serve.py's session split (serve.py:94-107) happily assigns this demand to session 1. Nothing consumes raw keeps_days beyond printing it in the shopping table (plan.py:293-294, 664-667). PRD §7.1 (line 341) claims keeps_days 'drives what can share a week' — false; §8.5 and §8.7 never mention raw freshness; §12's property tests have no raw-freshness invariant. The PRD's claim of 'real shelf-life physics' (PRD.md:17-18) holds only for cooked components; the raw half of the physical model does not exist, and 'working, validated' plans are physically uncookable as written.

> **Question for Devon:** Is the real-world assumption a second mid-week shopping trip or freezing raw meat on day 0? Whichever it is, where in §7/§8 does that state get modeled, and which regression test catches a session-1 batch whose raw inputs are expired?

### [CRITICAL] ✅ Session attribution in serve.py contradicts plan.py's availability model, and the CLI doesn't split sessions at all — batch counts, cook minutes, and shopping lists diverge between adapters

Two different physical models coexist. plan.py:482-486 `available_on` says a component cooked in ANY session with `0 <= d - start < keeps_days` feeds day d — so salsa_verde_chicken (keeps 5) cooked day 0 legitimately feeds day 4. serve.py:87-92 `session_of` attributes day-d demand to the LATEST session with `start <= d` — so that same day-4 portion is charged to session 1, forcing a fresh batch (serve.py:105-107 per-session ceil) even when session-0 leftovers cover it. Concrete: 300g of salsa_verde_chicken on day 4 → serve.py cooks a new 1,200g batch on day 4 (phantom 1,361g chicken-breast purchase, +20 min session-1 time, 900g phantom leftover), while build_week's availability logic justified the placement via the session-0 batch. Same for every keeps>=5 component on day 4 and every keeps-14 accent (scallion_ginger_oil, flour_tortilla) on days 4-6. Meanwhile plan.py's CLI path computes batches as a single `ceil(total_need/yield)` with no session split at all (plan.py:577-580), so CLI and HTTP produce different batch counts, different `purchase()` inputs, different costs and cook minutes for the same week. PRD §5.8 (lines 244-246) claims the double-listing bug was 'fixed' and 'batches now split by which days they feed' — that fix exists only in serve.py and is itself inconsistent with the availability rule. Appendix A's '45 batches, 5h44m' is adapter-dependent, and M0 (§13) writing tests 'against plan.py unmodified' would freeze the un-split CLI behavior while §5.8 describes the serve.py behavior.

> **Question for Devon:** Which adapter produced the §5.8/Appendix-A numbers, and what is the intended attribution rule when a session-0 component's shelf life overlaps session 1 — leftover-first, or always-recook?

### [MAJOR] ✅ `freezes` is recorded but ignored, yet the system's own output recommends freezing, and REG-04/05 lock in artifacts of a freezer-less model

PRD §7.2 (line 360) declares `freezes` a 'future lever; v1 records it, week builder ignores it.' Three consequences: (1) plan.py:609-611's empty-day fix text tells the user to 'freeze half of a batch on cook day' — advice the model cannot represent, so following it desynchronizes reality from every downstream number (availability, batches, waste). (2) The entire §5.3/§5.4 edifice — ≥2 lean anchors with staggered shelf life, [0,3] strands day 7, salsa_verde_chicken added to span the gap — is only true in a world without a freezer. 16 of 26 components carry `freezes: true`; freezing half a session-0 batch dissolves the shelf-life valley, meaning REG-04 and REG-05 (§12.3) enshrine as regression tests what may be artifacts of an incomplete physical model rather than household truths. (3) It is also the only physically plausible resolution to the raw-ingredient problem above, but `freezes` lives on components, not ingredients (ingredients.yaml has no freeze field at all), so 'freeze the day-0 chicken for Thursday' is unrepresentable even in the future-lever schema. The §7.4 pantry spec likewise models only fridge residual life ('cooked: 2026-08-02'), no frozen state.

> **Question for Devon:** If the household owns a freezer, why are REG-04/REG-05 being locked in as permanent regressions instead of as 'no-freezer-mode' scenarios?

### [MAJOR] ✅ Bone-in wings: macros are per edible 100g but grams are bone-in — protein overstated ~50%, and I2 forbids the only workaround

ingredients.yaml:23 chicken_wings: 203 kcal / 18.3p per 100g — that is the USDA edible-portion figure (meat+skin; USDA lists wing refuse at roughly a third bone). But the schema says macros are 'per 100g, as purchased' (PRD §7.1 line 336) and pack_g 2270 is a bone-in bag. mango_jalapeno_wings (components.yaml:436-460) multiplies 2,270 bone-in grams by edible-portion density: ~415g protein attributed when the edible meat (~1,500g) carries ~275g — a ~50% overstatement flowing into the derived per-100g (I2) and every plate. The error compounds at serving: unit_g 45 is a bone-in wing segment (~25-30g meat), and the Eat tab logs plate weight bone-in, so a person 'eating 450g of wings' is credited macros for 450g of boneless meat+skin. yield_g 1600 from 2270 raw is consistent with bone-in cooked weight, confirming bone never leaves the mass. No field models edible/refuse fraction anywhere (§7.1, §7.2), and I2 ('No override field. Ever.', PRD.md:150-152) plus the USDA-lookup flow (§11.8, which will fetch more edible-portion figures) guarantee the same bug for every future bone-in or shell-on ingredient (the SKILL yield rules in §9.4 don't mention refuse either). A single `grams` value cannot serve both purchasing (needs bone-in) and macros (needs edible).

> **Question for Devon:** Does the schema get an edible_fraction/refuse field on ingredients (and a plate-weight convention for bone-in components), or is bone-in food banned from the library?

### [MAJOR] ✅ Cooked keeps_days=5 for rice, shredded chicken, and braises is at or past USDA leftover guidance — and those exact values are what make the [0,4] architecture work

USDA/FoodSafety.gov guidance for refrigerated leftovers (cooked poultry, cooked meat, cooked rice) is 3–4 days. components.yaml assigns keeps_days: 5 to cilantro_lime_rice (135), jasmine_rice (300), salsa_verde_chicken (507), birria_chuck (73), carnitas (94), picadillo (35), sausage_sugo (354), refried_pintos (153). Under the availability convention (plan.py:484, served while age < keeps), keeps 5 means eaten at age 4 days — the outer edge of guidance with zero margin, and the yaml semantics claim edibility through 4.99 days. This is not incidental: PRD §5.4 (line 212) says '[0,4] works with 3–5-day components,' i.e., the gap-spanning that REG-05 tests and that justified adding salsa_verde_chicken depends precisely on the most optimistic shelf-life values in the file (5-day cooked chicken, 5-day cooked rice — rice being the canonical B. cereus case). Set those to the guidance-conservative 4 (age <= 3) and day 4 pre-session-1 plates lose every session-0 main, the shelf-life valley returns, and REG-04/REG-05's expected outcomes flip. A PRD selling 'real shelf-life physics' and 'honest accounting' should state whose shelf-life numbers these are and show the sensitivity of the architecture to them; §12 has no test at guidance-conservative values.

> **Question for Devon:** What is the provenance of the 5-day values, and does the feasibility story survive keeps_days=4 on cooked poultry/rice?

### [MINOR] ✅ Code default cook_days=[0,3] is the PRD's documented failure configuration, and the sessions_for docstring contradicts the shipped config

plan.py:479 and serve.py:85 (also serve.py:160) default to `cook_days [0,3]` when the settings key is absent — exactly the configuration §5.4/REG-05 documents as stranding day 7. Only people.yaml:47 saves it with [0,4]. A missing or mistyped key silently reintroduces the known-bad schedule with no warning, violating I10's no-silent-anything posture. The plan.py:477 docstring ('Session 0 cooks on day 1, session 1 on day 4 (0-indexed 0 and 3)') describes the old schedule and contradicts both the shipped config and people.yaml's comment ('[0,4] (Sun/Thu)'). During M1 extraction the default should become the validated schedule or a hard schema-required field.

### [MINOR] ✅ Cooked-component shelf clock resets at cook time regardless of ingredient age; day-0 produce enters session-1 cooking at zero margin

`available_on` (plan.py:482-486) measures keeps_days from the session start, treating a day-4 batch as identical to a day-0 batch. But its inputs are day-0 purchases: avocado, cilantro, mango, pineapple all keep 5 days (ingredients.yaml:54, 68, 76, 77), so session-1 batches use them at age 4 of 5 — one hot afternoon from spoiled. Worse, the model then grants the output a full fresh window: guacamole made on day 4 from age-4 avocados 'keeps 2 days' through day 6, and shrimp_al_pastor made from (already-expired, see critical finding) shrimp gets a fresh 3-day clock. Cooked keeps_days should be capped by remaining raw life, or the PRD should state explicitly that cooking is assumed to reset perishability — currently §5.4 and §8.5 are silent, and the §7.4 pantry design ('effective keeps = keeps_days - age') shows the team knows how to model age but applied it only to pantry carryover, not to the week's own timeline.


## Product journeys the PRD never specs

### [CRITICAL] Multi-device journeys are physically impossible: server binds 127.0.0.1, no cloud, no auth — yet both actors are specced on phones

serve.py:247 binds `ThreadingHTTPServer(("127.0.0.1", PORT), H)`. PRD.md §9.2 (line 532) mandates 'No auth (localhost bind only); never bind 0.0.0.0 by default' and §2 non-goals (line 119) forbid 'Cloud anything: no accounts, no sync, no deploy.' But §3 (line 130) has Devon 'shop from phone-glanceable list' and Jimbo (line 131) using 'Web UI (Eat tab)' — a separate person who plausibly is not sitting at Devon's Mac. §10 even requires 'Responsive ≥ 380px wide' and M5 gates on 'Playwright suite green incl. mobile viewport' — a mobile-optimized UI reachable only from the machine it runs on. No milestone, section, or open question addresses LAN binding, Tailscale/WireGuard, a reverse proxy, or what auth story applies the moment the bind moves off loopback. The two most-used human journeys in the PRD cannot occur as specced.

> **Question for Devon:** How do Devon's and Jimbo's phones reach the server — and if the answer is 'bind to LAN/tailnet', where is that specced, and what replaces 'no auth'?

### [CRITICAL] The UI never shows the accepted plan — every page load runs a fresh solve, so Jimbo's Tuesday view can be a menu nobody shopped or cooked

app.html:503-506 boots with hardcoded dial defaults (budget=550, n=12, seed=0, mass off) and immediately calls solve(); the Eat tab renders whatever /api/plan returns at that moment. Nothing in §10's additions wires `GET /api/plan/current` (PRD line 529) into the UI as the default view — it's listed as an endpoint, never as UI behavior. Determinism does not save this: after `accept` stages pantry writeback (§7.4/§7.6), the library changes, so a mid-week re-solve produces a different plan than the one that was shopped and cooked Sunday. Any dial nudge, dropped chip, or different device state also diverges. The PRD's own §7.6 makes the artifact the source of truth ('The UI's shopping checkboxes are ephemeral; the artifact is not') but never specs that the Eat and Shop tabs render the artifact rather than a live solve. As written, the person told to 'never touch config' (§3) is served a speculative re-solve every time he opens the page.

> **Question for Devon:** Should Eat/Shop default to the accepted artifact for the current ISO week (falling back to live solve only when none exists), and is 'live solve' an explicit mode Devon opts into?

### [CRITICAL] The system is open-loop: nothing captures actual consumption — pantry writeback uses PREDICTED leftovers, and Eat-tab gram edits evaporate

§0 and §3 say both people 'log macros by weight' — into an external tracker (§11.4 exports to MacroFactor/MFP). That data never comes back. §7.4 (PRD line 396): 'After a plan is accepted, predicted leftovers are written back as pantry candidates' — predicted, not actual. Cost attribution (§8.7, plan.py:102-109 `attribute`) splits spend by planned portions. The only place actual eating could be captured — Eat-tab gram edits — is a transient client-side `edits` object (app.html:234, 474) wiped on every solve() (app.html:250 `edits = {}`) and never POSTed anywhere except as replate pins. So: Jimbo skips a day or eats 300g instead of 450g → next week's pantry is wrong, the cost split is wrong, waste numbers are fiction, and yield calibration (§11.6, the admitted 'dominant macro error source') has no consumption signal to calibrate against. There is no `log` command, no actuals field in any schema, no end-of-week reconciliation flow anywhere in §7–§11. G6 'cost attribution by consumption' is actually attribution by prediction.

> **Question for Devon:** Where do actuals enter — a per-day 'ate it as planned / ate X instead / skipped' confirmation feeding pantry and attribution, or is open-loop an accepted v1 limitation that §7.4's 'user confirms' handwave should honestly state?

### [MAJOR] No rest-of-week replan flow: a burned batch, a restaurant night, or a store stock-out has no specced recovery beyond single-day replate

The only mid-week mechanism is `/api/replate` (§9.2, serve.py:165-173): one person, one day, pinned grams, same menu. Not specced anywhere: (a) a component is ruined/burned — no way to zero it out and re-solve days d..7 with remaining cooked inventory; (b) someone eats out — no way to mark a day consumed-externally and redistribute leftovers; (c) the store is out of an ingredient mid-shop — no substitute-and-recompute flow (the shopping list is checkbox-only, app.html:453-455); (d) re-planning the remainder of the week at all — `build_week` (plan.py:509) only builds full weeks from day 0. Worse, §7.6 declares the accepted plan a 'frozen artifact' with no amend semantics: §9.1 `accept` 'appends to history and stages pantry writeback' — accepting the same week twice (the natural act after any Tuesday divergence) would double-append history and double-stage pantry writeback as specced. §12–§13 contain no test or milestone for any divergence scenario.

> **Question for Devon:** What happens Tuesday when reality diverges — is there a 're-accept week N' semantics (replace artifact, reconcile history/pantry), and a replan-from-day-d operation over cooked-on-hand inventory?

### [MAJOR] 'Working, validated' means solver-validated only — no milestone anywhere requires cooking and eating one real week

Every §5 'empirical finding' was 'discovered by running the solver' (PRD line 185-186) — i.e., validated in silico against a library whose prices are estimates (OQ-3), whose yields are guesses (§11.6 admits `yield_g` is 'the dominant macro error source'), and whose Devon-side targets are invented (OQ-1). OQ-5 (line 743) admits no real cook day has happened ('batch_time_factor reality check after first real cook day'); §5.8 admits the cook-time budget 'is currently fictional.' Yet M0–M6 (lines 709-717) gate exclusively on tests, goldens, Playwright, and MCP callability. Nothing gates on: a week was cooked, plates were weighed, macros in the tracker matched the plan, yields were calibrated, a receipt was reconciled. The regression suite (§12.3) will faithfully lock in numbers (floor $264, Jimbo 2,121g/day, 5h44m) that may all be artifacts of estimated inputs. The status line's 'validated empirically this week' (line 8) overstates: the solver was exercised, not the food.

> **Question for Devon:** Insert a 'first real week' gate (cook it, weigh it, calibrate yields, reconcile the receipt) — before M3 at latest, since M3's calibration flow is useless without a real cook to feed it?

### [MAJOR] meals_per_day, min/max_components_per_day, and min_lean_anchors are declared, documented, and completely unused by the engine — and min_lean_anchors:1 contradicts the PRD's own §5.3

grep over plan.py/serve.py/app.html: zero occurrences of `meals_per_day`, `min_components_per_day`, `max_components_per_day`, or `min_lean_anchors`. All four appear in people.yaml (lines 15-17, 46) and are listed as live schema in PRD §7.3 (lines 368-374). Consequences: (1) Jimbo eats ~4,700 kcal / ~2.1 kg in 2 meals/day — the plate LP solves a whole day; nothing splits a 4.7 lb day into two assemblable meals, and the Eat tab renders day cards only. The field the PRD showcases in its very first table (§1.1) does nothing. (2) min/max components per day: the LP can legally serve one giant component or eight — unenforced. (3) people.yaml:46 sets `min_lean_anchors: 1` with the comment 'this is not a preference, it is structural' — while §5.3 and REG-04 assert ≥2 is required and plan.py:396 hardcodes `len(lean) >= 2`, ignoring the setting. The config file, the code, and the PRD state three different things about a 'structural' constraint.

> **Question for Devon:** For each field: does the build implement it (and where — meal-splitting is a real UX feature, not a schema note), or does §7.3 delete it? And which is right for lean anchors, 1 or 2?

### [MAJOR] Replate ignores shelf-life availability — a rebalanced day 7 can serve components that expired on day 3, contradicting REG-05 in the 'reference implementation'

serve.py:165-173 `replate` calls `plan.plate(people[pn], comps, menu, locked=locked)` over the FULL menu with no day parameter and no `available_on` filter; app.html:488-489 sends `menu: D.menu` (everything). plan.plate (plan.py:119) has no availability concept — that lives only in build_week. So Rebalance on day 6-7 can happily add guacamole (keeps_days 2, components.yaml:206) that build_week correctly excluded. The §9.2 endpoint spec repeats the omission: 'One person, pinned portions → re-solved day' lists no day/availability input, so the bug is being carried into the target contract. This directly violates the availability rule §5.4 declares a named regression (REG-05) and undercuts 'Working, validated' (§1.3) for the exact interaction the Eat tab exists for.

> **Question for Devon:** Should replate take a day index and filter to available_on(comp, day) — and should REG-05's test cover the replate path, not just build_week?

### [MAJOR] 'accept freezes the current solve' — but the server is stateless and there is no current solve; what gets frozen is a race

§9.1 (line 506): '`mealplan accept` — Freeze current solve → plans/<week>.yaml'. §1.3 (line 92) and §6.3: the server 'holds no state but rereads YAML per request.' So at accept time there is nothing to freeze: either (a) accept re-runs the solver — then the artifact silently depends on re-supplying the exact dials/seed/pins/drops the human was looking at, none of which the §9.2 `POST /api/accept` spec (line 528) says it accepts, and a mid-air library edit (Claude adding a component via MCP, §9.3) changes the result between viewing and accepting; or (b) the client posts the full plan it rendered — not specced either. Related ownership gap: §10 puts the Accept button in the UI (M5) but Devon also plans 'Claude conversationally' (§3) and MCP has `accept_plan {week}` — three surfaces can accept, and nothing defines which solve each one persists or what happens when accept is called twice for the same week (§7.6 append semantics again). The §7.6 'inputs hash' can verify reproducibility after the fact but cannot tell you the human approved *this* plan.

> **Question for Devon:** Define accept's payload precisely: does the client submit the rendered plan + full override state, and does the server verify-and-persist rather than re-solve?

### [MAJOR] CLI and server compute different batch counts for the same plan — the §5.8 'fixed prototype bug' is fixed in serve.py only

plan.py:577-580 (render) computes `batches[i] = ceil(total_week_demand / yield_g)` — one global ceil per component. serve.py:101-110 computes per-session demand and sums per-session ceils, which is ≥ the CLI number whenever a component feeds both sessions. §5.8 says 'fixed prototype bug: components were double-listed in both sessions; batches now split by which days they feed' — true for the HTTP path, false for the CLI path the PRD also calls 'Working, validated' (§1.3). Downstream, batches drive cook minutes, shopping quantities, purchase cost, and waste — so `plan.py all` and `POST /api/plan` disagree on cook time, the shopping list, and spend for the identical menu. M0 writes the regression harness 'against the prototype as-is' (line 711): as-is, there are two contradictory reference behaviors, and Appendix A's numbers (45 batches, 5h44m, $322–$345) don't say which surface produced them.

> **Question for Devon:** Which computation is the reference for M0 goldens, and does M0 first reconcile plan.py to the per-session split before locking anything in?

### [MAJOR] Write concurrency is specced for one process; the architecture has at least four writers

§6.4's entire concurrency posture is the in-process solve lock (serve.py:21) plus atomic write-temp-rename. But the target architecture has concurrent writers in separate processes with no coordination: the HTTP server (`POST /api/library/component`, `/api/accept`, `/api/pantry`), the MCP server (stdio, separate process, §9.3 write tools), the CLI (`add-component`, `calibrate`, `accept`, `pantry`), and humans/Claude editing YAML directly (§6.3 celebrates exactly this). Atomic rename prevents torn files but not lost updates: MCP `add_component` and an HTTP `POST /api/library/component` doing read-modify-write on components.yaml concurrently silently drops one write; `accept` from CLI while the server stages pantry writeback double-mutates pantry.yaml. §6.3 waves this off as 'concurrency needs are one household' — but the household's operator is Claude running in multiple surfaces simultaneously (G1 lists three at once), which is precisely the multi-writer case. No file locking, no optimistic versioning, no single-writer rule is specced.

> **Question for Devon:** Pick a rule and spec it: e.g., all writes route through one process, or fcntl lockfile around every read-modify-write, or writes carry the library hash they were based on and are rejected on mismatch.

### [MAJOR] 'Git history doubles as the audit log' — but the directory is not a git repo and no one is specced to commit, ever

§6.3 (line 311): 'Git history doubles as the audit log Devon already keeps.' §6.2 marks plans/ 'git-tracked.' I8 rests on 'git-versioned.' Reality: the working directory is not a git repository at all (per environment), and nothing anywhere specifies who commits or when — no post-write auto-commit in §6.4's write pipeline (validate → temp → rename → doctor diff, no commit step), no commit behavior in any §9 interface, no MCP tool commits, no milestone creates the repo. Without a specced commit protocol, atomic renames simply overwrite files in place and the 'audit log' is fiction: an MCP `add_component` followed by a bad `calibrate_yield` is unrecoverable and unattributable. Git also can't distinguish the four writers above unless author identity per surface is specced.

> **Question for Devon:** Is every validated write followed by an automatic commit (with which author string per surface — devon/claude-mcp/claude-cli/server?), and which milestone initializes the repo and makes commit-on-write a tested behavior?

### [MINOR] The lean-anchor 'shelf-life stagger' rule is not implemented — the code line is a boolean no-op, so §5.3's claim overstates what was validated

§5.3/REG-04 claim the menu rule '≥2 lean anchors, not all expiring together' was validated. plan.py:397-398: `pen += 0 if len({comps[i]["keeps_days"] >= 4 for i in lean}) and any(comps[i]["keeps_days"] >= 4 for i in lean) else 8000`. `len({...})` over a set of booleans is truthy whenever `lean` is non-empty, so the expression reduces to `any(lean keeps ≥ 4)` — 'at least one long-keeping lean', not staggered expiry. Two leans both keeping 4 days (expiring together) pass. If M0 goldens are recorded against this, REG-04 will enshrine the weaker rule while the PRD text asserts the stronger one. Doctor (plan.py:208-272) doesn't check stagger either, despite §8.3(b) promising 'lean anchors' shelf-life stagger'.

> **Question for Devon:** Is the real rule 'any lean keeps ≥ 4 days' (then fix §5.3's wording) or genuine stagger (then fix the code before goldens are recorded)?

### [MINOR] Code defaults quietly disagree with the library and the PRD's own findings: fallback cook_days is the known-broken [0,3]; frontier ranges never reach the claimed floor

people.yaml:47 sets `cook_days: [0, 4]` and §5.4/REG-05 establish [0,3] strands day 7 — yet plan.py:479, serve.py:85 and serve.py:160 all fall back to `settings.get("cook_days", [0, 3])`: the documented-broken configuration is the silent default if the key is ever absent. Separately, §5.6 claims the sweep $200→$650 found floor ≈$264, but the CLI frontier default is `--range 400:700:50` (plan.py:716) which cannot see the floor, while the UI sweeps 220–620 (app.html:403) — three surfaces, three ranges, and the CLI's default would 'confirm' a flat frontier. Minor individually, but these are exactly the kind of silent-default divergences I10 exists to prohibit.

> **Question for Devon:** Should M1's extraction make cook_days mandatory (no fallback) and unify the frontier default range across CLI/UI at one that brackets the empirical floor?

### [MINOR] Jimbo 'never touches config' but the UI gives every visitor full config: no role separation, and any Eat-tab visitor can trigger a global re-solve

§3: Jimbo 'nudge portions, never touch config.' The single-page app puts the budget/mass/seed dials, chip drop/✕ (which removes dishes), pin, and the global Re-solve button one tab away with no guard (app.html:146-153, 172-186). Any accidental dial drag fires a debounced full solve (app.html:471). There is no read-only mode, no per-person view, no confirmation on destructive actions. Combined with the fresh-solve-on-load issue, Jimbo browsing his day can silently generate and display a different week. §10's M5 'person editor' adds MORE config surface without ever specifying the Jimbo-safe view the actor table promises. Also unfulfilled Jimbo needs: 'self-assemble to his macros' presumes per-meal guidance that doesn't exist (see meals_per_day finding).

> **Question for Devon:** Does v1 need a per-person read-mostly Eat view (e.g., /eat?person=jimbo with dials and drop/pin hidden), and which milestone owns it?

### [MINOR] The PRD asserts facts about its own prototype that the prototype contradicts: the 'fixed' series-color bug is still present, and the UI hardcodes two people

§10 (lines 586-589): series hues 'mean macros only (P/F/C) — never reuse them for cuisines on the same screen (fixed bug; keep fixed).' app.html:233: `const CU = {mexican:'--s2', cn_am:'--s1', italian:'--s3', ...}` colors menu-chip cuisine bars with the macro series hues, on the Plan tab, which also draws frontier lines in --s1 (app.html:431) — the bug described as fixed is the shipped behavior of the file §1.3 calls 'Working. Screenshotted and reviewed.' Similarly §1.1 (lines 59-62) commands 'the build must not bake in two-person assumptions anywhere (UI person tabs...)' while the reference UI hardcodes `#mJ`/`#mD` dials and a `{jimbo, devon}` mass object (app.html:178-181, 240-241). Both are rebuild-fixable, but a PRD that misdescribes its own reference implementation undermines 'treat it as the reference implementation... the build must preserve it' (line 8) — the builder cannot tell which prototype behaviors are gospel and which are the bugs the PRD merely believes are gone.

> **Question for Devon:** Audit §10's 'keep fixed / keep' claims against app.html line-by-line before M0, and mark each prototype behavior explicitly preserve vs. known-defect?


## Invented constants presented as findings

### [CRITICAL] Determinism is claimed as a hard requirement but the prototype seeds plates with salted hash() — M0 goldens will be flaky

PRD §5.7 (PRD.md:235), §8.8 (PRD.md:485-487), and §12.1 (PRD.md:680) all require 'same inputs + seed ⇒ identical plan', and M0's gate (PRD.md:711) is that these tests pass against plan.py UNMODIFIED. But plan.py:537 seeds diverse_plates with `seed=(d * 31 + hash(pname)) % 9973`. Python salts str hash() per process (PYTHONHASHSEED is not set anywhere in the repo — grep confirms), so every process run produces different plate candidates, different weeks, different demand, different batches, different shopping lists. The 'Working, validated' prototype is nondeterministic across runs right now, which also means every empirical number in §5 and Appendix A came from one unreproducible process. The determinism property test cannot pass against the unmodified prototype, so M0 as written is unachievable.

> **Question for Devon:** Were the §5 empirical numbers (floors, waste, cook time, attribution) reproduced across at least two runs, or do they all come from single unrepeatable process invocations?

### [CRITICAL] min_lean_anchors: four sources give three different answers and the actual setting is dead code

people.yaml:46 sets `min_lean_anchors: 1` with the comment 'this is not a preference, it is structural.' components.yaml:14-15 says the library needs '>=1 per week.' PRD §5.3 (PRD.md:203-209) and §8.4 (PRD.md:456) say ≥2, and §7.3 (PRD.md:374) lists min_lean_anchors as a live setting. plan.py never reads it (grep: zero hits in plan.py/serve.py/app.html) — score_menu hardcodes `len(lean) >= 2` at plan.py:396 with a 15,000 penalty. So the config is a lie, the code contradicts the config's stated value, and REG-04 will enshrine the hardcoded 2 while the YAML continues to say 1. Worse, §5.3's own argument says the required count depends on shelf-life stagger and cook_days — i.e., it's derivable, not a constant at all; doctor's ablation already computes the true per-person number.

> **Question for Devon:** Is the requirement 1 or 2, and if doctor can derive it by ablation, why is it a hand-set constant (in two places, disagreeing) at all?

### [MAJOR] The 1.25× lean-ratio margin leaves Devon exactly 2 qualifying mains in the entire library — every menu is forced to contain the same two dishes, and the planned repeat penalty can never override it

plan.py:390-396: a main counts as 'lean' iff per-100g p/f ≥ (target p/f) × 1.25, and <2 leans costs 15,000. Computed against the actual library: Devon's threshold is 2.368 and only shrimp_al_pastor (5.93) and salsa_verde_chicken (5.52) pass — turkey_meatballs (1.88) fails despite being tagged `anchor: lean` in components.yaml:384. So for any menu to avoid a 15,000 penalty it must include both of those exact components, every week, forever. Consequences: (a) the two 'lean' definitions in the repo (anchor tag, used by doctor's report at plan.py:239-240; ratio×1.25, used by scoring) disagree on the library's own tagged anchors; (b) §7.5's repeat penalty (w=1200, PRD.md:406) is an order of magnitude below 15,000, so the history feature (G3) is structurally incapable of rotating lean mains — it's dead on arrival for exactly the dishes that repeat most; (c) the margin 1.25 has no derivation anywhere, and the third-leanest main sits at 1.88 vs Devon's 2.368 threshold, so small changes to 1.25 or to Devon's placeholder targets (OQ-1) flip the entire menu-search landscape. The number the whole search pivots on is coupled to admitted placeholder targets.

> **Question for Devon:** Is 'lean' the anchor tag or the ratio test? And is 'the same two mains every week' the intended product behavior, or an artifact of 1.25?

### [MAJOR] The perishable-waste score term is computed at 1 batch/component — the exact 'flat fake' defect the PRD says was fixed for cost, and the §5.6 waste findings were measured on it

PRD §8.7 (PRD.md:480-481) mandates estimate_batches for cost because 'the constant-batch version produced a flat fake frontier.' But score_menu's waste term — 'the number this whole thing exists to minimize' (plan.py:670-671) — is computed at plan.py:341 via `purchase(comps, ing, chosen)` with batches defaulting to 1 per component (plan.py:283). Real weeks run ~4 batches (Appendix A: 45 batches / 12 components), so the search optimizes leftover-of-one-batch, which has a completely different pack-boundary structure than leftover-of-four. The frontier's waste column (plan.py:690, serve.py:189) and therefore §5.6's headline claim 'waste 2,988g@$200 vs 2,188g@$320' and its REG expectation ('waste non-increasing as budget rises', PRD.md:691-692) are all measurements of the 1-batch estimator, not of what would actually be bought. Meanwhile render/serve compute real waste from real batches (plan.py:660, serve.py:125), so the number the menu was chosen on and the number reported to the user are different quantities.

> **Question for Devon:** Should REG tests lock in the 1-batch waste estimator's behavior, or should the estimator be fixed first — in which case the §5.6 numbers need re-measuring before they become regressions?

### [MAJOR] score_menu's penalty weights (4000/6000/15000/2500/1500/8000, −900/cuisine, 12/min, 120/$, 1.5×$, waste in raw grams) mix incommensurate units with zero justification, and the 'stagger' check is a no-op wrapper around a weaker check

plan.py:349-398. The score sums grams of waste + minutes×12 + dollars×1.5 + arbitrary role penalties. No document — not the PRD (§8.4 lists terms but not one weight), not the code comments — justifies any relative magnitude. Concrete trades these weights permit: two extra cuisines (−1800) more than cancel a missing accent minimum (+1500); a $50 budget overrun (+6000) equals a missing starch; a ~4000g swing in perishable waste equals a missing main. G2 says preserve this behavior under test, which freezes numbers nobody can defend. Separately, plan.py:397-398 — `pen += 0 if len({comps[i]["keeps_days"] >= 4 for i in lean}) and any(comps[i]["keeps_days"] >= 4 for i in lean) else 8000` — the `len({...})` clause is a set-of-booleans truthiness no-op; the check reduces to 'at least one lean keeps ≥4 days.' That is materially weaker than PRD §5.3/§8.4's claimed 'not all expiring together / shelf-life stagger,' and the threshold 4 is silently derived from cook_days=[0,4] while cook_days is a configurable setting — change cook_days and the hardcoded 4 is wrong with no error.

> **Question for Devon:** Which of these weights, if any, were swept or ablated during 'validation', versus typed in once and never touched?

### [MAJOR] §5.8's 'honest cook time ≈5h44m' is not an empirical finding — it is the assumed 0.45 constant multiplied out, and the two prototype surfaces disagree on the number

batch_time_factor 0.45 is admitted to be assumed (people.yaml:42-44 'a middle estimate'; OQ-5, PRD.md:743). Yet §5.8 presents '≈5h44m/week vs the stated 3h budget' as an empirical finding the build must preserve (§12.3 regression class). The '2× the wish' headline is pure arithmetic on 0.45: at f=0.25 it's ~4h27m; at f=0.1 it's ~3h20m. A regression test on this finding tests the constant, not the world. Worse, the two 'working, validated' surfaces compute different numbers: plan.py's report prints 'Hands-on total' from menuinfo['active_min'] (plan.py:568), which comes from estimate_batches' uniform-k guess (plan.py:307-319, same k for every component including zero-active_min store accents), while serve.py:120-122,156 computes per-session minutes from real build_week batches. plan.py has no per-session split at all, despite §5.8 claiming the double-listing bug was 'fixed' — that fix exists only in serve.py. The PRD never says which formula produced 5h44m/45 batches, so the regression target is unreproducible as specified.

> **Question for Devon:** Which code path generated the 5h44m/45-batch numbers, and should §5.8's regression be re-expressed as 'formula X with factor f' rather than a magic total?

### [MAJOR] Carb-ceiling 1.45× is an unexplained fudge standing in for day-availability math the system already performs, applied to the wrong population, and not actually checked by doctor

plan.py:382-384 penalizes 15,000 unless Σ(serve_max × carb density) over ALL eligible components (accents and veg included, despite the '≥3 starches' narrative) ≥ carb_target × 1.45, per person. Problems: (1) 1.45 has no derivation — PRD §5.2 (PRD.md:198-200) presents it as a 'menu rule derived' but the headroom it approximates (short-keeping starches expiring before cook day 2) depends on keeps_days distribution and cook_days, both data/config that can change while 1.45 stays frozen; build_week already computes exact per-day availability, so the constant is a proxy for a computation the system does anyway. (2) It was derived from Jimbo's 588g profile but is applied to Devon too (464g needed vs a 1,302g full-library ceiling — dead weight there, but it will misfire for any future person whose serve-max carb pool is tight). (3) PRD §8.3 (PRD.md:445) says doctor performs the 'carb-ceiling check vs 1.45×' — the prototype doctor (plan.py:208-272) does no such check, nor the starch count, nor the stagger check §8.3 lists. The PRD describes a doctor that does not exist while calling the prototype 'working, validated.'

> **Question for Devon:** If the week builder already knows exact per-day starch availability, why is the menu gate a global 1.45× constant rather than a worst-day availability computation?

### [MAJOR] Dislikes 6× exists only as a 0.001-scaled LP tiebreak; the PRD claims it is a menu-search score term, which is false, and the magnitude has no measurable effect to preserve

PRD I4 (PRD.md:154-157) elevates 'dislikes… soft objective weight (currently 6×)' to a constitutional invariant, and §8.4 (PRD.md:457-458) lists 'dislikes weight' as a Phase-1 cheap-score term. In the prototype, dislikes appear ONLY in plate() (plan.py:129-130) — score_menu has no dislikes term at all, so a disliked component is freely selected onto the menu. Within plate(), the 6× multiplies a tiebreak scaled by 0.001 against slack weighted 10,000 (plan.py:168-170): whenever any macro band benefits from the disliked component, it is served in full; 6× only reorders near-ties. It's also multiplied into diverse_plates' random weights U(0.35, 2.4) (plan.py:465), so disliked ([2.1, 14.4]) and liked ([0.35, 2.4]) ranges overlap — a random draw can rank a liked component worse than a disliked one. Net: '6×' is an invariant-blessed constant whose behavioral effect is approximately nil and whose documented location is wrong. A G2 regression test 'preserving' it would preserve nothing.

> **Question for Devon:** What observable behavior is the 6× supposed to produce (fewer grams? fewer days? off the menu entirely?), so a test can assert it — and does it belong in menu search as §8.4 claims?

### [MAJOR] mass_factor 0.3 for drinks has a circular acceptance criterion: the M4 gate tests the invented constant, not the household

PRD §7.2 (PRD.md:362) and §11.5 (PRD.md:638-646) introduce effective mass = grams × mass_factor, drinks ≈0.3, and the M4 acceptance is 'Jimbo's binary-searched mass floor drops below 2,000g effective' (PRD.md:715). But the effective floor is definitionally a function of the factor you chose — pick 0.3 and 500g of shake counts as 150g, and the test passes by construction. There is no calibration path for mass_factor (contrast batch_time_factor → OQ-5, yield_g → §11.6, prices → §11.7), no citation for 0.3, and no definition of what 'effective mass' physically means (satiety? gastric volume? chew time?). The §5.9 finding it serves ('floor ≈2,121g, only liquids move it') is real arithmetic, but the fix's success metric is the assumption itself. Note also the entire §5.9 floor derives from Devon's admitted-placeholder targets on one side and estimated serve_g bounds on the other.

> **Question for Devon:** What real-world observation would falsify 0.3, and shouldn't the acceptance criterion be phrased in that observable rather than in factor-weighted grams?

### [MAJOR] serve_g palatability bounds are person-independent while the eaters differ 1.65× in kcal and 2-vs-3 meals/day — and every per-person sizing knob in the schema is dead code

I5 calls serve_g bounds 'load-bearing' (PRD.md:158-162, REG-03), but the same {min,max} applies to a 4,705-kcal 2-meal eater and a 2,855-kcal 3-meal eater: jasmine_rice max 600g (components.yaml:298), flour_tortilla max 284g = '4 tortillas/day' (components.yaml:170) — 2/meal for Jimbo, 1.3/meal for Devon. Whose palate do these encode? Nothing in the repo says, and none of it was 'validated' in any stated way — the 4-tortilla cap is as invented as the 11-tortilla failure it prevents. Meanwhile the schema fields that could personalize plate structure — meals_per_day, min_components_per_day, max_components_per_day (people.yaml:16-18,31-33) — are read by NOTHING (grep: zero hits in plan.py/serve.py/app.html), yet PRD §7.3 (PRD.md:367-370) presents them as current working schema ('As today: …'). The PRD documents dead config as live behavior in a document whose prime claim is that prototype behavior is validated and must be preserved. (Same pattern: `freezes` and `batch_g` are stored and never read; the PRD at least flags `freezes` as recorded-but-ignored, but not the others.)

> **Question for Devon:** For each people.yaml/components.yaml field: is it (a) implemented, (b) spec'd future work, or (c) deletable? The PRD currently distinguishes none of these.

### [MINOR] Repeat-penalty constants (w=1200, decay 0.5, horizon 4) are invented, unanchored to the score scale, and the acceptance test cannot detect a wrong magnitude

PRD §7.5 (PRD.md:404-407) specs w=1200 × decay^weeks_ago. Ambiguity: is last week decay^0 (1200) or decay^1 (600)? Unstated. Scale: 1200 is below every structural penalty in score_menu (4000-15000) and comparable to ~1.3 cuisines (900), so it can only reorder near-ties — and per the lean-anchor finding it can never rotate the two forced lean mains. Week-4 penalty is 1200×0.0625=75, i.e., noise against a waste term measured in thousands of grams. The §11.2 acceptance ('a main used last week is avoided at equal score') passes for ANY positive w, so the test pins the sign, not the magnitude — all three constants are invisible to the test suite as specified.

> **Question for Devon:** What repeat rate is the target (e.g., 'a main used last week should lose to an otherwise-equal alternative but win over one costing ≥$X more / +Yg waste')? That statement would determine w; 1200 doesn't.

### [MINOR] Doctor's tolerance ladder 8/10/15% is absolute while personal tolerances are 5% and 7% — the first rung is nearly meaningless for Devon

plan.py:229 hardcodes `(0.08, 0.10, 0.15)`; PRD §8.3 (PRD.md:443) canonizes the same values. Jimbo's base tolerance is 0.05 (people.yaml:15) so 8% is a +60% relaxation; Devon's is 0.07 (people.yaml:30) so 8% is +14% — the ladder answers a different question per person. If anyone's tolerance is ever set ≥0.08, the first rung tests something tighter than or equal to their own setting and the message 'would clear at ±8%' becomes nonsense. A ladder relative to the person's tolerance (e.g., ×1.25/×1.5/×2) would be self-consistent; the absolute triple is an invented constant that happens to bracket today's two values.

> **Question for Devon:** Should the ladder be defined relative to each person's tolerance before §8.3 freezes the absolute values into the doctor contract?

### [MINOR] Frontier sweep ranges disagree everywhere: PRD says $200-650, tests say 17 points, CLI defaults 400:700:50, server defaults 250:600:25 — and cook_days code defaults are the documented-broken value

PRD §5.6 (PRD.md:222) reports the validated sweep as $200→$650. §12.5 (PRD.md:701) budgets 'frontier 17 points' (= 200:600:25, matching neither). plan.py:716 defaults `--range 400:700:50` (7 points, starting above the $340 plateau where §5.6 says money buys nothing — the default sweep would show a flat line and fail the 'frontier is non-flat' regression at PRD.md:691). serve.py:178-179 defaults lo=250, hi=600, step=25 (15 points). Four different ranges for the same 'validated' experiment; the regression band is unspecified. Same pattern: plan.py:479 and serve.py:85,160 default cook_days to [0,3] — the precise configuration §5.4 documents as broken (strands day 7); only people.yaml:47 setting [0,4] rescues every run. Fallback constants should not be the known-bad values.

> **Question for Devon:** Which sweep range is the canonical one for the §5.6 numbers and the §12.3/§12.5 tests?


## The test & milestone plan cannot execute as written

### [CRITICAL] M0's gate is unexecutable as written: several §12.1 properties test features the prototype does not have

M0 (§13) says done when "§12.1/12.3 pass against plan.py unmodified." But §12.1 includes: (a) "shopping packs cover ingredient need after pantry deduction" — pantry does not exist until M2 (§7.4); /Users/devon/Desktop/MealPlanner/mealplan/plan.py has no pantry anywhere. (b) "Mass caps respected in effective-mass terms" — mass_factor is NEW in §11.5 (M4); plate() caps raw grams only (plan.py:150-155). (c) "Session batch splits sum to totals" — plan.py has no session split at all; batches are computed globally in render() (plan.py:577-583) and the per-session split exists only in serve.py (serve.py:84-122), which M0's gate does not name. (d) "holes carry explanations" — build_week returns a bare empty dict for a hole (plan.py:543-544); the explanation exists only as markdown prose in render() (plan.py:604-611), and serve.py returns items:{} with no explanation object at all (serve.py:150). Either the gate text is wrong or §12.1 needs a column saying which properties activate at which milestone.

> **Question for Devon:** Which subset of §12.1 is actually the M0 gate, and against which surface — plan.py CLI, serve.py, or both?

### [CRITICAL] ✅ Determinism property is already violated: build_week seeds with hash(pname), which is salted per process

§12.1 asserts "same inputs + seed ⇒ identical plan" and §8.8/§5.7 claim everything is seeded. plan.py:537 seeds diverse_plates with `seed=(d * 31 + hash(pname)) % 9973`. Python salts str hash per interpreter process (PYTHONHASHSEED), so every fresh run of plan.py produces different randomized plate weights and therefore different plans for identical inputs and --seed. The M0 determinism test fails against plan.py unmodified, contradicting the gate; it also falsifies §7.6's "same inputs hash ⇒ same plan" reproducibility claim and the §14 "Seeds everywhere" mitigation. (Cosmetic confirmation that seeding was never audited: choose_menu creates `rng = random.Random(seed)` at plan.py:413 and never uses it.)

> **Question for Devon:** Is M0 allowed to fix this one line (making it not-unmodified), or does the determinism property get deferred to M1?

### [CRITICAL] The ≤400 LP-count guard (§8.8) is off by an order of magnitude against the actual code

Count it from plan.py: build_week tries up to 3 ladder rungs per person-day (plan.py:530-541), each calling diverse_plates(k=10), which makes up to k*8 = 80 plate() attempts (plan.py:463-473, call site :536). Every plate() is 2 CBC solves whenever any unit_g component is in the pool — which is essentially always with this library (tortilla/meatballs/wings/gordita) — via the snap-and-resolve second pass (plan.py:174-188). Worst case: 2 people × 7 days × 3 rungs × 80 attempts × 2 solves = 6,720 LPs, ~17× the guard. Even the happy path (every attempt feasible AND distinct, one rung) is 2×7×10×2 = 280 from build_week alone; duplicates are common with a 12-item menu so 80 attempts per day is typical, giving ~2,240. Add choose_menu phase 2 (≤25 menus × 2 people × 2 = 100, plan.py:441-453), plus doctor — which main() runs on every week/shop/all invocation (plan.py:732) with its tolerance ladder and per-person ablation (plan.py:229-233, 251-258) — and the guard fails on the first M0 run. Either the number is wrong or the code is; the PRD says "document both" as if the number were measured.

> **Question for Devon:** Was ≤400 ever measured, or derived from k=16... where did it come from?

### [CRITICAL] ✅ Named regressions are pinned to a library the PRD requires to be mutable — and REG-01's fixture doesn't exist anywhere

REG-01 (§5.1/§12.3) needs "target 200p/90f with an all-fatty-protein menu" producing "fat forced 24g over." No person in library/people.yaml has 200p/90f (jimbo 235/157, devon 180/95 — people.yaml:14,29), and the live library contains 3 lean anchors (shrimp_al_pastor, turkey_meatballs, salsa_verde_chicken — components.yaml:110,384,498), so an all-fatty menu is a synthetic fixture the PRD never specs. Same problem class everywhere: REG-02's masses (1,153g/1,734g/2,205g/3,108g), Appendix A's floors ($264/$340, 2,121g/day), and REG-04's ablation counts are all functions of ingredients.yaml/components.yaml values that G5 (library compounding), §11.6 (yield calibration rewrites per100), and §11.7 (price recalibration) are explicitly designed to change. §12 never says tests run against frozen fixture copies; as written, the first 2-minute TikTok recipe ingestion or first `calibrate` breaks the regression suite. REG-03 has the inverse gap: proving "without bounds the LP prescribes 750g of tortilla" requires a bounds-stripped fixture that §12 also never defines.

> **Question for Devon:** Do REG tests get a frozen tests/golden/library/ snapshot, and if so, does doctor's 'household structural facts' claim (§9.4.5) still get re-derived from the live library?

### [MAJOR] ✅ Golden-test policy is self-contradictory: exact determinism vs tolerance bands vs golden JSON, and CBC pinning doesn't rescue it cross-platform

§12.1 demands "same inputs + seed ⇒ identical plan" (exact). §5.7 and §14 demand goldens "assert within tolerance bands, not exact grams." §12.4 demands "Golden JSON files" for CLI/HTTP/MCP (byte-ish comparison). §7.6 demands artifact reproducibility "given pinned solver version." These can't all be the operative rule for the same outputs, and the PRD never says which applies where. The pinning story also doesn't hold: pulp vendors a prebuilt CBC binary per platform, so a lockfile pin gives you *a* CBC on the dev M-series Mac and a *different build* on linux CI; this LP is heavily degenerate (grams-minimizing tiebreak over near-interchangeable components, plan.py:164-170), which is exactly where different CBC builds pick different optimal bases. Tolerance-band goldens on portions won't save contract goldens on menu selection: a one-gram flip can change which shortlist menu first verifies feasible (plan.py:441-453), changing the entire plan discretely, not within a band.

> **Question for Devon:** Pick one: (a) exact goldens, same-platform CI only; (b) band goldens on macros/cost with menu identity asserted separately; which?

### [MAJOR] The ±tolerance property fails at the band edge by construction: grams-minimizing objective + integer rounding + 0.5g miss threshold

The plate objective minimizes weighted grams after slack (plan.py:164-170), so optimal solutions sit exactly on the lower tolerance boundary for at least one macro. Output then int-rounds every portion and drops values <5g (plan.py:197-201), and ok/miss uses a 0.5g slack threshold (plan.py:191-196) — a plate with 0.4g of slack per macro reports ok=True with no miss. Net effect: plates the prototype calls feasible can sit ~0.5-1g/macro outside the ±tolerance band when recomputed from reported portions, on nearly every plate, not rarely. §12.1's "Daily macros within ±tolerance for every non-hole day" as literally written fails against plan.py unmodified; it needs a specified epsilon (and a decision on whether the epsilon is per-macro grams or fraction), which the PRD never gives. Related latent hazard: the discrete snap fixes values with NO bound constraints in the re-solve (fixed values skip the lo/hi constraints, plan.py:142-148); it happens to respect serve_g today only because every unit_g component's min/max are exact unit multiples (components.yaml:56-58, 169-170, 387-388, 442-443). Nothing in §12.2's schema checks requires that data invariant, so one future component with min not a multiple of unit_g silently breaks the I5 property.

> **Question for Devon:** What is the rounding epsilon for the tolerance property, and should §12.2 add 'serve_g.min/max compatible with unit_g' as a schema rule?

### [MAJOR] ✅ The two prototype surfaces disagree on batches, cook minutes, and cost — which one do M0 goldens capture?

serve.py computes per-session batches as the sum of per-session ceils (serve.py:101-118) and session minutes from those (serve.py:120-122). plan.py's render computes one global ceil(need/yield) per component (plan.py:577-583) — no sessions — and, worse, the reported "Hands-on total" comes from menuinfo['active_min'], which is the *search-time estimate* via estimate_batches (plan.py:342-344, 568-570), not the actual demand batches at all. So for identical inputs, CLI and HTTP report different batch counts, different minutes, and different purchase/cost (Σ of per-session ceils ≥ global ceil, so serve.py buys more). §5.8 claims the double-listing bug is "fixed" — but the fix exists only in serve.py; plan.py still has the old behavior. §12.1's "Session batch splits sum to totals" is trivially true in serve.py (totals are defined as the sum) and untestable in plan.py. And Appendix A's ≈5h44m figure doesn't say which surface produced it.

> **Question for Devon:** Which surface is the reference for M0 goldens, and is plan.py's estimate-based hands-on number a bug to freeze or to fix?

### [MAJOR] ✅ Wall-clock performance assertions are flaky by construction on shared CI, and the thresholds don't cohere

§12.5 asserts full pipeline p95 < 10s on "M-series or CI-equivalent" — 'CI-equivalent' is undefined, GitHub-hosted runners are shared/variable, and the prototype already measures ~6s on an M-series (§1.3), leaving <2× headroom before a stock CI runner blows the bound on a good day. p95 is a distribution statistic: the plan never says the sample size (p95 of 20 runs ≈ 2+ minutes of CI per assertion; p95 of 3 runs is meaningless). §12.3 separately asserts <30s in CI as the "full-week-MILP guard" — a timeout is not a guard against MILP formulation, and 10s vs 30s for the same pipeline is never reconciled. "frontier 17 points < 25s" names a point count no interface produces by default (plan.py --range 400:700:50 → 7 points, plan.py:716; serve.py defaults 250:600:25 → 15 points, serve.py:178-179). Also note frontier in plan.py is print-only (plan.py:676-692) — the §12.3 frontier regression can't be asserted structurally against the unmodified prototype at all.

> **Question for Devon:** Are perf numbers CI gates or dev-machine benchmarks recorded as non-blocking artifacts?

### [MAJOR] Playwright has no home: §6.2 says 'nothing else in core', §12.4/M5 require a browser-automation stack

§6.2's pyproject line pins "pyyaml, pulp (CBC), pytest; nothing else in core." §12.4 requires Playwright UI smoke and M5's gate is "Playwright suite green incl. mobile viewport." The PRD never says where playwright (and pytest-playwright, and the browser binaries, and their CI provisioning step) live — dev extra? separate requirements file? — nor how the stdlib server gets started/torn down for the suite. Note also pytest itself is listed inside the 'core' pin list, muddying what 'core' means. As written, M5's gate depends on a dependency the dependency policy forbids. Minor adjacent gap: CI is 'GitHub Actions if the repo gets a remote; otherwise make test' (§12) — the repo is currently not even a git repo, so the entire CI-conditional test plan (12.3's <30s 'in CI', 12.5) may have no executor.

> **Question for Devon:** Define the dev-dependency group and the Playwright/browser provisioning step, or drop the M5 gate to a scriptable smoke (e.g. fetch + JS-free assertions)?

### [MAJOR] §5.6's '~62% of calories' is not measured consumption — it is the ratio of stated targets, violating I11 inside the PRD's own findings

render() computes '% of calories' from kcal_of(targets) (plan.py:638-641), and serve.py's people block likewise reports target kcal (serve.py:149). jimbo 4,705 / (4,705+2,855) = 62.2% — the '62%' in §5.6 and Appendix A is arithmetic on inputs, not an empirical finding about what the plans serve. Presenting it as "Jimbo eats ~62% of calories" is exactly the stated-vs-derived conflation I11 forbids, and a regression test encoding it would merely re-assert the input ratio forever (and silently break the moment OQ-1 lands real Devon targets). The 57%-of-cost half IS derived from portions (attribute(), plan.py:102-109) — so the two numbers in the comparison come from different epistemic categories. Related: with a third eater (OQ-2), serve.py's independently-rounded shares (serve.py:154) can violate §12.1's ±$0.01 sum bound; fine for 2 people, not guaranteed for N.

### [MAJOR] The shelf-life 'stagger' rule (§5.3, §8.3b, REG-04) is not implemented anywhere — the prototype's check is a buggy no-op reduction

score_menu's supposed stagger check is `len({comps[i]["keeps_days"] >= 4 for i in lean}) and any(comps[i]["keeps_days"] >= 4 for i in lean)` (plan.py:397-398). The len() of a set of booleans is truthy whenever lean is non-empty, so the whole expression reduces to any(keeps>=4) — it checks 'at least one long keeper', never 'not all expiring together'. doctor() has no shelf-life-stagger ablation at all (plan.py:239-269 covers leanness ablation only), despite §8.3(b) claiming "lean anchors' shelf-life stagger (§5.3)" as a doctor check. So REG-04 as specced tests behavior the prototype doesn't have and cannot pass at M0 unmodified; either the PRD documents the actual (weaker) behavior, or this is a known bug M0 must golden-in as-is and M1 fixes with a PRD note per §4's own amendment rule.

### [MINOR] ✅ REG-05/REG-06 as tests: fixture and observability gaps

REG-05 requires cook_days [0,3], but the shipped settings are [0,4] (people.yaml:47) — fine to override in-test, but the PRD never says regressions may override settings vs. use shipped config. The 'explained hole' it asserts exists only as markdown strings in render() (plan.py:604-611); via serve.py a hole is items:{} with zero explanation (serve.py:150), so §10's claim that the Eat tab 'renders the explained hole (REG-05 copy)' is unimplementable from the current API without the client re-deriving it. REG-06's ladder: build_week relaxes strict→+1→uncapped silently (plan.py:530-541) with no record of which rung fired — directly violating I10 ('if the solver relaxes... the output says so') and making 'ladder was used' unobservable to a test against unmodified code. Also §12.3's frontier assertion 'non-flat and non-increasing in waste as budget rises' generalizes monotonicity from exactly two data points in §5.6 over a stochastic local search; nothing in choose_menu guarantees it stepwise across a 17-point sweep.

### [MINOR] Dead config and dead flags contradict 'Working, validated' and the §9.1 contract

(1) settings.min_lean_anchors exists (people.yaml:46, value 1) and §7.3 lists it as live config, but plan.py never reads it — the lean-anchor requirement is hardcoded to ≥2 (plan.py:396), which also contradicts the yaml's own value of 1. G6's 'policy not hardcode' principle is violated for this knob. (2) The CLI defines --force (plan.py:715-716) but main() never passes must= to choose_menu (plan.py:745) — --force is silently ignored in plan.py; only serve.py honors force. An M1 contract golden for `mealplan menu --force a,b` (§9.1) would be recording behavior the reference implementation doesn't have. (3) build_week's weekly-gram cap is checked before adding the day's portion (plan.py:521-527), so used_g can exceed yield_g × max_batches_per_component by up to one full plate, and render's batches = ceil(need/yield) is unbounded by the cap (plan.py:577-579) — the max_batches_per_component setting (§7.3, people.yaml:51) is advisory in practice, which any §12.1-style 'caps respected' test would flag.


## Architecture & interface contract gaps

### [CRITICAL] "Same shapes" across CLI/HTTP/MCP has no owner, no artifact, and no enforcement

§9.3 promises MCP responses are "the same shapes as §9.1 --json", and §9.1 versions those shapes as "mealplan/v1". But nothing in the architecture produces or checks that shape: the §6.2 repo layout has no schemas/ directory and no serialization module in core (model.py is input validation only). Today the response shape is hand-assembled inside the HTTP adapter — serve.py:46-57 (comp_public) and serve.py:141-162 (solve_all builds the entire response dict inline) — which already violates §6.1's "adapters contain zero solver logic; if an adapter needs a computation, it moves into core". Under this PRD, cli.py and mcp_server.py will each hand-roll their own dicts. The §12.4 golden files are per-surface snapshots: they detect drift within a surface after the fact, they do not prove the three surfaces are identical. Note also that §9.2 never says HTTP responses carry the "schema": "mealplan/v1" field — as written, only CLI --json does, so the surfaces are definitionally not the same shape.

> **Question for Devon:** Where does the canonical response shape live — a core-owned serializer emitting typed result objects that all three adapters pass through verbatim — and is there a contract test asserting CLI --json, HTTP, and MCP produce identical JSON for identical inputs+seed?

### [CRITICAL] Accepting a plan mutates its own inputs — inputs-hash reproducibility is self-contradictory as specced

§7.6: the artifact stores an "inputs hash (library file hashes + overrides)" and claims "same inputs hash ⇒ same plan"; the M2 gate (§13) is "plans/ round-trip reproducible by inputs hash". But accept appends to history.yaml (an input to menu scoring, §7.5/§8.4) and writes predicted leftovers back to pantry.yaml (an input to purchase, costing, and week-builder availability, §7.4/§8.5/§8.7). If pantry.yaml and history.yaml are among the hashed "library file hashes", the act of accepting invalidates the hash it just recorded and the round-trip test can never pass; if they are excluded, the hash doesn't cover the inputs and the plan isn't reproducible at all. §7.6 never says whether the artifact embeds snapshots of pantry/history state or when the hash is computed relative to the writeback.

> **Question for Devon:** Exactly which files/values are hashed, is the hash taken pre- or post-writeback, and does the artifact embed frozen copies of pantry+history so a replay doesn't depend on mutable library state?

### [CRITICAL] Frontier holds the solve lock ~25s; every UI dial queues behind it, blowing the p95<10s budget the PRD calls a hard requirement

serve.py:212-214 runs GET /api/frontier under _lock; §12.5 budgets 17 frontier points at <25s (each point is a full choose_menu, serve.py:181-190). app.html:506 fires solve() AND loadFrontier() together on page load, and loadFrontier() re-fires on every theme toggle (app.html:458). Dial edits debounce 420ms (app.html:471) then POST /api/plan, which blocks on the same lock — so during any frontier sweep, the "UI solve round-trip p95 < 10s" hard requirement (§5.7) is arithmetically unachievable, and §6.4's "queued requests fine" was asserted without accounting for a 25s lock holder. No cancellation, coalescing, or priority is specified in §6.4/§9.2. Compounding it: serve.py:230-234 sends the response inside the `with _lock:` block, so a slow client (the phone the PRD wants, §3) extends lock hold past solve time. Separately, /api/frontier is GET-only with lo/hi/step/n (§9.2) and cannot carry the override block, so the frontier charts on the Plan tab are computed from library defaults while the adjacent tiles reflect dial overrides — two inconsistent states on one screen.

> **Question for Devon:** What is the concurrency design for frontier — chunked/cancellable points, a separate low-priority queue, or precomputed off the interactive path — and does the p95 requirement apply while a sweep is in flight?

### [CRITICAL] calibrate_yield and update_pantry violate the "no partial patches in v1" rail declared in the same table

§9.3 safety rails: "tools that write require the full validated object (no partial patches in v1)". Two rows above it, calibrate_yield takes {component, cooked_g} and mutates a single field (yield_g) of an existing component — exactly a partial patch. update_pantry is specced as "Replace/merge stock" — merge is also a partial patch. §11.6 additionally has calibrate "append a provenance note", a field that does not exist in the §7.2 component schema. Either the rail is the contract (then calibrate_yield must round-trip the full component object and pantry is replace-only) or field-level ops are the contract (then the rail is false and the MCP schemas need explicit patch semantics). This must be resolved before M4 freezes tool schemas, because it decides the write model for every future tool.

> **Question for Devon:** Which is authoritative — the full-object-replace rail, or field-level operations — and where does calibrate's provenance note actually get stored?

### [MAJOR] Localhost-only + zero auth contradicts the phone use cases the PRD itself commits to

§3 requires Devon to "shop from phone-glanceable list" and Jimbo to use the Eat tab; §2 says the responsive web UI "suffices" in lieu of a mobile app; §10 mandates responsiveness ≥380px. Phones cannot reach 127.0.0.1, and §9.2 says "No auth (localhost bind only); never bind 0.0.0.0 by default" — "by default" implies a LAN-bind flag will exist, at which point POST /api/accept, /api/pantry, and /api/library/component are unauthenticated writes on the LAN. Even purely localhost-bound there is a CSRF hole: serve.py sets no Origin/CORS check (do_POST at serve.py:225-238 accepts any origin), so once write endpoints exist, any web page open in Devon's browser can POST to localhost:8770 and mutate the library. The PRD must either spec a shared token / Origin allowlist for write endpoints, or explicitly retract the phone story.

> **Question for Devon:** How do the phone flows work at all under localhost-only bind, and what protects write endpoints when the implied non-default LAN bind is used?

### [MAJOR] G1 parity matrix has holes: MCP can't solve an explicit menu, CLI has no replate, MCP has no validate or current-plan read

G1: "Every capability reachable via MCP tools and via CLI with --json". Gaps as specced: (1) MCP plan_week input is {n?, seed?, budget?, force?, exclude?, mass?} (§9.3) — no menu parameter, though HTTP /api/plan solves a caller-supplied menu (serve.py:69-77) and CLI has --menu; it also lacks the targets/tolerance/dislikes overrides that §9.2 grants every HTTP POST. (2) The §9.1 CLI table has no replate command at all, so the Eat-tab rebalance capability (HTTP /api/replate, MCP replate_day) is unreachable from the Cowork/device-bridge path G1 names explicitly. (3) MCP has no validate tool and nothing equivalent to GET /api/plan/current; get_history returns menus only (§7.5 schema), not portions, so a Claude Desktop operator cannot inspect the accepted plan. Each gap breaks "any Claude surface can drive it without screen-scraping".

### [MAJOR] "exclude" means allergen tags in one part of the contract and component ids in another

people.yaml exclude = allergen tags, infeasibility-hard (I4, §7.3). CLI --exclude, the HTTP override block's exclude[] (§9.2), and MCP plan_week's exclude? = component ids removed pre-search (plan.py:726-727 pops component ids; serve.py:62-63 same). Same word, two domains, both defined in §9. An operator — or the SKILL.md v2 that will be written against these interfaces — passing tag names into exclude[] silently no-ops (comps.pop of a nonexistent id, no error), which is precisely the I10 "silent drop" class the constitution forbids. One of the two needs renaming (e.g. omit[]/drop[] for components) before the schemas are versioned.

### [MAJOR] §7.3 documents config fields the reference implementation never reads; min_lean_anchors disagrees with the hardcoded constant

Grep of plan.py and serve.py: meals_per_day, min_components_per_day, max_components_per_day, and min_lean_anchors appear nowhere outside people.yaml. §7.3 lists all four as current schema ("As today"). Worse, people.yaml:46 sets min_lean_anchors: 1 while score_menu hardcodes len(lean) >= 2 (plan.py:396) and §5.3/§8.4 mandate ≥2 — a knob that exists, is documented, contradicts the constant, and does nothing. Since G2 freezes prototype behavior under goldens, these dead fields get fossilized as-is unless the PRD marks each one implement, wire-to-constant, or delete. (Also §7.3's parenthetical "mains only, I5.5" cites a nonexistent invariant — it means §5.5.)

> **Question for Devon:** For each of the four fields: implement in M1, or delete from the schema? And which wins for lean anchors — the setting or the constant?

### [MAJOR] Plans keyed by ISO week (Mon-start) while cook days are Sunday-anchored — no day-0→calendar mapping exists anywhere

§7.6 keys artifacts as plans/2026-W32.yaml and §9.2's GET /api/plan/current returns "last accepted artifact for this ISO week". ISO weeks run Monday–Sunday. But §5.4 and people.yaml:47-50 define cook_days [0,4] as Sun/Thu — day 0 is a Sunday, so the household week (Sun..Sat) straddles two ISO weeks. On any Sunday (a cook day!), which artifact does /api/plan/current resolve to — the week that started that morning (whose ISO label is next week's) or the one ending? Nothing in §7.6 defines the calendar date of day 0, so the artifact key, history entries (§7.5 "week: 2026-W31"), and weeks_ago decay math are all ambiguous by one week. Related silent divergence: serve.py:85 and serve.py:160 hardcode fallback cook_days [0,3] while the library says [0,4].

> **Question for Devon:** Define the mapping: does day 0 = the Monday of the ISO week (making the Sun/Wed–Sun/Thu narrative wrong), or does the household week get its own anchor date stored in the artifact?

### [MAJOR] "Git is the audit log" but nobody commits, and accept spans three files with no transaction boundary

§6.3 leans on git history as the audit log; §6.4 specs only per-file atomicity (temp+rename). accept (§9.1/§9.2/§7.6) writes plans/<week>.yaml, appends history.yaml, and stages pantry.yaml writeback — three files with no specified ordering, no multi-file rollback, and no specified commit actor. A crash between writes leaves history without an artifact (or vice versa), and nothing detects it. More basically: no section says who runs `git commit` — engine, adapter, hook, or Devon manually. Until that's specified, the audit log is whatever Devon remembers to commit, and MCP-driven library writes (add_component etc.) accumulate uncommitted and un-audited. The working directory isn't even a git repo right now (per environment), so the entire I8 audit story currently rests on nothing.

> **Question for Devon:** Does accept (and every library write) auto-commit with a message convention, or is the audit log explicitly best-effort manual?

### [MAJOR] Component id rename "migration note" is a note, not a migration — raw ids are denormalized into four files

§7.2: "renames require a migration note in the plan history." But raw component ids are stored in history.yaml menus (§7.5), plans/*.yaml portions (§7.6), pantry.yaml cooked-component entries (§7.4), and people.yaml dislikes (§7.3). After a rename, the repeat penalty silently stops matching (the renamed dish escapes its history penalty — an I10-class silent behavior change), pantry leftovers orphan, and dislikes dangle; §12.2's validation list has no cross-file referential checks covering history/pantry/dislikes, so none of this is caught. A note in prose fixes none of it. Either forbid renames in v1 or spec a `mealplan rename <old> <new>` that rewrites all referencing files under validation.

### [MAJOR] Doctor-after-every-write is dozens of LP solves per write, inside the solve lock, and the "doctor diff" has no defined baseline

§6.4/§8.3/§9.3 mandate an automatic doctor run + diff after every library write. doctor() (plan.py:208-272) runs, per person: a full-library plate solve, up to 3 tolerance-ladder solves, plus an ablation loop of up to len(mains) plate solves — at §12.5's own 150ms plate p95 that is seconds of CBC per write, serialized behind the same lock the UI solves on. The §9.4 ingestion protocol adds missing ingredients one at a time before the component (one doctor run each), and §11.7 receipt calibration can touch dozens of ingredients — multiplying doctor runs with no batching or async story anywhere. Separately, "doctor diff" requires a before-state to diff against, and nothing in the data model (§7) stores the previous doctor report — is it re-run pre-write inside the same request, doubling the cost?

> **Question for Devon:** Is doctor-on-write batched (one run per logical operation), bounded (skip ablations?), or async — and what exactly is the diff computed against?

### [MAJOR] §12.4 byte-golden JSON files vs §5.7 "tolerance bands, not exact grams" cannot both hold

§5.7/§14 require golden tests to "assert within tolerance bands, not exact grams" because CBC/PuLP drift across versions. §12.4 requires "golden JSON files for CLI --json and each HTTP/MCP tool". Those JSON documents contain portion grams, costs, and batch counts — a byte-comparison golden of them is exactly the exact-gram assert §5.7 forbids, and will go flaky on any solver bump (the very risk §14 lists). The PRD never reconciles this: it needs to define which parts of each payload are shape/schema-asserted, which are value-band-asserted, and which are exact (ids, enums, structure).

### [MINOR] Library YAML files carry no schema version while the PRD adds new fields and a new enum value

Outputs get "schema": "mealplan/v1" (§9.1), but the actual database — ingredients.yaml, components.yaml, people.yaml (verified: no version key in any) — is unversioned, even as §7 adds NEW fields (usda_fdc_id, liquid, mass_factor, method) and extends the role enum with drink (M4). §12.2 requires rejecting "bad enum values" with structured errors, so an M1-era validator meeting that spec will hard-reject an M4-era file containing role: drink, and load() has no way to distinguish "newer schema" from "typo". Plan artifacts (§7.6) similarly get no schema version of their own despite being long-lived files read back by /api/plan/current. Add a schema_version per file or state the forward/backward-compat policy explicitly.

### [MINOR] CLI default n=10 vs server default n=12 — same seed, different plan per surface

plan.py:707 defaults --n to 10; serve.py:64 defaults n to 12 (and frontier n=12 at serve.py:179); SKILL.md instructs --n 12; §12.5's performance gate is measured at n=12. The §9.1 CLI table specifies no default. So `mealplan menu --seed 0` and POST /api/plan {seed:0} — two surfaces the PRD says share shapes and semantics — return different menus. Contract goldens recorded per-surface will happily enshrine the divergence. Pin one default (12, evidently) in core, not in each adapter.

### [MINOR] serve.py contradicts its own error contract: unconditional tracebacks, and malformed JSON escapes the handler entirely

§6.4: "engine exceptions → 500 with traceback in dev mode" — but serve.py:221-223 and 236-238 return the full traceback unconditionally, and no dev-mode flag exists or is specced anywhere. Worse, in do_POST the body parse `body = json.loads(self.rfile.read(n) or b"{}")` (serve.py:228) sits before the try block (serve.py:229), so a malformed JSON body raises outside the handler's try and the client gets a dropped connection instead of any structured error — contradicting both the §6.4 posture and the "Working, validated" claim for serve.py (§1.3). Small fix, but it belongs in the M1 error-contract spec, not left to chance.


## Additional findings (synthesizer's own, verified by direct code reading / execution)

### [MAJOR] Two byte-identical PRD copies in the repo
`PRD.md` at root and `mealplan/PRD.md` are identical today; nothing keeps them so. First edit to one silently forks the constitution. Pick one location, delete the other.

### [MAJOR] Two kcal accountings coexist and disagree
Ingredient `kcal` is hand-entered label data (e.g., `ground_beef_85`: 215 listed vs 209.4 by Atwater 4/9/4 from its own macros); component `per100.kcal` sums those labels, but every day-total and person-target kcal is `kcal_of()` = Atwater-derived (plan.py:112-113, 615). The Eat tab and the tracker export can disagree with the plan header by a few percent for the same food. Decide which kcal is canonical — and note `spices` is entered as 0 kcal, a deliberate fudge I2's rhetoric ("macros are derived, never wrong") never acknowledges.

### [VERIFIED-NUANCE] Appendix A's volume floor replicates, but is a best case
Reproduced exactly: Jimbo infeasible at 2,100g cap, feasible at 2,121g, carbs the binding macro. But that is the *full 26-component library on a single day* with no variety caps and no shelf-life valleys — a lower bound. A real 10–12 dish menu across a real week sits higher. The PRD presents the bound as "Jimbo's floor" without the qualifier.

### [REPRODUCED] Same-seed nondeterminism
Two consecutive `plan.py all --seed 0` runs produced different menus, batch counts (jasmine rice 4 vs 3), and shopping lists. Cause: `hash(pname)` in build_week's plate seeds (plan.py:537) is salted per process (PYTHONHASHSEED).

## What survives scrutiny

For calibration — the load-bearing architecture held up: the two-problem decomposition (I1), macros-derived-from-ingredients (I2), the accent/structural-allergen data model (I3), hard-vs-soft preference separation (I4), directional infeasibility reporting (I6), and grams-internally/packs-at-boundary (I7) all check out in both design and (mostly) implementation. §5.2's carb-density arithmetic verifies against the ingredient data (tortilla 1,153g … pintos ~3,111g per 588g carbs). The prototype's plate LP is genuinely correct on the cases tested. The problem is not the vision — it is that the document's empirical spine is partly fiction, its two prototype surfaces are two different products, and its acceptance gates cannot execute as written.
