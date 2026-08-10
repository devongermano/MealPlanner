import { Module } from '@nestjs/common';
import { APP_FILTER } from '@nestjs/core';
import { AppController } from './app.controller';
import { AuthModule } from './auth/auth.module';
import { ApiExceptionFilter } from './common/api-exception.filter';
import { ConfigModule } from './config.module';
import { HouseholdsModule } from './households/households.module';
import { PrismaModule } from './prisma/prisma.module';

@Module({
  imports: [ConfigModule, PrismaModule, AuthModule, HouseholdsModule],
  controllers: [AppController],
  providers: [
    // Registered here rather than in main.ts so tests that build the module
    // directly get the same error envelope the server produces.
    { provide: APP_FILTER, useClass: ApiExceptionFilter },
  ],
})
export class AppModule {}
