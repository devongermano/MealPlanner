import { Injectable, inject } from '@angular/core';
import { Auth } from '../auth/auth';
import { RUNTIME_CONFIG } from '../config/runtime-config';
import { toApiError } from '../errors/api-error';
import type {
  AddHouseholdMemberRequest,
  CreateHouseholdRequest,
  HouseholdApi,
  HouseholdMemberView,
  HouseholdSummary,
  MeResponse,
  UpdateHouseholdMemberRequest,
  UpdateOwnMembershipRequest,
} from './household-api';
import type { HouseholdDetail } from './contracts';

/**
 * The real household client. Talks to the NestJS API — never to Supabase, which
 * this app uses for authentication only (ARCHITECTURE.md).
 */
@Injectable()
export class HouseholdHttp implements HouseholdApi {
  private readonly auth = inject(Auth);
  private readonly config = inject(RUNTIME_CONFIG);
  private readonly baseUrl = this.config.apiBaseUrl.replace(/\/+$/, '');

  me(): Promise<MeResponse> {
    return this.send<MeResponse>('GET', '/me');
  }

  async createHousehold(input: CreateHouseholdRequest): Promise<HouseholdSummary> {
    const detail = await this.send<HouseholdDetail>('POST', '/households', input);
    // POST returns the full detail; the caller wants the summary shape that
    // GET /me hands back, so the caller's own standing is derived here.
    const self = detail.members.find((member) => member.userId !== null);
    return {
      id: detail.id,
      name: detail.name,
      role: self?.role ?? 'planner',
      displayName: self?.displayName ?? '',
      personName: self?.personName ?? null,
      memberCount: detail.members.length,
      createdAt: detail.createdAt,
    };
  }

  listMembers(householdId: string): Promise<readonly HouseholdMemberView[]> {
    return this.send<HouseholdMemberView[]>('GET', `/households/${householdId}/members`);
  }

  addMember(
    householdId: string,
    input: AddHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    return this.send<HouseholdMemberView>(
      'POST',
      `/households/${householdId}/members`,
      input,
    );
  }

  updateMember(
    householdId: string,
    memberId: string,
    patch: UpdateHouseholdMemberRequest,
  ): Promise<HouseholdMemberView> {
    return this.send<HouseholdMemberView>(
      'PATCH',
      `/households/${householdId}/members/${memberId}`,
      patch,
    );
  }

  updateSelf(
    householdId: string,
    patch: UpdateOwnMembershipRequest,
  ): Promise<HouseholdMemberView> {
    return this.send<HouseholdMemberView>(
      'PATCH',
      `/households/${householdId}/members/me`,
      patch,
    );
  }

  async removeMember(householdId: string, memberId: string): Promise<void> {
    await this.send<void>('DELETE', `/households/${householdId}/members/${memberId}`);
  }

  private async send<T>(method: string, path: string, body?: unknown): Promise<T> {
    // Preview auth mints no verifiable token, so the API would answer 401 and the
    // user would read "your session expired" about a session that was never real.
    // Say the true thing instead.
    if (this.config.authMode === 'preview') {
      throw toApiError(
        'unauthenticated',
        'Preview mode signs you in locally but has no API behind it. Set "authMode": "supabase" in config.json and run the API to load a household.',
      );
    }

    const token = this.auth.accessToken();
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (token) {
      // RFC 6750. The API verifies this as a Supabase GoTrue token.
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      // A dead network never produced an ApiErrorResponse, so say so plainly
      // rather than inventing a code the API did not send.
      throw toApiError('internal', 'Could not reach the mealplan API. Check that it is running.');
    }

    if (response.status === 204) {
      return undefined as T;
    }

    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw payload ?? toApiError('internal', `Request failed with status ${response.status}.`);
    }
    return payload as T;
  }
}
