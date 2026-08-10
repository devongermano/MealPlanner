import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { Transform } from 'class-transformer';
import {
  IsEmail,
  IsIn,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Matches,
  ValidateIf,
} from 'class-validator';
import { HOUSEHOLD_ROLES, type HouseholdRoleName } from '../roles';

/**
 * The engine keys every per-person map (`weeks`, `broke`, `cost.shares`, …) by
 * the string used under `people:` in the library YAML — `jimbo`, not "Jimbo
 * Smith". This pattern is the API's guarantee that whatever lands in
 * `person_name` is usable as that key: a YAML-safe, URL-safe, case-stable slug.
 *
 * Deliberately stricter than the engine, which validates nothing here. The API
 * is the strict layer; loosening this later is a migration, tightening it is a
 * data cleanup.
 */
export const PERSON_NAME_PATTERN = /^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$/;
const PERSON_NAME_MESSAGE =
  "must be a lowercase slug matching the library's people: key (a-z, 0-9, _ or -, 1-64 chars, starting and ending alphanumeric)";

const trim = ({ value }: { value: unknown }) =>
  typeof value === 'string' ? value.trim() : value;

// --------------------------------------------------------------------------
// Requests
// --------------------------------------------------------------------------

export class CreateHouseholdRequest {
  @ApiProperty({
    description: 'Household name.',
    minLength: 1,
    maxLength: 120,
    example: 'The Germanos',
  })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  name!: string;

  @ApiProperty({
    description:
      "The creator's own human-readable name in this household. Required because every member has one — a member list of UUIDs is not a member list.",
    minLength: 1,
    maxLength: 120,
    example: 'Devon',
  })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  displayName!: string;

  @ApiPropertyOptional({
    description:
      'Which person in the library the creator eats as. Omit if the creator plans but does not eat.',
    example: 'devon',
  })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, {
    message: `personName ${PERSON_NAME_MESSAGE}`,
  })
  personName?: string;
}

export class UpdateHouseholdRequest {
  @ApiProperty({ minLength: 1, maxLength: 120 })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  name!: string;
}

/**
 * Adds a member. Two shapes, distinguished by whether `userId` is present:
 *
 *   PLACEHOLDER (omit userId) — the normal path. A real member who has no
 *     account yet: a housemate, a child, anyone the planner is planning for.
 *     Bind an account to it later through the claim seam.
 *   EXISTING ACCOUNT (send userId) — only usable when the caller already knows
 *     the account's UUID. There is deliberately no lookup-by-email endpoint:
 *     answering "does this address have an account?" is an enumeration oracle.
 */
export class AddHouseholdMemberRequest {
  @ApiProperty({
    description: "This member's human-readable name.",
    minLength: 1,
    maxLength: 120,
    example: 'Alex',
  })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  displayName!: string;

  @ApiProperty({ enum: HOUSEHOLD_ROLES })
  @IsIn(HOUSEHOLD_ROLES, {
    message: `role must be one of: ${HOUSEHOLD_ROLES.join(', ')}`,
  })
  role!: HouseholdRoleName;

  @ApiPropertyOptional({
    format: 'uuid',
    description:
      'auth.users id, when the caller already knows it. Omit to create a placeholder member with no account.',
  })
  @IsOptional()
  @IsUUID()
  userId?: string;

  @ApiPropertyOptional({
    description: 'The library person this member eats as.',
    example: 'alex',
  })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, {
    message: `personName ${PERSON_NAME_MESSAGE}`,
  })
  personName?: string;

  @ApiPropertyOptional({
    format: 'email',
    maxLength: 320,
    description:
      'Where an invitation WOULD be sent once that flow exists. Stored as intent only: it is never checked against the account directory, so sending it reveals nothing about whether an account exists. Rejected on a member that already has an account.',
  })
  @IsOptional()
  @Transform(trim)
  @IsEmail()
  @Length(1, 320)
  inviteEmail?: string;
}

/**
 * A planner editing SOMEONE ELSE's membership.
 *
 * What is accepted depends on the target (enforced in the service, because it
 * depends on database state a DTO cannot see):
 *   - placeholder target → every field here
 *   - claimed target     → `role` and `personName` only. `personName` is
 *                          co-owned because it is planning data: a wrong link
 *                          breaks the household's week and the planner runs
 *                          the plan. `displayName` and `inviteEmail` belong to
 *                          the account, which edits them at `PATCH /members/me`.
 */
export class UpdateHouseholdMemberRequest {
  @ApiPropertyOptional({ enum: HOUSEHOLD_ROLES })
  @IsOptional()
  @IsIn(HOUSEHOLD_ROLES, {
    message: `role must be one of: ${HOUSEHOLD_ROLES.join(', ')}`,
  })
  role?: HouseholdRoleName;

