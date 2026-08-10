import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  Param,
  Patch,
  Post,
  UseGuards,
} from '@nestjs/common';
import {
  ApiBearerAuth,
  ApiOperation,
  ApiParam,
  ApiResponse,
  ApiTags,
} from '@nestjs/swagger';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthenticatedUser } from '../auth/authenticated-user';
import {
  ApiAuthenticatedErrors,
  ApiHouseholdScopedErrors,
  uuidParam,
} from '../common/swagger';
import {
  CurrentMembership,
  type CurrentMembershipInfo,
} from './current-membership.decorator';
import {
  HOUSEHOLD_ID_PARAM,
  HouseholdMembershipGuard,
} from './household-membership.guard';
import { HouseholdsService } from './households.service';
import { MinRole } from './roles';
import {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdDetail,
  HouseholdMemberView,
  HouseholdSummary,
  UpdateHouseholdMemberRequest,
  UpdateHouseholdRequest,
  UpdateOwnMembershipRequest,
} from './dto/household.dto';

/**
 * Authorization map for this controller — the whole of it:
 *
 * | Route                                      | Requires                    |
 * |--------------------------------------------|-----------------------------|
 * | POST   /households                          | any authenticated account   |
 * | GET    /households                          | any authenticated account   |
 * | GET    /households/:id                      | member (eater+)             |
 * | PATCH  /households/:id                      | planner                     |
 * | DELETE /households/:id                      | planner                     |
 * | GET    /households/:id/members              | member (eater+)             |
 * | POST   /households/:id/members              | planner                     |
 * | PATCH  /households/:id/members/me           | member (eater+), self only  |
 * | DELETE /households/:id/members/me           | member (eater+), self only  |
 * | PATCH  /households/:id/members/:memberId    | planner (see note below)    |
 * | DELETE /households/:id/members/:memberId    | planner                     |
 *
 * Every `:id` route is gated by `HouseholdMembershipGuard`, so the floor is
 * always membership and `@MinRole` only ever raises it. Leaving and self-edit
 * are their own routes rather than special cases inside the planner handlers,
 * which keeps the role decision entirely in the guard — and keeps `role` out of
 * the request shape an eater is allowed to submit.
 *
 * One rule the guard cannot express, because it depends on the TARGET row and
 * not on the caller: a planner owns a PLACEHOLDER member outright, but on a
 * member who has an account may change only `role` and `personName` — the
 * planning fields. That lives in `assertPlannerMayEdit` in the service.
 */
@ApiTags('households')
@ApiBearerAuth()
@Controller('households')
export class HouseholdsController {
  constructor(private readonly households: HouseholdsService) {}

  @Post()
  @ApiOperation({
    summary: 'Create a household',
    description:
      'The caller becomes its first planner. Not a parameter: a household with no planner could never be administered.',
  })
  @ApiResponse({ status: 201, type: HouseholdDetail })
  @ApiAuthenticatedErrors()
  create(
    @CurrentUser() user: AuthenticatedUser,
    @Body() body: CreateHouseholdRequest,
  ): Promise<HouseholdDetail> {
    return this.households.create(user, body);
  }

  @Get()
  @ApiOperation({
    summary: 'List the households I belong to',
    description:
      'Scoped by membership. There is no route that lists households the caller is not in.',
  })
  @ApiResponse({ status: 200, type: [HouseholdSummary] })
  @ApiAuthenticatedErrors()
  listMine(
    @CurrentUser() user: AuthenticatedUser,
  ): Promise<HouseholdSummary[]> {
    return this.households.listMine(user.userId);
  }

