"""The simulated world — a store, a cart, a receipt, and an audit trail.

Pure Python. No I/O beyond reading its own catalog and the sheets under test,
no MCP awareness, no LLM awareness. MCP is one transport (see mcp_server.py);
a scripted driver can be swapped in for bulk calibration without touching a
line of this file.

TWO CHANNELS, and the distinction is the design:

  raise   the cook cannot proceed until it engages. Reserved for genuine
          forks — "which of these three shrimp products did you mean?" — and
          for the assumption chokepoint. Used sparingly: a world that raises
          at every imperfection deadlocks the cook and produces one finding
          instead of forty.

  record  the world noticed something and wrote it down. The cook is not
          interrupted and does not need to notice. This is where most
          findings come from, and it is deliberate: a gate that depends on
          the cook *remembering to complain* inherits exactly the
          unfalsifiability we are trying to escape.

The most important consequence: `checkout()` reconciles the whole trip
mechanically — every shopping row against the cart, every ingredient the cook
plan names against what was actually bought. Salt and water are caught there,
by arithmetic, with no hint given to the cook that anyone was looking.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .errors import (AlreadyCheckedOut, AmbiguousProduct, AssumptionRequired,
                     GaveUp, NothingInCart, SimError, UnknownSku)

SHEET_NAMES = ("shopping_list", "cook_plan")   # slice 1; eat sheets join in slice 3


# --------------------------------------------------------------------------- #
#  products
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Sku:
    id: str
    display_name: str
    aisle: str
    sold_by: str                 # weight | count | bunch
    pack_g: float
    price: float
    aliases: tuple = ()
    pack_count: Optional[int] = None
    perishable: bool = False
    needs_refrigeration: bool = False
    keeps_days_raw: Optional[int] = None
    freezable: bool = False
    notes: str = ""

    @property
    def search_text(self) -> str:
        return " ".join((self.display_name, " ".join(self.aliases),
                         self.id.replace("_", " "))).lower()

    def pack_phrase(self) -> str:
        """How a shelf tag would describe one package."""
        if self.sold_by == "count" and self.pack_count:
            return f"{self.pack_count} ct ({self.pack_g:g}g)"
        if self.sold_by == "bunch":
            return f"1 bunch (~{self.pack_g:g}g)"
        return f"{self.pack_g:g}g"


def _norm(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


class Store:
    """A shelf-accurate grocery store, authored independently of the meal
    planner's own ingredient data (see catalog.yaml's provenance note —
    that independence is what makes the planner's pack claims falsifiable)."""

    def __init__(self, skus: list[Sku], aisles: list[str]):
        self.skus = {s.id: s for s in skus}
        self.aisles = list(aisles)

    @classmethod
    def load(cls, path: str | Path) -> "Store":
        doc = yaml.safe_load(Path(path).read_text())
        skus = []
        for raw in doc["skus"]:
            skus.append(Sku(
                id=raw["id"], display_name=raw["display_name"],
                aisle=raw["aisle"], sold_by=raw["sold_by"],
                pack_g=float(raw["pack_g"]), price=float(raw["price"]),
                aliases=tuple(raw.get("aliases") or ()),
                pack_count=raw.get("pack_count"),
                perishable=bool(raw.get("perishable", False)),
                needs_refrigeration=bool(raw.get("needs_refrigeration",
                                                 False)),
                keeps_days_raw=raw.get("keeps_days_raw"),
                freezable=bool(raw.get("freezable", False)),
                notes=raw.get("notes") or ""))
        return cls(skus, doc.get("aisles") or [])

    def search(self, query: str, limit: int = 8) -> list[Sku]:
        """Fuzzy shelf search. Returns [] for a genuine miss — which is the
        finding when a shopping row names a category ("spices") rather than
        a product. Scores token overlap first (a shopper's words), then
        falls back to fuzzy string similarity for near-misses and typos."""
        q = _norm(query)
        if not q:
            return []
        scored = []
        for sku in self.skus.values():
            hay = set(_norm(sku.search_text))
            overlap = sum(1 for t in q if t in hay)
            ratio = difflib.SequenceMatcher(
                None, " ".join(q), sku.search_text).ratio()
            score = overlap * 10 + ratio
            if overlap or ratio > 0.62:
                scored.append((score, sku.id, sku))
        scored.sort(key=lambda r: (-r[0], r[1]))
        return [s for _, _, s in scored[:limit]]

    def get(self, sku_id: str) -> Sku:
        if sku_id not in self.skus:
            raise UnknownSku(f"no product with id {sku_id!r} — search first")
        return self.skus[sku_id]

    def vocabulary(self) -> dict[str, str]:
        """Every food word the store knows -> sku id. Used to scan cook-plan
        prose for ingredients that no shopping row ever buys (this is how
        salt and water surface, without anyone naming them in advance)."""
        vocab = {}
        for sku in self.skus.values():
            for phrase in (sku.display_name, *sku.aliases):
                key = " ".join(_norm(phrase))
                if key:
                    vocab.setdefault(key, sku.id)
        return vocab


