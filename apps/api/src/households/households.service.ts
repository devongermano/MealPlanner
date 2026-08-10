import { Injectable } from '@nestjs/common';
import { Prisma, type HouseholdMember } from '@prisma/client';
import { ApiException } from '../common/api-error';
import type { AuthenticatedUser } from '../auth/authenticated-user';
import { PrismaService } from '../prisma/prisma.service';
import {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdDetail,
  HouseholdMemberView,
  HouseholdSummary,
  UpdateHouseholdMemberRequest,
  UpdateHouseholdRequest,
} from './dto/household.dto';

type Tx = Prisma.TransactionClient;

/** Audit verbs. Closed set so the log stays queryable. */
type AuditAction =
  | 'household.created'
  | 'household.renamed'
  | 'household.deleted'
  | 'member.added'
  | 'member.updated'
  | 'member.removed'
  | 'member.left';

/**
 * Membership and household CRUD. Nothing else.
 *
 * This service holds no product logic — no plans, no solves, no meals. Its only
 * job is to decide who belongs to what and to record the decision. Role checks
 * live in `HouseholdMembershipGuard`, not here; the one thing this file
 * enforces beyond the guard is the last-planner invariant, which is a data
 * integrity rule rather than an authorization rule.
 */
@Injectable()
export class HouseholdsService {
  constructor(private readonly prisma: PrismaService) {}

  // ------------------------------------------------------------------------
  // Reads
  // ------------------------------------------------------------------------

  /** Every household the caller belongs to. Scoped by membership, never by id. */
  async listMine(userId: string): Promise<HouseholdSummary[]> {
    const memberships = await this.prisma.householdMember.findMany({
      where: { userId },
      include: {
        household: { include: { _count: { select: { members: true } } } },
      },
      orderBy: { createdAt: 'asc' },
    });

    return memberships.map((membership) => ({
      id: membership.household.id,
      name: membership.household.name,
      role: membership.role,
      personName: membership.personName,
      memberCount: membership.household._count.members,
      createdAt: membership.household.createdAt.toISOString(),
    }));
  }

  /**
   * Full household including its member list.
   *
   * Reachable only through `HouseholdMembershipGuard`, which has already proven
   * the caller is a member — so there is no ownership check here and there must
   * not be a route that calls this without that guard.
   */
  async getDetail(householdId: string): Promise<HouseholdDetail> {
    const household = await this.prisma.household.findUnique({
      where: { id: householdId },
      include: { members: { orderBy: { createdAt: 'asc' } } },
    });
    // The guard found a membership, so this is a race with a concurrent delete
    // rather than an authorization question.
    if (!household) throw ApiException.notFound();

    return {
      id: household.id,
      name: household.name,
      members: household.members.map(toMemberView),
      createdAt: household.createdAt.toISOString(),
      updatedAt: household.updatedAt.toISOString(),
    };
  }

  async listMembers(householdId: string): Promise<HouseholdMemberView[]> {
    const members = await this.prisma.householdMember.findMany({
      where: { householdId },
      orderBy: { createdAt: 'asc' },
    });
    return members.map(toMemberView);
  }

  // ------------------------------------------------------------------------
  // Writes
  // ------------------------------------------------------------------------

  /** Creates a household with the caller as its first planner. */
  async create(
    user: AuthenticatedUser,
    body: CreateHouseholdRequest,
  ): Promise<HouseholdDetail> {
    return this.prisma.$transaction(async (tx) => {
      const household = await tx.household.create({
        data: { name: body.name },
      });
      const member = await tx.householdMember.create({
        data: {
          householdId: household.id,
          userId: user.userId,
          // Not a parameter. Whoever creates a household administers it, and a
          // household created with no planner would be unadministrable.
          role: 'planner',
          personName: body.personName ?? null,
        },
      });
      await this.audit(
        tx,
        household.id,
        user.userId,
        'household.created',
        household.id,
        {
          name: household.name,
          founderMemberId: member.id,
        },
      );

      return {
        id: household.id,
        name: household.name,
        members: [toMemberView(member)],
        createdAt: household.createdAt.toISOString(),
        updatedAt: household.updatedAt.toISOString(),
      };
    });
  }

