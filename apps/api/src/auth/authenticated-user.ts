import type { Request } from 'express';

/**
 * The verified caller. Built ONLY from claims that survived signature,
 * issuer, audience and expiry verification — never from a header, a query
 * parameter, or an unverified decode.
 */
export interface AuthenticatedUser {
  /** `sub` — the auth.users id. Always a UUID; the verifier rejects anything else. */
  userId: string;
  /** `email`, when the identity has one. Informational: never an authorization input. */
  email: string | null;
  /** `is_anonymous` — true for Supabase anonymous sign-ins. */
  isAnonymous: boolean;
  /** `session_id`, for correlating with GoTrue's session records. */
  sessionId: string | null;
}

/** `req.user` / `req.membership` as this app populates them. */
export interface AuthenticatedRequest extends Request {
  user?: AuthenticatedUser;
  membership?: {
    id: string;
    householdId: string;
    /**
     * Never null in practice: a membership resolved from a token subject can
     * only be a claimed one, because SQL's `user_id = <uuid>` never matches the
     * NULL a placeholder carries. Typed nullable to match the column.
     */
    userId: string | null;
    role: string;
    displayName: string;
    personName: string | null;
  };
}
