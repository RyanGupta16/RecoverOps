import { NextResponse } from 'next/server';
import { getSampleBatch } from '@/lib/sample.server';

export function GET() {
  return NextResponse.json(getSampleBatch().exceptions);
}
