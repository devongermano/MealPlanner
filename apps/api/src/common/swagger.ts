import { ParseUUIDPipe, applyDecorators } from '@nestjs/common';
import { ApiResponse } from '@nestjs/swagger';
import { ApiErrorResponse, ApiException } from './api-error';

/**
 * The error responses every authenticated route can produce, declared once so
 * the generated client sees the same envelope everywhere instead of a
 * per-route guess.
 */
export function ApiAuthenticatedErrors(): MethodDecorator & ClassDecorator {
  return applyDecorators(
    ApiResponse({
      status: 400,
      description: 'Validation failed.',
      type: ApiErrorResponse,
    }),
    ApiResponse({
      status: 401,
      description: 'Missing or invalid access token.',
      type: ApiErrorResponse,
    }),
    ApiResponse({
      status: 500,
      description: 'Internal error.',
      type: ApiErrorResponse,
    }),
  );
}

/**
 * Adds the household-scoped failures. The 404 description is the contract for
 * the isolation posture: non-membership and non-existence are the same answer.
 */
export function ApiHouseholdScopedErrors(): MethodDecorator & ClassDecorator {
  return applyDecorators(
    ApiAuthenticatedErrors(),
    ApiResponse({
      status: 403,
      description:
        'You are a member, but your role is below what this route requires.',
      type: ApiErrorResponse,
    }),
    ApiResponse({
      status: 404,
      description:
        'No such household, OR you are not a member of it. Deliberately indistinguishable.',
      type: ApiErrorResponse,
    }),
  );
}

/** `ParseUUIDPipe` that fails into this API's envelope rather than Nest's default. */
export function uuidParam(field: string): ParseUUIDPipe {
  return new ParseUUIDPipe({
    exceptionFactory: () =>
      ApiException.validationFailed([{ field, message: 'must be a UUID' }]),
  });
}
