import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Alert } from '../ui/alert';
import { Auth } from './auth';
import { AuthShell } from './auth-shell';

const MIN_PASSWORD_LENGTH = 8;

@Component({
  selector: 'app-signup',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, RouterLink, Alert, AuthShell],
  templateUrl: './signup.html',
})
export class Signup {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  protected readonly minPasswordLength = MIN_PASSWORD_LENGTH;

  protected readonly form = inject(FormBuilder).nonNullable.group({
    displayName: ['', [Validators.required, Validators.maxLength(60)]],
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(MIN_PASSWORD_LENGTH)]],
  });

  protected readonly pending = signal(false);
  protected readonly failure = signal<string | null>(null);
  /** Set when the project requires email confirmation, so there is no session to route on. */
  protected readonly awaitingConfirmation = signal<string | null>(null);

  protected async createAccount(): Promise<void> {
    if (this.form.invalid || this.pending()) {
      this.form.markAllAsTouched();
      return;
    }
    this.pending.set(true);
    this.failure.set(null);
    const outcome = await this.auth.signUp(this.form.getRawValue());
    this.pending.set(false);

    switch (outcome.status) {
      case 'failed':
        this.failure.set(outcome.message);
        return;
      case 'confirm-email':
        this.awaitingConfirmation.set(outcome.email);
        return;
      case 'signed-in':
        await this.router.navigateByUrl('/onboarding');
    }
  }
}
