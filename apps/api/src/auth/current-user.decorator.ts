import { ExecutionContext, createParamDecorator } from '@nestjs/common';
import { ApiException } from '../common/api-error';
import type { AuthenticatedRequest, AuthenticatedUser } from './authenticated-user';

/**
 * Injects the verified caller.
 *
 * Throws rather than returning `undefined` when no user is attached: that only
 * happens if a handler is `@Public()` and still asks who is calling, which is a
 * coding error that must not degrade into an unauthenticated request being
 * treated as a user.
 */
export const CurrentUser = createParamDecorator(
  (_data: unknown, context: ExecutionContext): AuthenticatedUser => {
    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    if (!request.user) {
      throw ApiException.unauthenticated();
    }
    return request.user;
  },
);