# --------------------------------------------------------------------------- #
#  cart, events, findings
# --------------------------------------------------------------------------- #
@dataclass
class CartLine:
    sku: Sku
    packages: int
    for_row: Optional[int] = None      # shopping_list line number this serves

    @property
    def grams(self) -> float:
        return self.sku.pack_g * self.packages

    @property
    def cost(self) -> float:
        return round(self.sku.price * self.packages, 2)


@dataclass
class Event:
    seq: int
    action: str
    args: dict
    outcome: str                      # ok | refused | recorded
    detail: str = ""


@dataclass
class Finding:
    defect_class: str
    detail: str
    state: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)   # [{sheet, line_no, text}]
    source: str = "world"             # world | assumption | cook


class EventLog:
    def __init__(self):
        self.events: list[Event] = []
        self._seq = 0

    def add(self, action: str, args: dict, outcome: str, detail: str = ""):
        self._seq += 1
        self.events.append(Event(self._seq, action, dict(args), outcome,
                                 detail))
        return self.events[-1]


# --------------------------------------------------------------------------- #
#  the sheets, as the cook receives them
# --------------------------------------------------------------------------- #
class Sheets:
    """The cook-facing artifacts, held as numbered lines.

    Line numbers matter: every finding must quote a real line at a real
    number, and score.py verifies that claim against this object before
    anything else. A finding whose evidence does not appear where it says it
    does is hallucinated, and enough of them void the run.
    """

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.text: dict[str, str] = {}
        for p in sorted(self.dir.glob("*.md")):
            self.text[p.stem] = p.read_text()

    def numbered(self, name: str) -> str:
        if name not in self.text:
            raise UnknownSku(f"there is no sheet called {name!r}; you have: "
                             f"{', '.join(sorted(self.text))}")
        return "\n".join(f"{i:4d}| {ln}" for i, ln
                         in enumerate(self.text[name].splitlines(), 1))

    def line(self, name: str, line_no: int) -> str:
        lines = self.text.get(name, "").splitlines()
        return lines[line_no - 1] if 0 < line_no <= len(lines) else ""


