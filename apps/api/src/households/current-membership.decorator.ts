import { ExecutionContext, createParamDecorator } from '@nestjs/common';
import { ApiException } from '../common/api-error';
import type { AuthenticatedRequest } from '../auth/authenticated-user';
import type { HouseholdRoleName } from './roles';

export interface CurrentMembershipInfo {
  id: string;
  householdId: string;
  userId: string;
  role: HouseholdRoleName;
  personName: string | null;
}

/**
 * The caller's membership in the household named by the route, as resolved by
 * `HouseholdMembershipGuard`.
 *
 * Using this instead of re-querying is what stops a handler from authorizing
 * against one row and acting on another.
 */
export const CurrentMembership = createParamDecorator(
  (_data: unknown, context: ExecutionContext): CurrentMembershipInfo => {
    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    if (!request.membership) {
      // Only reachable if a route asks for a membership without the guard that
      // resolves one. Fail closed.
      throw ApiException.forbidden('Household membership was not resolved for this route.');
    }
    return request.membership as CurrentMembershipInfo;
  },
);
