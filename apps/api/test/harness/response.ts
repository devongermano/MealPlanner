import type { Response } from 'supertest';
import type {
  ApiErrorBody,
  ApiErrorResponse,
} from '../../src/common/api-error';

/**
 * Typed accessors for supertest response bodies.
 *
 * `Response.body` is `any`, so every `response.body.error.code` in a test is an
 * unchecked property chain that keeps compiling after the shape changes. These
 * helpers are the ONE place that `any` is narrowed, so tests that read a body
 * get the same types the generated contract publishes — and a DTO change breaks
 * the tests at compile time instead of at assertion time.
 */
export function bodyOf<T>(response: Response): T {
  return response.body as T;
}

/** The error envelope every non-2xx response carries. */
export function errorOf(response: Response): ApiErrorBody {
  return bodyOf<ApiErrorResponse>(response).error;
}

/** The correlation id every response carries. */
export function requestIdOf(response: Response): string {
  return bodyOf<ApiErrorResponse>(response).requestId;
}
