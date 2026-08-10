import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { ApiException } from '../common/api-error';
import type { AuthenticatedRequest } from './authenticated-user';
import { IS_PUBLIC_KEY } from './public.decorator';
import { SupabaseJwtVerifier } from './supabase-jwt.verifier';

/**
 * Authenticates every request from the `Authorization: Bearer <jwt>` header.
 *
 * Registered as `APP_GUARD` so the default is authenticated and `@Public()` is
 * the exception. Any route reachable without a token is therefore visible in
 * one `grep -r "@Public"`.
 */
@Injectable()
export class SupabaseAuthGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly verifier: SupabaseJwtVerifier,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    const token = extractBearerToken(request.headers.authorization);
    if (!token) {
      throw ApiException.unauthenticated(
        'Missing bearer token. Send "Authorization: Bearer <access_token>".',
      );
    }

    request.user = await this.verifier.verify(token);
    return true;
  }
}

/**
 * RFC 6750 credential extraction: exactly two whitespace-separated parts, the
 * scheme compared case-insensitively.
 *
 * Rejecting a 3+ part header matters — `Bearer a b` must not be read as the
 * token `a`, or a proxy that appends to the header changes who you are.
 */
export function extractBearerToken(
  header: string | string[] | undefined,
): string | null {
  if (typeof header !== 'string') return null;
  const parts = header.trim().split(/\s+/);
  if (parts.length !== 2) return null;
  if (parts[0].toLowerCase() !== 'bearer') return null;
  return parts[1] || null;
}
