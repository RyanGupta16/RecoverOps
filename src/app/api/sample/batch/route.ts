import { NextResponse } from 'next/server';
import { getSampleBatch } from '@/lib/sample.server';

/** Demo-mode fallback for the console. Keeps the 800 KB batch out of client JS. */
export function GET() {
  return NextResponse.json(getSampleBatch());
}
