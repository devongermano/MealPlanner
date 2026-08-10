import { HttpException, HttpStatus } from '@nestjs/common';
import { ApiProperty } from '@nestjs/swagger';

/**
 * The closed set of machine-readable error codes this API emits. Clients switch
 * on `code`; `message` is for humans and may change without notice.
 */
export const API_ERROR_CODES = [
  /** No credential, or a credential that failed verification. */
  'unauthenticated',
  /** Verified caller, insufficient role for this route. */
  'forbidden',
  /**
   * The resource does not exist — OR the caller is not a member of the
   * household that owns it. The two are deliberately indistinguishable: a 403
   * on a household you are not in confirms that household exists.
   */
  'not_found',
  /** Request body or params failed validation. `details` names the fields. */
  'validation_failed',
  /** The write would violate an invariant (uniqueness, last planner, …). */
  'conflict',
  /** Unhandled server-side failure. Never carries internal detail. */
  'internal',
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

const STATUS_BY_CODE: Record<ApiErrorCode, HttpStatus> = {
  unauthenticated: HttpStatus.UNAUTHORIZED,
  forbidden: HttpStatus.FORBIDDEN,
  not_found: HttpStatus.NOT_FOUND,
  validation_failed: HttpStatus.BAD_REQUEST,
  conflict: HttpStatus.CONFLICT,
  internal: HttpStatus.INTERNAL_SERVER_ERROR,
};

export class ApiErrorDetail {
  @ApiProperty({
    description: 'Dotted path of the offending field.',
    example: 'name',
  })
  field!: string;

  @ApiProperty({ description: 'What is wrong with it.' })
  message!: string;
}

export class ApiErrorBody {
  @ApiProperty({
    enum: API_ERROR_CODES,
    description: 'Machine-readable code. Switch on this.',
  })
  code!: ApiErrorCode;

  @ApiProperty({
    description: 'Human-readable summary. Not a stable contract.',
  })
  message!: string;

  @ApiProperty({
    type: [ApiErrorDetail],
    required: false,
    description:
      'Per-field detail. Present on validation_failed; every failing field is reported, not just the first.',
  })
  details?: ApiErrorDetail[];
}

/** Every non-2xx response from this API has exactly this shape. */
export class ApiErrorResponse {
  @ApiProperty({ type: ApiErrorBody })
  error!: ApiErrorBody;

  @ApiProperty({
    description:
      'Correlates this response with the server log line. Quote it in bug reports.',
    example: '3f1c0b5e-9a1f-4a1d-9a2f-6b0c9c8f0a11',
  })
  requestId!: string;
}

/**
 * The only exception application code should throw. Carries the code; the
 * filter derives the HTTP status and renders the envelope.
 */
export class ApiException extends HttpException {
  readonly code: ApiErrorCode;
  readonly details?: ApiErrorDetail[];

  constructor(code: ApiErrorCode, message: string, details?: ApiErrorDetail[]) {
    super(message, STATUS_BY_CODE[code]);
    this.code = code;
    this.details = details;
  }

  static unauthenticated(message = 'Authentication required.'): ApiException {
    return new ApiException('unauthenticated', message);
  }

  static forbidden(
    message = 'You do not have permission to do that.',
  ): ApiException {
    return new ApiException('forbidden', message);
  }

  /**
   * Also the correct response for "you are not a member of that household".
   * Keep the message identical in both cases — a distinguishable message is an
   * existence oracle even when the status code matches.
   */
  static notFound(message = 'Not found.'): ApiException {
    return new ApiException('not_found', message);
  }

  static conflict(message: string): ApiException {
    return new ApiException('conflict', message);
  }

  static validationFailed(
    details: ApiErrorDetail[],
    message = 'Request validation failed.',
  ): ApiException {
    return new ApiException('validation_failed', message, details);
  }
}

export function httpStatusForCode(code: ApiErrorCode): HttpStatus {
  return STATUS_BY_CODE[code];
}
