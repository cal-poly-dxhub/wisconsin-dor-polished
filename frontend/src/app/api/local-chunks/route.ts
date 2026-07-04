import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const FINAL_CHUNKS_DIR = path.resolve(
  process.cwd(),
  '../pdf_chunking/chunk_logs/final_chunks'
);

export async function GET() {
  try {
    if (!fs.existsSync(FINAL_CHUNKS_DIR)) {
      return NextResponse.json({ documents: [] });
    }

    const files = fs
      .readdirSync(FINAL_CHUNKS_DIR)
      .filter(f => f.endsWith('.jsonl'))
      .sort()
      .reverse();

    const documents = files.map(file => {
      const stat = fs.statSync(path.join(FINAL_CHUNKS_DIR, file));
      return {
        doc_id: file,
        last_modified: stat.mtime.toISOString(),
        size_bytes: stat.size,
      };
    });

    return NextResponse.json({ documents });
  } catch (err) {
    console.error('Failed to list local chunks:', err);
    return NextResponse.json(
      { error: 'Failed to list local chunks' },
      { status: 500 }
    );
  }
}
