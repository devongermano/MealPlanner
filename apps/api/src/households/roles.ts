import { SetMetadata } from '@nestjs/common';
import { HouseholdRole as PrismaHouseholdRole } from '@prisma/client';

/**
 * The roles, in the order the API publishes them (PRD §4.2).
 *
 * Declared here rather than re-exported from Prisma so the wire contract does
 * not silently inherit a database refactor — but the two must never diverge,
 * which `assertRolesMatchDatabase` below enforces at compile time.
 */
export const HOUSEHOLD_ROLES = ['planner', 'cook', 'eater'] as const;
export type HouseholdRoleName = (typeof HOUSEHOLD_ROLES)[number];

type Equal<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
/**
 * Fails `tsc` if the API enum and the `household_role` database enum stop
 * being the same set. Adding a role to one without the other is then a build
 * error, not a runtime surprise on an authorization path.
 */
const assertRolesMatchDatabase: Equal<HouseholdRoleName, PrismaHouseholdRole> =
  true;
void assertRolesMatchDatabase;

/**
 * Roles are a LADDER, not a set: planner > cook > eater.
 *
 * PRD §4.2 gives one person potentially all three roles ("often = planner",
 * "one person can hold all roles"). A ladder expresses that with a single
 * column: a planner already holds every capability a cook holds, and a cook
 * every capability an eater holds. The alternative — a role set per member —
 * buys nothing v1 needs and doubles the authz surface.
 *
 * Where the ladder does NOT reach: "eater" capabilities are self-scoped (see
 * your own sheets, veto, nudge your own portions). A planner inherits the
 * capability, still applied to their own person. Nothing here grants one member
 * authority over another member's personal data.
 */
const ROLE_RANK: Record<HouseholdRoleName, number> = {
  eater: 1,
  cook: 2,
  planner: 3,
};

/** True when `actual` sits at or above `required` on the ladder. */
export function roleSatisfies(
  actual: HouseholdRoleName,
  required: HouseholdRoleName,
): boolean {
  return ROLE_RANK[actual] >= ROLE_RANK[required];
}

export const MIN_ROLE_KEY = 'mealplan:minRole';

/**
 * Minimum role for a route, enforced by `HouseholdMembershipGuard`.
 *
 * Routes with no `@MinRole` still require membership — the guard defaults to
 * `eater`, the bottom of the ladder. There is no way to reach a
 * household-scoped route without being in that household.
 */
export const MinRole = (role: HouseholdRoleName) =>
  SetMetadata(MIN_ROLE_KEY, role);
