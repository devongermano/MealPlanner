import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { Transform } from 'class-transformer';
import { IsIn, IsOptional, IsString, IsUUID, Length, Matches, ValidateIf } from 'class-validator';
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
  'must be a lowercase slug matching the library\'s people: key (a-z, 0-9, _ or -, 1-64 chars, starting and ending alphanumeric)';

const trim = ({ value }: { value: unknown }) =>
  typeof value === 'string' ? value.trim() : value;

// --------------------------------------------------------------------------
// Requests
// --------------------------------------------------------------------------

export class CreateHouseholdRequest {
  @ApiProperty({ description: 'Display name.', minLength: 1, maxLength: 120, example: 'The Germanos' })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  name!: string;

  @ApiPropertyOptional({
    description:
      'Which person in the library the creator eats as. Omit if the creator plans but does not eat — the membership is still created, as a planner.',
    example: 'jimbo',
  })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, { message: `personName ${PERSON_NAME_MESSAGE}` })
  personName?: string;
}

export class UpdateHouseholdRequest {
  @ApiProperty({ minLength: 1, maxLength: 120 })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  name!: string;
}

export class AddHouseholdMemberRequest {
  @ApiProperty({
    format: 'uuid',
    description:
      'auth.users id of the account to add. The caller must already know it — this API does not look accounts up by email, because a lookup endpoint tells an attacker which addresses have accounts. Email invitations are the intended replacement (see README open questions).',
  })
  @IsUUID()
  userId!: string;

  @ApiProperty({ enum: HOUSEHOLD_ROLES })
  @IsIn(HOUSEHOLD_ROLES, { message: `role must be one of: ${HOUSEHOLD_ROLES.join(', ')}` })
  role!: HouseholdRoleName;

  @ApiPropertyOptional({ description: 'The library person this account eats as.', example: 'alice' })
  @IsOptional()
  @Transform(trim)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, { message: `personName ${PERSON_NAME_MESSAGE}` })
  personName?: string;
}

export class UpdateHouseholdMemberRequest {
  @ApiPropertyOptional({ enum: HOUSEHOLD_ROLES })
  @IsOptional()
  @IsIn(HOUSEHOLD_ROLES, { message: `role must be one of: ${HOUSEHOLD_ROLES.join(', ')}` })
  role?: HouseholdRoleName;

  @ApiPropertyOptional({
    nullable: true,
    description:
      'The library person this account eats as. Send null to unlink (the account keeps its membership but stops being an eater in the plan).',
    example: 'alice',
  })
  @IsOptional()
  @Transform(trim)
  // null is a meaningful value here — "unlink" — so it must skip the pattern
  // check rather than be rejected by it.
  @ValidateIf((_object, value) => value !== null)
  @IsString()
  @Matches(PERSON_NAME_PATTERN, { message: `personName ${PERSON_NAME_MESSAGE}` })
  personName?: string | null;
}

// --------------------------------------------------------------------------
// Responses
// --------------------------------------------------------------------------

export class HouseholdMemberView {
  @ApiProperty({ format: 'uuid' })
  id!: string;

  @ApiProperty({ format: 'uuid', description: 'auth.users id.' })
  userId!: string;

  @ApiProperty({ enum: HOUSEHOLD_ROLES })
  role!: HouseholdRoleName;

  @ApiProperty({ nullable: true, description: 'Library person key, or null if this account does not eat.' })
  personName!: string | null;

  @ApiProperty({ format: 'date-time' })
  createdAt!: string;
}

/** One row of "my households" — the household plus the caller's own standing in it. */
export class HouseholdSummary {
  @ApiProperty({ format: 'uuid' })
  id!: string;

  @ApiProperty()
  name!: string;

  @ApiProperty({ enum: HOUSEHOLD_ROLES, description: "The CALLER's role here." })
  role!: HouseholdRoleName;

  @ApiProperty({ nullable: true, description: "The CALLER's library person key here." })
  personName!: string | null;

  @ApiProperty({ description: 'Members in this household, including the caller.' })
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
  @ApiProperty({ format: 'uuid', description: 'auth.users id of the verified caller.' })
  userId!: string;

  @ApiProperty({ nullable: true })
  email!: string | null;

  @ApiProperty()
  isAnonymous!: boolean;

  @ApiProperty({ type: [HouseholdSummary], description: 'Every household the caller belongs to.' })
  households!: HouseholdSummary[];
}