  async rename(
    user: AuthenticatedUser,
    householdId: string,
    body: UpdateHouseholdRequest,
  ): Promise<HouseholdDetail> {
    await this.prisma.$transaction(async (tx) => {
      const before = await tx.household.findUnique({
        where: { id: householdId },
      });
      if (!before) throw ApiException.notFound();

      await tx.household.update({
        where: { id: householdId },
        data: { name: body.name },
      });
      await this.audit(
        tx,
        householdId,
        user.userId,
        'household.renamed',
        householdId,
        {
          before: before.name,
          after: body.name,
        },
      );
    });
    return this.getDetail(householdId);
  }

  async remove(user: AuthenticatedUser, householdId: string): Promise<void> {
    await this.prisma.$transaction(async (tx) => {
      const household = await tx.household.findUnique({
        where: { id: householdId },
      });
      if (!household) throw ApiException.notFound();

      // Written before the delete and never cascaded away — the audit table has
      // no foreign key to households precisely so this record survives.
      await this.audit(
        tx,
        householdId,
        user.userId,
        'household.deleted',
        householdId,
        {
          name: household.name,
        },
      );
      // Memberships cascade; nothing else hangs off a household yet.
      await tx.household.delete({ where: { id: householdId } });
    });
  }

  async addMember(
    user: AuthenticatedUser,
    householdId: string,
    body: AddHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    return this.prisma.$transaction(async (tx) => {
      await this.lockHousehold(tx, householdId);

      const existing = await tx.householdMember.findUnique({
        where: { householdId_userId: { householdId, userId: body.userId } },
      });
      if (existing) {
        throw ApiException.conflict(
          'That account is already a member of this household.',
        );
      }
      await this.assertPersonNameFree(
        tx,
        householdId,
        body.personName ?? null,
        null,
      );

      const member = await tx.householdMember.create({
        data: {
          householdId,
          userId: body.userId,
          role: body.role,
          personName: body.personName ?? null,
        },
      });
      await this.audit(
        tx,
        householdId,
        user.userId,
        'member.added',
        member.id,
        {
          userId: member.userId,
          role: member.role,
          personName: member.personName,
        },
      );
      return toMemberView(member);
    });
  }

  async updateMember(
    user: AuthenticatedUser,
    householdId: string,
    memberId: string,
    body: UpdateHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    if (body.role === undefined && body.personName === undefined) {
      throw ApiException.validationFailed(
        [{ field: 'role', message: 'send at least one of role or personName' }],
        'Nothing to update.',
      );
    }

    return this.prisma.$transaction(async (tx) => {
      await this.lockHousehold(tx, householdId);
      const member = await this.findMemberInHousehold(
        tx,
        householdId,
        memberId,
      );

      if (body.personName !== undefined) {
        await this.assertPersonNameFree(
          tx,
          householdId,
          body.personName,
          memberId,
        );
      }
      // Demoting the last planner would leave nobody able to administer the
      // household — including nobody able to promote a replacement.
      if (
        body.role !== undefined &&
        member.role === 'planner' &&
        body.role !== 'planner'
      ) {
        await this.assertAnotherPlannerRemains(tx, householdId, memberId);
      }

      const updated = await tx.householdMember.update({
        where: { id: memberId },
        data: {
          ...(body.role !== undefined ? { role: body.role } : {}),
          ...(body.personName !== undefined
            ? { personName: body.personName }
            : {}),
        },
      });
      await this.audit(
        tx,
        householdId,
        user.userId,
        'member.updated',
        memberId,
        {
          before: { role: member.role, personName: member.personName },
          after: { role: updated.role, personName: updated.personName },
        },
      );
      return toMemberView(updated);
    });
  }

  /** Planner removing someone (possibly themselves) by member id. */
  async removeMember(
    user: AuthenticatedUser,
    householdId: string,
    memberId: string,
  ): Promise<void> {
    await this.deleteMembership(user, householdId, memberId, 'member.removed');
  }

