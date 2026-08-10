import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { SwaggerModule } from '@nestjs/swagger';
import { AppModule } from './app.module';
import { buildValidationPipe } from './common/validation';
import { loadConfig } from './config';
import { buildOpenApiDocument } from './openapi';

async function bootstrap() {
  // Loaded before the app so a configuration error fails the process with a
  // readable cause instead of a dependency-injection stack trace.
  const config = loadConfig();

  const app = await NestFactory.create(AppModule);
  app.useGlobalPipes(buildValidationPipe());

  if (config.docsEnabled) {
    SwaggerModule.setup('docs', app, buildOpenApiDocument(app));
    Logger.log('Swagger UI on /docs (API_DOCS=1)', 'Bootstrap');
  }

  await app.listen(config.port, config.host);
  Logger.log(
    `Listening on ${config.host}:${config.port} — auth via ${config.auth.jwksUrl ? 'JWKS' : 'shared secret'}`,
    'Bootstrap',
  );
}
void bootstrap();
