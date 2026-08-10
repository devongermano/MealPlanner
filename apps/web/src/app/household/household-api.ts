import { InjectionToken } from '@angular/core';
import type {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdMemberView,
  HouseholdRole,
  HouseholdSummary,
  MeResponse,
  UpdateHouseholdMemberRequest,
  UpdateOwnMembershipRequest,
} from './contracts';

/*
 * The household seam. Every shape below now comes from packages/contracts-api,
 * generated from the NestJS API's own OpenAPI document — nothing here is a
 * hand-written mirror, per ARCHITECTURE.md. (The one narrowing this app applies,
 * and why, is documented in ./contracts.ts.)
 *
 * The interface remains because the UI should depend on a capability, not on
 * HttpClient. It is what let the whole app be built and tested before these
 * endpoints existed.
 */

export type {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdMemberView,
  HouseholdRole,
  HouseholdSummary,
  MeResponse,
  UpdateHouseholdMemberRequest,
  UpdateOwnMembershipRequest,
};

/** PRD §4.2. planner configures and approves, cook preps, eater sees sheets and vetoes. */
export const HOUSEHOLD_ROLES: readonly HouseholdRole[] = ['planner', 'cook', 'eater'];

export const ROLE_DESCRIPTIONS: Readonly<Record<HouseholdRole, string>> = {
  planner: 'Sets up people and targets, approves the week, does the shopping.',
  cook: 'Gets the prep plan for cook days. Often the same person as the planner.',
  eater: 'Sees their own sheets, vetoes dishes, and nudges portions.',
};

export interface HouseholdApi {
  /** Bootstrap: identity and every household the caller belongs to, in one call. */
  me(): Promise<MeResponse>;
  createHousehold(input: CreateHouseholdRequest): Promise<HouseholdSummary>;
  listMembers(householdId: string): Promise<readonly HouseholdMemberView[]>;
  addMember(
    householdId: string,
    input: AddHouseholdMemberRequest,
  ): Promise<HouseholdMemberView>;
  /**
   * Planner route. The API accepts role and personName for any member — the
   * library link is planning data — but rejects displayName and inviteEmail for
   * a member who has an account, and rejects the whole patch rather than
   * applying it partially.
   */
  updateMember(
    householdId: string,
    memberId: string,
    patch: UpdateHouseholdMemberRequest,
  ): Promise<HouseholdMemberView>;
  /** Self route. Carries no role: it is open to eaters, so one would be self-promotion. */
  updateSelf(
    householdId: string,
    patch: UpdateOwnMembershipRequest,
  ): Promise<HouseholdMemberView>;
  removeMember(householdId: string, memberId: string): Promise<void>;
}

export const HOUSEHOLD_API = new InjectionToken<HouseholdApi>('HOUSEHOLD_API');