  /** Any member removing their own membership. */
  async leave(
    user: AuthenticatedUser,
    householdId: string,
    memberId: string,
  ): Promise<void> {
    await this.deleteMembership(user, householdId, memberId, 'member.left');
  }

  private async deleteMembership(
    user: AuthenticatedUser,
    householdId: string,
    memberId: string,
    action: AuditAction,
  ): Promise<void> {
    await this.prisma.$transaction(async (tx) => {
      await this.lockHousehold(tx, householdId);
      const member = await this.findMemberInHousehold(
        tx,
        householdId,
        memberId,
      );

      if (member.role === 'planner') {
        await this.assertAnotherPlannerRemains(tx, householdId, memberId);
      }

      await tx.householdMember.delete({ where: { id: memberId } });
      await this.audit(tx, householdId, user.userId, action, memberId, {
        userId: member.userId,
        role: member.role,
        personName: member.personName,
      });
    });
  }

  // ------------------------------------------------------------------------
  // Invariants and helpers
  // ------------------------------------------------------------------------

  /**
   * Serialises membership mutations for one household.
   *
   * Without this, two planners demoting each other concurrently both read
   * "another planner exists", both commit, and the household is left with zero
   * planners — a state no sequence of individually-valid requests should be
   * able to reach. A row lock on the household is the smallest thing that makes
   * every check-then-write below atomic, and it costs nothing at beta scale
   * where membership changes are rare.
   */
  private async lockHousehold(tx: Tx, householdId: string): Promise<void> {
    const rows = await tx.$queryRaw<
      { id: string }[]
    >`SELECT id FROM households WHERE id = ${householdId}::uuid FOR UPDATE`;
    if (rows.length === 0) throw ApiException.notFound();
  }

  /**
   * Scopes a member lookup to the household from the URL.
   *
   * The `householdId` filter is the important half: without it, a planner of
   * household A could pass a member id belonging to household B and mutate a
   * household they have no standing in. The guard authorizes the household in
   * the path — so every child resource must be proven to live in that
   * household, not merely to exist.
   */
  private async findMemberInHousehold(
    tx: Tx,
    householdId: string,
    memberId: string,
  ): Promise<HouseholdMember> {
    const member = await tx.householdMember.findFirst({
      where: { id: memberId, householdId },
    });
    if (!member) throw ApiException.notFound();
    return member;
  }

  private async assertAnotherPlannerRemains(
    tx: Tx,
    householdId: string,
    excludingMemberId: string,
  ): Promise<void> {
    const remaining = await tx.householdMember.count({
      where: { householdId, role: 'planner', id: { not: excludingMemberId } },
    });
    if (remaining === 0) {
      throw ApiException.conflict(
        'A household must always have at least one planner. Promote another member first.',
      );
    }
  }

  private async assertPersonNameFree(
    tx: Tx,
    householdId: string,
    personName: string | null | undefined,
    excludingMemberId: string | null,
  ): Promise<void> {
    if (!personName) return;
    const clash = await tx.householdMember.findFirst({
      where: {
        householdId,
        personName,
        ...(excludingMemberId ? { id: { not: excludingMemberId } } : {}),
      },
    });
    if (clash) {
      throw ApiException.conflict(
        `Another member is already linked to the person "${personName}".`,
      );
    }
  }

  /**
   * Appends to the audit trail (PRD §10).
   *
   * Always called with the same `tx` as the mutation it describes, so a
   * committed change without its audit row is not a reachable state.
   */
  private async audit(
    tx: Tx,
    householdId: string,
    actorUserId: string,
    action: AuditAction,
    targetId: string | null,
    detail: Prisma.InputJsonValue,
  ): Promise<void> {
    await tx.householdAuditEntry.create({
      data: { householdId, actorUserId, action, targetId, detail },
    });
  }
}

function toMemberView(member: HouseholdMember): HouseholdMemberView {
  return {
    id: member.id,
    userId: member.userId,
    role: member.role,
    personName: member.personName,
    createdAt: member.createdAt.toISOString(),
  };
}
