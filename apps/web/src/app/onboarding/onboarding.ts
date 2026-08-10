import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { Auth } from '../auth/auth';
import {
  HOUSEHOLD_ROLES,
  ROLE_DESCRIPTIONS,
  type HouseholdRole,
} from '../household/household-api';
import { HouseholdStore } from '../household/household-store';
import { Alert } from '../ui/alert';

type Step = 'household' | 'members' | 'done';

const STEPS: readonly { readonly id: Step; readonly label: string }[] = [
  { id: 'household', label: 'Household' },
  { id: 'members', label: 'People' },
  { id: 'done', label: 'Ready' },
];

@Component({
  selector: 'app-onboarding',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, Alert],
  templateUrl: './onboarding.html',
  styleUrl: './onboarding.css',
})
export class Onboarding {
  private readonly auth = inject(Auth);
  private readonly store = inject(HouseholdStore);
  private readonly router = inject(Router);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly steps = STEPS;
  protected readonly roles = HOUSEHOLD_ROLES;
  protected readonly roleDescriptions = ROLE_DESCRIPTIONS;

  protected readonly step = signal<Step>('household');
  protected readonly pending = signal(false);
  protected readonly failure = signal<string | null>(null);

  protected readonly household = this.store.active;
  protected readonly members = this.store.members;

  protected readonly stepIndex = computed(() =>
    STEPS.findIndex((step) => step.id === this.step()),
  );

  protected readonly householdForm = this.formBuilder.nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(80)]],
    creatorRole: ['planner' as HouseholdRole, Validators.required],
  });

  protected readonly memberForm = this.formBuilder.nonNullable.group({
    displayName: ['', [Validators.required, Validators.maxLength(60)]],
    role: ['eater' as HouseholdRole, Validators.required],
    email: ['', Validators.email],
  });

  protected async createHousehold(): Promise<void> {
    if (this.householdForm.invalid || this.pending()) {
      this.householdForm.markAllAsTouched();
      return;
    }
    await this.guard(async () => {
      const { name, creatorRole } = this.householdForm.getRawValue();
      await this.store.createHousehold({
        name,
        creatorRole,
        creatorDisplayName: this.auth.displayName(),
      });
      this.step.set('members');
    });
  }

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

  protected async removeMember(memberId: string): Promise<void> {
    await this.guard(() => this.store.removeMember(memberId));
  }

  protected finishMembers(): void {
    this.step.set('done');
  }

  protected backToMembers(): void {
    this.step.set('members');
  }

  protected async enterApp(): Promise<void> {
    await this.router.navigateByUrl('/app');
  }

  private async guard(work: () => Promise<void>): Promise<void> {
    this.pending.set(true);
    this.failure.set(null);
    try {
      await work();
    } catch (cause) {
      this.failure.set(
        cause instanceof Error ? cause.message : 'That did not save. Try again in a moment.',
      );
    } finally {
      this.pending.set(false);
    }
  }
}
