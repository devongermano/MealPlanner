import type { ApiErrorBody, ApiErrorResponse } from '../household/household-api';

/**
 * The single place an API failure becomes text a person reads. Every view routes
 * through it so the rules below are inherited rather than re-litigated per screen.
 *
 * RULE — never say "you do not have access to this household".
 * The API answers 404 for a household you are not a member of, deliberately
 * indistinguishable from one that does not exist, so that a stranger cannot use
 * error codes to discover which household ids are real. Copy that distinguishes
 * "forbidden" from "missing" hands that oracle back. When you cannot tell the
 * difference, say the neutral thing: it could not be found.
 *
 * RULE — switch on `code`, never on `message`. The generated contract calls the
 * message "not a stable contract" in as many words; codes are the contract.
 */

export type ApiErrorCode = ApiErrorBody['code'];

export function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (value === null || typeof value !== 'object') {
    return false;
  }
  const error = (value as { error?: unknown }).error;
  return (
    error !== null &&
    typeof error === 'object' &&
    typeof (error as { code?: unknown }).code === 'string'
  );
}

/**
 * Marks an envelope this app authored rather than received. Those carry copy we
 * already wrote for a person, so describeApiError shows it verbatim instead of
 * substituting the generic line for the code.
 */
export const CLIENT_ORIGIN = 'client';

/** Builds the API's envelope for failures that never reached it, like a dead network. */
export function toApiError(code: ApiErrorCode, message: string): ApiErrorResponse {
  return { error: { code, message }, requestId: CLIENT_ORIGIN };
}

const BY_CODE: Readonly<Record<ApiErrorCode, string>> = {
  unauthenticated: 'Your session has expired. Sign in again to pick up where you left off.',
  forbidden: 'You do not have permission to make that change.',
  // Never "you do not have access" — see the rule above.
  not_found: 'We could not find that. It may have been removed.',
  validation_failed: 'Some of those details need another look.',
  conflict: 'That change conflicts with something else in this household.',
  internal: 'Something went wrong on our end. Try again in a moment.',
};

/**
 * Turn any thrown value into one sentence for the user. Accepts the API envelope,
 * a plain Error, or anything else, because a rendering layer that can itself throw
 * is worse than a generic message.
 */
export function describeApiError(
  cause: unknown,
  fallback = 'That did not save. Try again.',
): string {
  if (isApiErrorResponse(cause)) {
    const { code, message, details } = cause.error;
    // We wrote this one; substituting the generic line for its code would replace
    // an explanation with a guess — "your session expired" about preview mode, say.
    if (cause.requestId === CLIENT_ORIGIN) {
      return message || fallback;
    }
    if (code === 'validation_failed' && details?.length) {
      // Every failing field is reported, not just the first, so show them all.
      return details.map((detail) => detail.message).join(' ');
    }
    // forbidden and conflict carry a specific, actionable server message —
    // "that person is already linked to another member" beats a generic line.
    if (code === 'forbidden' || code === 'conflict') {
      return message || BY_CODE[code];
    }
    return BY_CODE[code] ?? message ?? fallback;
  }
  if (cause instanceof Error && cause.message) {
    return cause.message;
  }
  return fallback;
}