  @ApiPropertyOptional({
    minLength: 1,
    maxLength: 120,
    description:
      'Placeholder members only — once a member has an account, their display name is theirs.',
  })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  displayName?: string;

  @ApiPropertyOptional({
    nullable: true,
    description:
      'Accepted on ANY member, placeholder or not: the library link is planning data, and the planner runs the plan. Send null to unlink the member from it.',
    example: 'alex',
  })
  @IsOptional()
  @Transform(trim)
  // null is a meaningful value here — "unlink" — so it must skip the pattern
  // check rather than be rejected by it.
  @ValidateIf((_object, value) => value !== null)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, {
    message: `personName ${PERSON_NAME_MESSAGE}`,
  })
  personName?: string | null;

  @ApiPropertyOptional({
    format: 'email',
    maxLength: 320,
    nullable: true,
    description: 'Placeholder members only. Send null to clear.',
  })
  @IsOptional()
  @Transform(trim)
  @ValidateIf((_object, value) => value !== null)
  @IsEmail()
  @Length(1, 320)
  inviteEmail?: string | null;
}

/**
 * A member editing THEIR OWN membership.
 *
 * `role` is deliberately absent, and its absence is a security control rather
 * than an oversight: if this shape accepted a role, any eater could promote
 * themselves to planner in one request. Role changes go through the
 * planner-only route.
 */
export class UpdateOwnMembershipRequest {
  @ApiPropertyOptional({ minLength: 1, maxLength: 120 })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  displayName?: string;

  @ApiPropertyOptional({
    nullable: true,
    description:
      'The library person you eat as. Send null to stop eating in this plan.',
    example: 'devon',
  })
  @IsOptional()
  @Transform(trim)
  @ValidateIf((_object, value) => value !== null)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, {
    message: `personName ${PERSON_NAME_MESSAGE}`,
  })
  personName?: string | null;
}

// --------------------------------------------------------------------------
// Responses
// --------------------------------------------------------------------------

export class HouseholdMemberView {
  @ApiProperty({ format: 'uuid' })
  id!: string;

  @ApiProperty({ description: 'Human-readable name. Always present.' })
  displayName!: string;

  @ApiProperty({ enum: HOUSEHOLD_ROLES })
  role!: HouseholdRoleName;

  @ApiProperty({
    format: 'uuid',
    nullable: true,
    description:
      'auth.users id, or null when this member is a PLACEHOLDER — a real member with no account yet. Null here is the one flag that distinguishes the two kinds of member; a placeholder can never be a caller.',
  })
  userId!: string | null;

  @ApiProperty({
    nullable: true,
    description: 'Library person key, or null if this member does not eat.',
  })
  personName!: string | null;

  @ApiProperty({
    nullable: true,
    description:
      'Invite intent for a placeholder. Always null once the member has an account.',
  })
  inviteEmail!: string | null;

  @ApiProperty({ format: 'date-time' })
  createdAt!: string;
}

/** One row of "my households" — the household plus the caller's own standing in it. */
export class HouseholdSummary {
  @ApiProperty({ format: 'uuid' })
  id!: string;

  @ApiProperty()
  name!: string;

  @ApiProperty({
    enum: HOUSEHOLD_ROLES,
    description: "The CALLER's role here.",
  })
  role!: HouseholdRoleName;

  @ApiProperty({ description: "The CALLER's display name here." })
  displayName!: string;

  @ApiProperty({
    nullable: true,
    description: "The CALLER's library person key here.",
  })
  personName!: string | null;

  @ApiProperty({
    description:
      'Members in this household, including placeholders and the caller.',
  })
  memberCount!: number;

  @ApiProperty({ format: 'date-time' })
  createdAt!: string;
}

export class HouseholdDetail {
  @ApiProperty({ format: 'uuid' })
  id!: string;

  @ApiProperty()
  name!: string;

  @ApiProperty({ type: [HouseholdMemberView] })
  members!: HouseholdMemberView[];

  @ApiProperty({ format: 'date-time' })
  createdAt!: string;

  @ApiProperty({ format: 'date-time' })
  updatedAt!: string;
}

export class MeResponse {
  @ApiProperty({
    format: 'uuid',
    description: 'auth.users id of the verified caller.',
  })
  userId!: string;

  @ApiProperty({ nullable: true })
  email!: string | null;

  @ApiProperty()
  isAnonymous!: boolean;

  @ApiProperty({
    type: [HouseholdSummary],
    description: 'Every household the caller belongs to.',
  })
  households!: HouseholdSummary[];
}
