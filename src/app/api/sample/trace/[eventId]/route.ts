import { NextResponse } from 'next/server';
import { getSampleTrace } from '@/lib/sample.server';

export async function GET(_req: Request, { params }: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await params;
  const trace = await getSampleTrace(eventId);
  if (!trace) {
    return NextResponse.json(
      { error: 'No trace for that event id in the sample batch.' },
      { status: 404 },
    );
  }
  return NextResponse.json(trace);
}
