import { ValidationPipe, ValidationError } from '@nestjs/common';
import { ApiErrorDetail, ApiException } from './api-error';

/** Flattens nested class-validator errors into `field` / `message` pairs. */
function flatten(errors: ValidationError[], parentPath = ''): ApiErrorDetail[] {
  const out: ApiErrorDetail[] = [];
  for (const error of errors) {
    const path = parentPath ? `${parentPath}.${error.property}` : error.property;
    for (const message of Object.values(error.constraints ?? {})) {
      out.push({ field: path, message });
    }
    if (error.children?.length) {
      out.push(...flatten(error.children, path));
    }
  }
  return out;
}

/**
 * The app-wide validation pipe.
 *
 * `whitelist` + `forbidNonWhitelisted` together are load-bearing, not tidiness:
 * without them a client can post `{"name":"x","role":"planner"}` to an endpoint
 * whose DTO has no `role`, and any later code that spreads the body would
 * silently accept a privilege field it never declared.
 *
 * Every failing field is reported, not first-error-wins — the same rule PRD
 * §8.1 sets for the engine's own writes.
 */
export function buildValidationPipe(): ValidationPipe {
  return new ValidationPipe({
    whitelist: true,
    forbidNonWhitelisted: true,
    transform: true,
    transformOptions: { enableImplicitConversion: false },
    stopAtFirstError: false,
    exceptionFactory: (errors: ValidationError[]) =>
      ApiException.validationFailed(flatten(errors)),
  });
}
