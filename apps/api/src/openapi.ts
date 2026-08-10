import type { INestApplication } from '@nestjs/common';
import { DocumentBuilder, OpenAPIObject, SwaggerModule } from '@nestjs/swagger';

/**
 * Builds the OpenAPI document for this API.
 *
 * ONE builder, two consumers: the optional `/docs` UI and
 * `scripts/dump-openapi.ts`, whose output is codegen'd into
 * `packages/contracts-api`. If they could drift, the docs a human reads and the
 * types the web app compiles against would stop being the same API.
 *
 * The document must be byte-stable across runs — the drift gate diffs it — so
 * nothing here may include a timestamp, a hostname, or anything else that
 * varies per invocation. `version` is pinned deliberately rather than read from
 * package.json, so a version bump is an explicit contract change.
 */
export function buildOpenApiDocument(app: INestApplication): OpenAPIObject {
  const config = new DocumentBuilder()
    .setTitle('mealplan API')
    .setDescription(
      'Accounts, households, and roles. Authorization lives here (ARCHITECTURE.md, "one brain"); ' +
        'the database RLS policies are a containment boundary, not business logic.',
    )
    .setVersion('mealplan/v2')
    .addBearerAuth(
      {
        type: 'http',
        scheme: 'bearer',
        bearerFormat: 'JWT',
        description: 'Supabase Auth access token (GoTrue). Sent as "Authorization: Bearer <token>".',
      },
      'bearer',
    )
    .build();

  return SwaggerModule.createDocument(app, config);
}
