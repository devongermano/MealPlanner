import type { components } from '@mealplan/contracts-api';

/*
 * ============================================================================
 * DELETE THIS FILE when apps/api adds `type: String` to its nullable
 * @ApiProperty declarations and packages/contracts-api is regenerated.
 * ----------------------------------------------------------------------------
 * NestJS Swagger cannot infer a type through a `string | null` union, so a bare
 * `@ApiProperty({ nullable: true })` emits `{"type": "object", "nullable": true}`
 * into openapi.json, and openapi-typescript faithfully renders that as
 * `Record<string, never> | null`. The result is that the fields this app leans on
 * hardest — the plan identity, and the userId that marks a placeholder — cannot
 * be treated as strings.
 *
 * Rather than hand-mirror the schemas (which ARCHITECTURE.md forbids, rightly),
 * every type below is the GENERATED one with only those known-broken fields
 * narrowed. A field added, removed or renamed upstream still reaches this app
 * through codegen exactly as it should; only the null-typing is corrected, and
 * only until the source is fixed. Reported to the api-steward 2026-08-10.
 * ============================================================================
 */

type Schemas = components['schemas'];

/**
 * Replaces the `Record<string, never> | null` fields named in K with `string | null`.
 * Mapping over `keyof Pick<T, K>` rather than K directly keeps the optional and
 * readonly modifiers — the request types are all-optional patches, and making them
 * required here would be a second bug on top of the one being worked around.
 */
type NullableStrings<T, K extends keyof T> = Omit<T, K> & {
  [P in keyof Pick<T, K>]: string | null;
};

export type HouseholdMemberView = NullableStrings<
  Schemas['HouseholdMemberView'],
  'userId' | 'personName' | 'inviteEmail'
>;

export type HouseholdSummary = NullableStrings<Schemas['HouseholdSummary'], 'personName'>;

export type HouseholdDetail = Omit<Schemas['HouseholdDetail'], 'members'> & {
  readonly members: readonly HouseholdMemberView[];
};

export type MeResponse = Omit<Schemas['MeResponse'], 'email' | 'households'> & {
  readonly email: string | null;
  readonly households: readonly HouseholdSummary[];
};

export type CreateHouseholdRequest = Schemas['CreateHouseholdRequest'];
export type AddHouseholdMemberRequest = Schemas['AddHouseholdMemberRequest'];

export type UpdateHouseholdMemberRequest = NullableStrings<
  Schemas['UpdateHouseholdMemberRequest'],
  'personName' | 'inviteEmail'
>;

export type UpdateOwnMembershipRequest = NullableStrings<
  Schemas['UpdateOwnMembershipRequest'],
  'personName'
>;

export type ApiErrorResponse = Schemas['ApiErrorResponse'];
export type ApiErrorBody = Schemas['ApiErrorBody'];
export type ApiErrorDetail = Schemas['ApiErrorDetail'];

/** Generated as a string union on the schema, so it needs no correction. */
export type HouseholdRole = Schemas['HouseholdMemberView']['role'];
