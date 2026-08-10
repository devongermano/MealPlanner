import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { API_CONFIG, loadConfig } from './config';

@Module({
  imports: [],
  controllers: [AppController],
  providers: [{ provide: API_CONFIG, useFactory: () => loadConfig() }],
})
export class AppModule {}
