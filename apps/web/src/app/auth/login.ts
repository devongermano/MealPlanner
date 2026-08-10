import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Alert } from '../ui/alert';
import { Auth } from './auth';
import { AuthShell } from './auth-shell';

@Component({
  selector: 'app-login',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule, RouterLink, Alert, AuthShell],
  templateUrl: './login.html',
})
export class Login {
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  protected readonly form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  protected readonly pending = signal(false);
  protected readonly failure = signal<string | null>(null);

  protected async signIn(): Promise<void> {
    if (this.form.invalid || this.pending()) {
      this.form.markAllAsTouched();
      return;
    }
    this.pending.set(true);
    this.failure.set(null);
    const outcome = await this.auth.signIn(this.form.getRawValue());
    this.pending.set(false);

    if (outcome.status === 'failed') {
      this.failure.set(outcome.message);
      return;
    }
    if (outcome.status === 'confirm-email') {
      this.failure.set('This account still needs to be confirmed. Check your inbox for the link.');
      return;
    }
    await this.router.navigateByUrl(this.nextUrl());
  }

  /** Returns the user to where the guard interrupted them, defaulting to the dashboard. */
  private nextUrl(): string {
    const next = this.router.parseUrl(this.router.url).queryParams['next'];
    return typeof next === 'string' && next.startsWith('/') ? next : '/app';
  }
}
