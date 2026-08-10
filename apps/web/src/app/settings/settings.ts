import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Auth } from '../auth/auth';
import {
  HOUSEHOLD_ROLES,
  ROLE_DESCRIPTIONS,
  type HouseholdMember,
  type HouseholdRole,
  type UpdateSelfInput,
} from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { PERSON_NAME_RULE, isValidPersonName } from '../household/person-name';
import { describeApiError } from '../errors/api-error';
import { Alert } from '../ui/alert';
import { PendingNote } from '../ui/pending-note';

/** A placeholder is fully editable, a claimed member is role-only, you edit yourself. */
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
  /** Id of the member whose plan identity was last rejected, if any. */
  protected readonly invalidPersonName = signal<string | null>(null);
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
      await this.store.addMember({ displayName, role, email: email || null });
      this.memberForm.reset({ displayName: '', role: 'eater', email: '' });
    });
  }

  /**
   * Which controls a row gets. The API decides this, not us: a placeholder is
   * fully editable by a planner; a member who has an account owns their profile,
   * so only their role can be changed; and your own row goes through the self
   * route, which carries no role at all.
   */
  protected memberKind(member: HouseholdMember): MemberKind {
    if (member.isSelf) {
      return 'self';
    }
    return member.userId === null ? 'placeholder' : 'claimed';
  }

  protected async changeRole(member: HouseholdMember, event: Event): Promise<void> {
    const role = (event.target as HTMLSelectElement).value as HouseholdRole;
    await this.guard(() => this.store.updateMember(member.id, { role }));
  }

  protected async changeDisplayName(member: HouseholdMember, event: Event): Promise<void> {
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
  protected async changePersonName(member: HouseholdMember, event: Event): Promise<void> {
    const value = (event.target as HTMLInputElement).value.trim();
    if (value && !isValidPersonName(value)) {
      this.invalidPersonName.set(member.id);
      return;
    }
    this.invalidPersonName.set(null);
    await this.guard(() => this.saveProfile(member, { personName: value || null }));
  }

  /** Your own profile goes through the self route; a placeholder's through the planner one. */
  private saveProfile(member: HouseholdMember, patch: UpdateSelfInput): Promise<void> {
    return member.isSelf
      ? this.store.updateSelf(member.id, patch)
      : this.store.updateMember(member.id, patch);
  }

  protected async removeMember(member: HouseholdMember): Promise<void> {
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