  @Get(`:${HOUSEHOLD_ID_PARAM}`)
  @UseGuards(HouseholdMembershipGuard)
  @ApiOperation({ summary: 'Get one household with its members' })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 200, type: HouseholdDetail })
  @ApiHouseholdScopedErrors()
  get(
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
  ): Promise<HouseholdDetail> {
    return this.households.getDetail(householdId);
  }

  @Patch(`:${HOUSEHOLD_ID_PARAM}`)
  @UseGuards(HouseholdMembershipGuard)
  @MinRole('planner')
  @ApiOperation({ summary: 'Rename a household' })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 200, type: HouseholdDetail })
  @ApiHouseholdScopedErrors()
  rename(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @Body() body: UpdateHouseholdRequest,
  ): Promise<HouseholdDetail> {
    return this.households.rename(user, householdId, body);
  }

  @Delete(`:${HOUSEHOLD_ID_PARAM}`)
  @UseGuards(HouseholdMembershipGuard)
  @MinRole('planner')
  @HttpCode(HttpStatus.NO_CONTENT)
  @ApiOperation({
    summary: 'Delete a household',
    description:
      'Memberships cascade. The audit trail does not — the record that it was deleted outlives it.',
  })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 204, description: 'Deleted.' })
  @ApiHouseholdScopedErrors()
  remove(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
  ): Promise<void> {
    return this.households.remove(user, householdId);
  }

  @Get(`:${HOUSEHOLD_ID_PARAM}/members`)
  @UseGuards(HouseholdMembershipGuard)
  @ApiOperation({ summary: 'List members of a household' })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 200, type: [HouseholdMemberView] })
  @ApiHouseholdScopedErrors()
  listMembers(
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
  ): Promise<HouseholdMemberView[]> {
    return this.households.listMembers(householdId);
  }

  @Post(`:${HOUSEHOLD_ID_PARAM}/members`)
  @UseGuards(HouseholdMembershipGuard)
  @MinRole('planner')
  @ApiOperation({
    summary: 'Add a member to a household',
    description:
      'Omit userId to create a PLACEHOLDER — a real member with no account yet, which is the normal path: a household must be plannable before everyone has signed up. Send userId only when the caller already knows the account id.',
  })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 201, type: HouseholdMemberView })
  @ApiResponse({
    status: 409,
    description: 'Already a member, or that person is already linked.',
  })
  @ApiHouseholdScopedErrors()
  addMember(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @Body() body: AddHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    return this.households.addMember(user, householdId, body);
  }

  /**
   * Editing your own membership.
   *
   * The target is `req.membership.id` — the row the guard resolved from the
   * caller's own token — never a value from the URL or the body, so this route
   * cannot be pointed at anyone else.
   *
   * `UpdateOwnMembershipRequest` has no `role` field, and that omission is the
   * security control: this route is open to eaters, so a `role` here would be
   * one-request self-promotion. Combined with `forbidNonWhitelisted`, sending
   * one is a 400 rather than a silent ignore.
   */
  @Patch(`:${HOUSEHOLD_ID_PARAM}/members/me`)
  @UseGuards(HouseholdMembershipGuard)
  @ApiOperation({
    summary: 'Edit my own display name or linked person',
    description:
      'Your profile is yours: a planner cannot change it for you, and this route cannot change your role.',
  })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 200, type: HouseholdMemberView })
  @ApiResponse({
    status: 409,
    description: 'That person is already linked to another member.',
  })
  @ApiHouseholdScopedErrors()
  updateOwnMembership(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @CurrentMembership() membership: CurrentMembershipInfo,
    @Body() body: UpdateOwnMembershipRequest,
  ): Promise<HouseholdMemberView> {
    return this.households.updateOwnMembership(
      user,
      householdId,
      membership.id,
      body,
    );
  }

  /**
   * Declared before `:memberId` so Express matches the literal first. It would
   * also survive the other order (`me` fails UUID parsing), but relying on that
   * is relying on an error path.
   */
  @Delete(`:${HOUSEHOLD_ID_PARAM}/members/me`)
  @UseGuards(HouseholdMembershipGuard)
  @HttpCode(HttpStatus.NO_CONTENT)
  @ApiOperation({
    summary: 'Leave a household',
    description:
      'Any member may remove their own membership. A sole planner cannot — promote someone first.',
  })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiResponse({ status: 204, description: 'Left.' })
  @ApiResponse({ status: 409, description: 'You are the last planner.' })
  @ApiHouseholdScopedErrors()
  leave(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @CurrentMembership() membership: CurrentMembershipInfo,
  ): Promise<void> {
    return this.households.leave(user, householdId, membership.id);
  }

  @Patch(`:${HOUSEHOLD_ID_PARAM}/members/:memberId`)
  @UseGuards(HouseholdMembershipGuard)
  @MinRole('planner')
  @ApiOperation({
    summary: "Change another member's role, or edit a placeholder",
    description:
      'A placeholder member (userId null) is fully editable by a planner. On a member who has an account, a planner may change role and personName (planning data) but not displayName — that is theirs.',
  })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiParam({ name: 'memberId', format: 'uuid' })
  @ApiResponse({ status: 200, type: HouseholdMemberView })
  @ApiResponse({
    status: 409,
    description: 'Would leave the household with no planner.',
  })
  @ApiHouseholdScopedErrors()
  updateMember(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @Param('memberId', uuidParam('memberId')) memberId: string,
    @Body() body: UpdateHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    return this.households.updateMember(user, householdId, memberId, body);
  }

  @Delete(`:${HOUSEHOLD_ID_PARAM}/members/:memberId`)
  @UseGuards(HouseholdMembershipGuard)
  @MinRole('planner')
  @HttpCode(HttpStatus.NO_CONTENT)
  @ApiOperation({ summary: 'Remove a member from a household' })
  @ApiParam({ name: HOUSEHOLD_ID_PARAM, format: 'uuid' })
  @ApiParam({ name: 'memberId', format: 'uuid' })
  @ApiResponse({ status: 204, description: 'Removed.' })
  @ApiResponse({
    status: 409,
    description: 'Would leave the household with no planner.',
  })
  @ApiHouseholdScopedErrors()
  removeMember(
    @CurrentUser() user: AuthenticatedUser,
    @Param(HOUSEHOLD_ID_PARAM) householdId: string,
    @Param('memberId', uuidParam('memberId')) memberId: string,
  ): Promise<void> {
    return this.households.removeMember(user, householdId, memberId);
  }
}