# --------------------------------------------------------------------------- #
#  the world
# --------------------------------------------------------------------------- #
class World:
    """The facade every cook tool calls. Holds the store, the cart, the
    sheets, the assumption ledger, and the findings the world noticed."""

    def __init__(self, store: Store, sheets: Sheets, persona: dict):
        self.store = store
        self.sheets = sheets
        self.persona = persona
        self.log = EventLog()
        self.cart: list[CartLine] = []
        self.findings: list[Finding] = []
        self.assumptions: list[dict] = []
        self.receipt: Optional[dict] = None
        self.location = "entrance"
        self._last_search: list[Sku] = []
        self._seen: set[str] = set()          # skus the cook has actually seen

    # ---- persistence ----------------------------------------------------- #
    #
    # A CLI driver runs one verb per process, so the world has to survive
    # between invocations. Everything mutable is plain data; the store and
    # the sheets are reloaded from disk, so only the cook's own history is
    # carried here.
    def to_state(self) -> dict:
        return {
            "cart": [{"sku": c.sku.id, "packages": c.packages,
                      "for_row": c.for_row} for c in self.cart],
            "findings": [{"defect_class": f.defect_class, "detail": f.detail,
                          "state": f.state, "evidence": f.evidence,
                          "source": f.source} for f in self.findings],
            "assumptions": list(self.assumptions),
            "events": [{"seq": e.seq, "action": e.action, "args": e.args,
                        "outcome": e.outcome, "detail": e.detail}
                       for e in self.log.events],
            "receipt": self.receipt,
            "location": self.location,
            "seen": sorted(self._seen)}

    def load_state(self, st: dict) -> None:
        self.cart = [CartLine(self.store.get(c["sku"]), c["packages"],
                              c.get("for_row")) for c in st.get("cart", [])]
        self.findings = [Finding(f["defect_class"], f["detail"],
                                 f.get("state", {}), f.get("evidence", []),
                                 f.get("source", "world"))
                         for f in st.get("findings", [])]
        self.assumptions = list(st.get("assumptions", []))
        self.log.events = [Event(e["seq"], e["action"], e["args"],
                                 e["outcome"], e.get("detail", ""))
                           for e in st.get("events", [])]
        self.log._seq = max((e.seq for e in self.log.events), default=0)
        self.receipt = st.get("receipt")
        self.location = st.get("location", "entrance")
        self._seen = set(st.get("seen", []))

    # ---- recording ------------------------------------------------------- #
    def record(self, defect_class: str, detail: str, evidence=None,
               source: str = "world", **state):
        self.findings.append(Finding(defect_class, detail, dict(state),
                                     list(evidence or []), source))
        self.log.add("_record", {"defect_class": defect_class}, "recorded",
                     detail)

    # ---- the cook's verbs ------------------------------------------------ #
    def look_at(self, sheet: str) -> str:
        """Read one of the sheets you are holding. Which sheets the cook
        consults, and when, is itself signal — hence the log entry."""
        out = self.sheets.numbered(sheet)
        self.log.add("look_at", {"sheet": sheet}, "ok")
        return out

    def walk_to(self, aisle: str) -> str:
        if aisle not in self.store.aisles:
            self.log.add("walk_to", {"aisle": aisle}, "refused")
            return (f"There is no {aisle!r} aisle. The store has: "
                    f"{', '.join(self.store.aisles)}")
        self.location = aisle
        here = [s for s in self.store.skus.values() if s.aisle == aisle]
        self._seen.update(s.id for s in here)
        self.log.add("walk_to", {"aisle": aisle}, "ok")
        return (f"You are in {aisle}. On the shelves:\n" +
                "\n".join(f"  {s.id} — {s.display_name} — "
                          f"{s.pack_phrase()} — ${s.price:.2f}"
                          for s in sorted(here, key=lambda s: s.id)))

    def search_product(self, query: str) -> str:
        hits = self.store.search(query)
        self._last_search = hits
        self._seen.update(s.id for s in hits)
        self.log.add("search_product", {"query": query},
                     "ok" if hits else "refused",
                     "" if hits else "no match")
        if not hits:
            return (f"Nothing on any shelf matches {query!r}. Ask a clerk, "
                    f"try a different word, or give_up on this row.")
        return "\n".join(
            f"  {s.id} — {s.display_name} — sold by {s.sold_by} — "
            f"{s.pack_phrase()} — ${s.price:.2f}"
            + (f" — {s.notes}" if s.notes else "")
            for s in hits)

    def inspect(self, sku_id: str) -> str:
        s = self.store.get(sku_id)
        self._seen.add(s.id)
        self.log.add("inspect", {"sku": sku_id}, "ok")
        keeps = (f"{s.keeps_days_raw} days refrigerated"
                 if s.keeps_days_raw else "shelf stable")
        return (f"{s.display_name}\n  id: {s.id}\n  aisle: {s.aisle}\n"
                f"  sold by: {s.sold_by}\n  one package: {s.pack_phrase()}\n"
                f"  price: ${s.price:.2f}\n  keeps: {keeps}\n"
                f"  needs refrigeration: {s.needs_refrigeration}\n"
                f"  freezable: {s.freezable}"
                + (f"\n  note: {s.notes}" if s.notes else ""))

    def add_to_cart(self, sku_id: str, packages: int,
                    for_row: Optional[int] = None) -> str:
        """Put `packages` whole packages in the cart.

        `for_row` is the shopping_list line number this purchase satisfies.
        It is optional for the cook's convenience but load-bearing for
        reconciliation: an unattributed purchase cannot be matched to a row,
        so checkout will treat that row as unsatisfied.
        """
        if self.receipt is not None:
            raise AlreadyCheckedOut("you have already paid and left")
        sku = self.store.get(sku_id)
        if sku_id not in self._seen:
            raise UnknownSku(
                f"you have not looked at {sku_id!r} yet — search_product or "
                f"walk_to its aisle first")
        if packages < 1:
            raise UnknownSku("you cannot add fewer than one package")
        self.cart.append(CartLine(sku, int(packages), for_row))
        self.log.add("add_to_cart",
                     {"sku": sku_id, "packages": packages,
                      "for_row": for_row}, "ok")
        return (f"Added {packages} × {sku.display_name} "
                f"({sku.pack_g * packages:g}g total, "
                f"${sku.price * packages:.2f}).")

    def remove_from_cart(self, sku_id: str) -> str:
        before = len(self.cart)
        self.cart = [c for c in self.cart if c.sku.id != sku_id]
        self.log.add("remove_from_cart", {"sku": sku_id},
                     "ok" if len(self.cart) < before else "refused")
        if len(self.cart) == before:
            return f"{sku_id} was not in your cart."
        return f"Removed {sku_id}."

    def view_cart(self) -> str:
        if not self.cart:
            return "Your cart is empty."
        total = sum(c.cost for c in self.cart)
        return ("\n".join(
            f"  {c.packages} × {c.sku.display_name} — {c.grams:g}g — "
            f"${c.cost:.2f}" + (f"  [row {c.for_row}]" if c.for_row else "")
            for c in self.cart) + f"\n  TOTAL: ${total:.2f}")

    def assume(self, field_: str, value: str, why: str) -> str:
        """Declare, on the record, something the sheets did not tell you.

        This is not a confession — it is the instrument. Every assumption is
        a place the plan failed to be executable, and the count of them
        falling over releases is the product becoming usable.
        """
        self.assumptions.append({"field": field_, "value": value,
                                 "why": why})
        self.log.add("assume", {"field": field_, "value": value}, "ok", why)
        self.record("assumption_required",
                    f"had to assume {field_} = {value!r}: {why}",
                    source="assumption", field=field_, value=value)
        return (f"Noted: {field_} = {value!r}. Proceed — but this is now on "
                f"the record as something the sheets did not tell you.")

    def give_up(self, what: str, why: str) -> str:
        """Stop. The strongest signal this gate can produce."""
        self.log.add("give_up", {"what": what}, "refused", why)
        self.record("orientation_missing", f"gave up on {what}: {why}",
                    source="cook", what=what)
        return f"Recorded: you could not proceed with {what}."

    def note_problem(self, what: str, why: str, sheet: str = "",
                     line_no: int = 0) -> str:
        """The cook's own judgment channel — kept, but weighted lowest and
        clearly separated from what physics refused."""
        ev = []
        if sheet and line_no:
            ev = [{"sheet": sheet, "line_no": line_no,
                   "text": self.sheets.line(sheet, line_no)}]
        self.record("implausible_meal", f"{what}: {why}", evidence=ev,
                    source="cook")
        return "Noted."

    # ---- checkout: where the arithmetic happens --------------------------- #
    def checkout(self) -> str:
        from .sheets import reconcile          # local: avoids import cycle
        if self.receipt is not None:
            raise AlreadyCheckedOut("you have already paid")
        if not self.cart:
            raise NothingInCart("your cart is empty")
        total = round(sum(c.cost for c in self.cart), 2)
        self.receipt = {
            "lines": [{"sku": c.sku.id, "name": c.sku.display_name,
                       "packages": c.packages, "grams": c.grams,
                       "cost": c.cost, "for_row": c.for_row}
                      for c in self.cart],
            "total": total}
        self.log.add("checkout", {"lines": len(self.cart)}, "ok",
                     f"${total:.2f}")
        reconcile(self)                        # records findings, never raises
        return (f"You paid ${total:.2f} for {len(self.cart)} items and drove "
                f"home. The bags are on the kitchen floor.")
