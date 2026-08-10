import { Global, Module } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { SupabaseAuthGuard } from './supabase-auth.guard';
import { SupabaseJwtVerifier } from './supabase-jwt.verifier';

@Global()
@Module({
  providers: [
    SupabaseJwtVerifier,
    // Secure-by-default: every route authenticates unless it carries @Public().
    { provide: APP_GUARD, useClass: SupabaseAuthGuard },
  ],
  exports: [SupabaseJwtVerifier],
})
export class AuthModule {}
