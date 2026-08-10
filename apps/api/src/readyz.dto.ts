import { ApiProperty } from '@nestjs/swagger';

export class ReadyzResponse {
  @ApiProperty({ description: 'True when every dependency below is healthy.' })
  ok!: boolean;

  @ApiProperty({ description: 'Database reachable (SELECT 1 succeeded).' })
  database!: boolean;

  @ApiProperty({
    enum: ['jwks', 'shared-secret'],
    description:
      'How access tokens are verified. "jwks" is the production posture — this service holds no signing material.',
  })
  authMode!: 'jwks' | 'shared-secret';
}
