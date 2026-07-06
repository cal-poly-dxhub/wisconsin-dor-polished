import { NextResponse } from 'next/server';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';

const BUCKET = process.env.WORK_BUCKET_NAME || 'wis-work-bucket-c8e69250';
const REGION = process.env.AWS_REGION || 'us-east-1';
const PREFIX = 'visualizer/';

const s3 = new S3Client({ region: REGION });

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const file = searchParams.get('file');

  const allowed = ['grid-manifest.json', 'grid-metadata.json'];
  if (!file || !allowed.includes(file)) {
    return NextResponse.json({ error: 'Invalid file parameter' }, { status: 400 });
  }

  try {
    const command = new GetObjectCommand({
      Bucket: BUCKET,
      Key: `${PREFIX}${file}`,
    });

    const response = await s3.send(command);
    const body = await response.Body?.transformToByteArray();

    if (!body) {
      return NextResponse.json({ error: 'Empty response from S3' }, { status: 502 });
    }

    return new NextResponse(body, {
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
      },
    });
  } catch (err) {
    console.error('[API/visualizer] S3 fetch failed:', err);
    return NextResponse.json({ error: 'Failed to fetch from S3' }, { status: 502 });
  }
}
