import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Auth } from '../auth/auth';
import {
  HOUSEHOLD_ROLES,
  ROLE_DESCRIPTIONS,
  type HouseholdMember,
  type HouseholdRole,
} from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { Alert } from '../ui/alert';
import { PendingNote } from '../ui/pending-note';

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

  protected async changeRole(member: HouseholdMember, event: Event): Promise<void> {
    const role = (event.target as HTMLSelectElement).value as HouseholdRole;
    await this.guard(() => this.store.updateMemberRole(member.id, role));
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
      this.failure.set(
        cause instanceof Error ? cause.message : 'That change did not save. Try again.',
      );
    } finally {
      this.pending.set(false);
    }
  }
}
