import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { ApiException } from '../common/api-error';
import type { AuthenticatedRequest } from '../auth/authenticated-user';
import { PrismaService } from '../prisma/prisma.service';
import { MIN_ROLE_KEY, type HouseholdRoleName, roleSatisfies } from './roles';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Route parameter every household-scoped route must name its household with. */
export const HOUSEHOLD_ID_PARAM = 'householdId';

/**
 * The single choke point for household authorization.
 *
 * Every household-scoped route passes through here, so there is exactly one
 * place to audit and exactly one place a mistake can live. It resolves the
 * caller's membership, enforces the `@MinRole` floor, and hands the membership
 * to the handler on `req.membership` so services never re-query it (and can
 * never re-query it *differently*).
 *
 * NOT-A-MEMBER IS 404, NOT 403. A 403 on a household you are not in is an
 * existence oracle: it distinguishes "this id names a real household" from
 * "this id names nothing", which is enough to enumerate households and to
 * confirm a guessed id. PRD §7 asks for per-household isolation; leaking
 * existence is a hole in it. The message is identical in both cases too — a
 * distinguishable body is the same oracle with extra steps.
 */
@Injectable()
export class HouseholdMembershipGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly prisma: PrismaService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();

    // Defence in depth: the global auth guard runs first and this should be
    // unreachable. If guard ordering is ever changed, fail closed here rather
    // than resolving membership for `undefined`.
    const user = request.user;
    if (!user) {
      throw ApiException.unauthenticated();
    }

    const householdId = request.params?.[HOUSEHOLD_ID_PARAM];
    // Guards run BEFORE pipes, so ParseUUIDPipe on the param cannot help us
    // here; a malformed id would otherwise reach Prisma and surface as a 500.
    if (typeof householdId !== 'string' || !UUID_RE.test(householdId)) {
      throw new ApiException('validation_failed', 'Malformed household id.', [
        { field: HOUSEHOLD_ID_PARAM, message: 'must be a UUID' },
      ]);
    }

    const membership = await this.prisma.householdMember.findUnique({
      where: { householdId_userId: { householdId, userId: user.userId } },
    });
    if (!membership) {
      throw ApiException.notFound();
    }

    const required =
      this.reflector.getAllAndOverride<HouseholdRoleName>(MIN_ROLE_KEY, [
        context.getHandler(),
        context.getClass(),
      ]) ?? 'eater';

    if (!roleSatisfies(membership.role, required)) {
      throw ApiException.forbidden(
        `This action requires the "${required}" role in this household; you are "${membership.role}".`,
      );
    }

    request.membership = membership;
    return true;
  }
}
