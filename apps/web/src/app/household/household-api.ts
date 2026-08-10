import { InjectionToken } from '@angular/core';

/*
 * ============================================================================
 * REGENERATE-FROM-CONTRACTS-API
 * ----------------------------------------------------------------------------
 * Every type in this file is a PLACEHOLDER. The api-steward owns the real
 * household shapes; when they land in packages/contracts these definitions get
 * deleted and re-exported from the generated types instead — per ARCHITECTURE.md,
 * "nobody hand-writes the TypeScript mirror" of a cross-boundary type.
 *
 * The interface below is the seam that makes that swap mechanical: the UI depends
 * only on HOUSEHOLD_API, never on the implementation behind it.
 *
 * Shapes here now follow the member model ratified 2026-08-10 (placeholder
 * members: nullable userId, displayName on the row, personName as plan identity)
 * so the regeneration is a type swap rather than a redesign. Field names track
 * apps/api's DTOs deliberately. Known remaining gaps, all owned by the API:
 *   - Household lacks the caller's own role/personName/memberCount that
 *     HouseholdSummary carries; adopt them with GET /me at swap time.
 *   - listMine should become GET /me, which returns identity and households in
 *     one call.
 * ============================================================================
 */

/** PRD §4.2. planner configures and approves, cook preps, eater sees sheets and vetoes. */
export type HouseholdRole = 'planner' | 'cook' | 'eater';

export const HOUSEHOLD_ROLES: readonly HouseholdRole[] = ['planner', 'cook', 'eater'];

export const ROLE_DESCRIPTIONS: Readonly<Record<HouseholdRole, string>> = {
  planner: 'Sets up people and targets, approves the week, does the shopping.',
  cook: 'Gets the prep plan for cook days. Often the same person as the planner.',
  eater: 'Sees their own sheets, vetoes dishes, and nudges portions.',
};

export interface Household {
  readonly id: string;
  readonly name: string;
  readonly createdAt: string;
}

export interface HouseholdMember {
  readonly id: string;
  readonly householdId: string;
  readonly displayName: string;
  readonly role: HouseholdRole;
  /**
   * The account this member is, or null for a PLACEHOLDER member — someone the plan
   * cooks for who has not signed up. Owner-ratified: a household must be fully
   * plannable before everyone has an account, because an eater may never log in at
   * all. Invitations later claim these rows; accounts serve the veto/notify loop,
   * they are not a precondition for being fed.
   */
  readonly userId: string | null;
  /**
   * Who this person is in the meal plan. The engine keys every per-person map by
   * this slug, so it is the join between an account and the plan. Null means the
   * person holds a role but does not eat — a planner who shops and approves but has
   * no portions cooked for them.
   */
  readonly personName: string | null;
  /** Invite intent for a placeholder member. Nothing is sent until invitations exist. */
  readonly email: string | null;
  /** True for the member row belonging to the signed-in account. */
  readonly isSelf: boolean;
}

export interface CreateHouseholdInput {
  readonly name: string;
  /** Role the creating account takes in their own household. */
  readonly creatorRole: HouseholdRole;
  readonly creatorDisplayName: string;
  /** Omit to derive from the display name; explicit null means the creator does not eat. */
  readonly creatorPersonName?: string | null;
}

export interface AddMemberInput {
  readonly displayName: string;
  readonly role: HouseholdRole;
  readonly email?: string | null;
  /** Omit to derive from the display name; explicit null means this person does not eat. */
  readonly personName?: string | null;
}

/**
 * Partial update of a member. Shaped as one patch rather than a method per field
 * because the API is one endpoint — PATCH …/members/:id — taking both optionally.
 * A `personName` of null unlinks: the member keeps their role and stops eating.
 */
export interface UpdateMemberInput {
  readonly role?: HouseholdRole;
  readonly personName?: string | null;
}

export interface HouseholdApi {
  /** Households the signed-in account belongs to. Empty means onboarding is owed. */
  listMine(): Promise<readonly Household[]>;
  create(input: CreateHouseholdInput): Promise<Household>;
  listMembers(householdId: string): Promise<readonly HouseholdMember[]>;
  addMember(householdId: string, input: AddMemberInput): Promise<HouseholdMember>;
  updateMember(
    householdId: string,
    memberId: string,
    patch: UpdateMemberInput,
  ): Promise<HouseholdMember>;
  removeMember(householdId: string, memberId: string): Promise<void>;
}

export const HOUSEHOLD_API = new InjectionToken<HouseholdApi>('HOUSEHOLD_API');
