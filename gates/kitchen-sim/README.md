# The kitchen sim gate

A simulated grocery store and kitchen, and a cook who has never seen this
repository, sent to shop for and cook a week from the planner's own output.

## Why this exists

Every other gate here checks the engine against its own idea of correctness —
tests assert conservation, goldens assert byte-stability, judge panels assert
spec conformance. All of them were written by people who know what the engine
meant. None can catch a plan that is internally perfect and unusable by a
human standing in a kitchen, which is the failure mode that has actually bitten
this project twice, and both times a person caught it, not a test.

The PRD says it plainly (§11): *"Solver-side validation alone is no longer
called validation."*

## The idea

The cook proposes an action; **physics** accepts or refuses it. `2720 g of
beef plus 1400 g of stock into a 5.7 L Dutch oven` either fits or it does not.
Findings stop being opinions and become typed exceptions carrying the numbers
that caused them.

That yields a metric worth trending: **how much human judgment a plan needs in
order to be executable.** A perfect plan runs on a dumb robot. Every point
where the cook must supply something the sheets did not say is a recorded
assumption, and that count falling over releases *is* the product becoming
usable.

Two drivers make that measurable:

| driver | what it is | what it measures |
|---|---|---|
| `naive` | a deterministic robot with no judgment at all — searches the literal word, buys the fewest whole packages that cover the need, never substitutes, never infers | the **floor**: what is executable with no cleverness. Free, instant, reproducible; run it on every plan |
| `cook` | an LLM persona with real kitchen knowledge, driving the same world through the same verbs | the **ceiling**: what a thinking person can rescue. Run at milestones |

A defect both hit is a hard one — nobody could proceed. A defect the robot hits
and the cook sails past is the interesting kind: the plan silently outsourced a
decision to human competence and would fail anyone who lacked it.

## The store is authored independently, and that is the whole trick

`kitchen_sim/catalog.yaml` — 84 SKUs written from real-world grocery knowledge
and **deliberately not derived from `examples/ingredients.yaml`**. The engine's
own `pack_g` values are precisely the claim under test; generating the store
from them would make the gate self-consistent by construction and unable to
find anything. Independence is what makes `spices — 3 × 100g` falsifiable:
there is no such product, and no shelf to walk to.

The store also stocks what the plan silently assumes — salt, oil, foil,
containers — so the cook *could* have bought them. Their absence from the
shopping list is the finding; their absence from the world would be our bug.

## Running it

```bash
# the deterministic robot (free, instant)
cd gates/kitchen-sim
../../.venv/bin/python -m kitchen_sim.run \
    --sheets <dir with shopping_list.md, cook_plan.md, eat_*.md> \
    --json findings.json

# the LLM cook: point the world at a run, then send the agent in
cat > .current_run.json <<'EOF'
{"sheets": "<dir>", "catalog": "kitchen_sim/catalog.yaml", "out": "<run>.json"}
EOF
```

The cook acts through `kitchen_sim.cook_cli` (one verb per invocation, state
persisted between calls) or through the MCP server, which is the intended
transport: registered in `.mcp.json` as `kitchen`, it gives the cook a tool
surface containing **no filesystem verb at all**, so it cannot consult this
repo because it has nothing that could. MCP servers load at session start, so
a freshly-registered server needs a restart before its tools appear.

Watch a run live:

```bash
tail -f <run>.events.jsonl
```

## Trusting the output

`PREDICTIONS.md` holds falsifiable expectations **written before the first
run** and scored after it. A gate nobody has tested is a rubber stamp; the
cheapest test is to say in advance what it must find, then check.

First run: **4/4 must-hit predictions, 14 findings, zero false positives** —
after two rounds in which the prose scan cried wolf 78% of the time and had to
be fixed. That history is recorded deliberately. A gate's credibility comes
from its calibration record, not from its confidence.

From slice 2 onward, planted mutants join: one line of a *copy* of the sheets
is rewritten (the engine is never touched, so the saved diff is an exact answer
key), including control mutations that must **not** be flagged.

## Layout

```
kitchen_sim/
  world.py         Store · Cart · Sheets · EventLog · World   (pure, no I/O)
  errors.py        the refusal taxonomy — these ARE the findings
  catalog.yaml     84 independently-authored SKUs
  sheets.py        parsers + the six reconciliation checks
  naive_shopper.py the deterministic robot (the floor)
  cook_cli.py      one verb per invocation, state persisted
  mcp_server.py    the same world as MCP tools (the intended transport)
  run.py           sheets in, findings out
tests/             the instrument gets tested like the engine does
PREDICTIONS.md     written first, scored after
```

## Scope

Slice 1 (built) is the store and the shopping trip. Slice 2 is the kitchen —
vessels, burners, oven temperature buckets, one pair of hands, a clock. Slice 3
is storage, containers, expiry and eating, where shelf-life bugs become raised
exceptions rather than readings. Slice 4 is the four-persona panel.

This is an **M1.6 pre-gate, not per-PR CI**: it runs before a real week is
cooked, and whenever `artifacts.py` changes materially. Anything deterministic
that falls out of it graduates into the normal test suite.
