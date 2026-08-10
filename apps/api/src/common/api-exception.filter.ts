import {
  ArgumentsHost,
  Catch,
  ExceptionFilter,
  HttpException,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { randomUUID } from 'node:crypto';
import type { Request, Response } from 'express';
import {
  ApiErrorCode,
  ApiErrorDetail,
  ApiErrorResponse,
  ApiException,
  httpStatusForCode,
} from './api-error';

const CODE_BY_STATUS: Partial<Record<number, ApiErrorCode>> = {
  [HttpStatus.BAD_REQUEST]: 'validation_failed',
  [HttpStatus.UNAUTHORIZED]: 'unauthenticated',
  [HttpStatus.FORBIDDEN]: 'forbidden',
  [HttpStatus.NOT_FOUND]: 'not_found',
  [HttpStatus.CONFLICT]: 'conflict',
};

/**
 * Renders every failure as the one envelope (`ApiErrorResponse`), so a client
 * never has to parse two shapes.
 *
 * The security-relevant half of this filter is the 5xx path: an unrecognised
 * exception's message is logged and NOT returned. Prisma in particular puts
 * SQL, column names, and connection strings in `.message`.
 */
@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger(ApiExceptionFilter.name);

  catch(exception: unknown, host: ArgumentsHost): void {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const request = ctx.getRequest<Request>();
    const requestId = randomUUID();

    const { code, message, details } = this.classify(exception);
    const status = httpStatusForCode(code);

    if (status >= HttpStatus.INTERNAL_SERVER_ERROR) {
      this.logger.error(
        `${requestId} ${request.method} ${request.url} -> ${status}`,
        exception instanceof Error ? exception.stack : String(exception),
      );
    } else {
      this.logger.debug?.(
        `${requestId} ${request.method} ${request.url} -> ${status} ${code}`,
      );
    }

    const body: ApiErrorResponse = {
      error: { code, message, ...(details?.length ? { details } : {}) },
      requestId,
    };
    response.status(status).json(body);
  }

  private classify(exception: unknown): {
    code: ApiErrorCode;
    message: string;
    details?: ApiErrorDetail[];
  } {
    if (exception instanceof ApiException) {
      return {
        code: exception.code,
        message: exception.message,
        details: exception.details,
      };
    }

    if (exception instanceof HttpException) {
      const status = exception.getStatus();
      const code =
        CODE_BY_STATUS[status] ??
        (status >= 500 ? 'internal' : 'validation_failed');
      // A 5xx HttpException still must not echo its message.
      if (code === 'internal') {
        return { code, message: 'Internal server error.' };
      }
      return { code, message: exception.message };
    }

    if (exception instanceof Prisma.PrismaClientKnownRequestError) {
      // P2002 unique violation / P2003 FK violation / P2025 record not found.
      // Messages name tables and columns, so they are summarised, never echoed.
      if (exception.code === 'P2002') {
        return { code: 'conflict', message: 'That value is already taken.' };
      }
      if (exception.code === 'P2025') {
        return { code: 'not_found', message: 'Not found.' };
      }
      this.logger.error(
        `Unmapped Prisma error ${exception.code}`,
        exception.stack,
      );
      return { code: 'internal', message: 'Internal server error.' };
    }

    return { code: 'internal', message: 'Internal server error.' };
  }
}
