import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Auth } from '../auth/auth';
import { describeApiError, isApiErrorResponse } from '../errors/api-error';
import {
  HOUSEHOLD_ROLES,
  ROLE_DESCRIPTIONS,
  type HouseholdRole,
  type UpdateOwnMembershipRequest,
} from '../household/household-api';
import { HouseholdStore, type HouseholdMemberRow } from '../household/household-store';
import { PERSON_NAME_RULE, isValidPersonName, slugifyPersonName } from '../household/person-name';
import { Alert } from '../ui/alert';
import { PendingNote } from '../ui/pending-note';

/**
 * What a row may edit. Only the display name is account-owned — a claimed member
 * still has an editable role and plan identity.
 */
type MemberKind = 'placeholder' | 'claimed' | 'self';

@Component({
  selector: 'app-settings',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, Alert, PendingNote],
  templateUrl: './settings.html',
  styleUrl: './settings.css',
})
export class Settings {
  private readonly auth = inject(Auth);
  private readonly store = inject(HouseholdStore);

  protected readonly roles = HOUSEHOLD_ROLES;
  protected readonly roleDescriptions = ROLE_DESCRIPTIONS;

  protected readonly user = this.auth.user;
  protected readonly displayName = this.auth.displayName;
  protected readonly household = this.store.active;
  protected readonly members = this.store.members;

  protected readonly pending = signal(false);
  protected readonly failure = signal<string | null>(null);
  protected readonly personNameRule = PERSON_NAME_RULE;
  /** Which row's plan identity was rejected, and why. Shown on that row. */
  protected readonly personNameProblem = signal<{ memberId: string; message: string } | null>(
    null,
  );
  protected readonly invalidDisplayName = signal<string | null>(null);

  protected readonly memberForm = inject(FormBuilder).nonNullable.group({
    displayName: ['', [Validators.required, Validators.maxLength(60)]],
    role: ['eater' as HouseholdRole, Validators.required],
    email: ['', Validators.email],
  });

  protected async addMember(): Promise<void> {
    if (this.memberForm.invalid || this.pending()) {
      this.memberForm.markAllAsTouched();
      return;
    }
    await this.guard(async () => {
      const { displayName, role, email } = this.memberForm.getRawValue();
      const personName = slugifyPersonName(displayName);
      // Never sends userId: settings adds placeholders, same as the wizard.
      await this.store.addMember({
        displayName,
        role,
        ...(personName ? { personName } : {}),
        ...(email ? { inviteEmail: email } : {}),
      });
      this.memberForm.reset({ displayName: '', role: 'eater', email: '' });
    });
  }

  /**
   * Which controls a row gets. The API decides this, not us: only displayName
   * and inviteEmail belong to a member's own account, so a claimed member still
   * has an editable role and plan identity. Your own row goes through the self
   * route, which carries no role at all.
   */
  protected memberKind(member: HouseholdMemberRow): MemberKind {
    if (member.isSelf) {
      return 'self';
    }
    return member.userId === null ? 'placeholder' : 'claimed';
  }

  protected async changeRole(member: HouseholdMemberRow, event: Event): Promise<void> {
    const role = (event.target as HTMLSelectElement).value as HouseholdRole;
    await this.guard(() => this.store.updateMember(member.id, { role }));
  }

  protected async changeDisplayName(member: HouseholdMemberRow, event: Event): Promise<void> {
    const displayName = (event.target as HTMLInputElement).value.trim();
    if (!displayName) {
      this.invalidDisplayName.set(member.id);
      return;
    }
    this.invalidDisplayName.set(null);
    await this.guard(() => this.saveProfile(member, { displayName }));
  }

  /**
   * An empty plan identity is not an error — it means this person holds a role but
   * has no portions cooked for them, which is exactly what a planner who does not
   * eat looks like.
   */
  protected async changePersonName(member: HouseholdMemberRow, event: Event): Promise<void> {
    const value = (event.target as HTMLInputElement).value.trim();
    if (value && !isValidPersonName(value)) {
      this.personNameProblem.set({ memberId: member.id, message: PERSON_NAME_RULE });
      return;
    }
    this.personNameProblem.set(null);
    this.pending.set(true);
    this.failure.set(null);
    try {
      await this.saveProfile(member, { personName: value || null });
    } catch (cause) {
      // A plan identity is unique per household, so a clash is about THIS field
      // on THIS row — a page-level banner would make the reader hunt for it.
      if (isApiErrorResponse(cause) && cause.error.code === 'conflict') {
        this.personNameProblem.set({ memberId: member.id, message: describeApiError(cause) });
      } else {
        this.failure.set(describeApiError(cause, 'That change did not save. Try again.'));
      }
    } finally {
      this.pending.set(false);
    }
  }

  /** Your own profile goes through the self route; a placeholder's through the planner one. */
  private saveProfile(member: HouseholdMemberRow, patch: UpdateOwnMembershipRequest): Promise<void> {
    return member.isSelf
      ? this.store.updateSelf(member.id, patch)
      : this.store.updateMember(member.id, patch);
  }

  protected async removeMember(member: HouseholdMemberRow): Promise<void> {
    await this.guard(() => this.store.removeMember(member.id));
  }

  private async guard(work: () => Promise<void>): Promise<void> {
    this.pending.set(true);
    this.failure.set(null);
    try {
      await work();
    } catch (cause) {
      this.failure.set(describeApiError(cause, 'That change did not save. Try again.'));
    } finally {
      this.pending.set(false);
    }
  }
}
