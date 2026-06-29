import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

const FINAL_CHUNKS_DIR = path.resolve(
  process.cwd(),
  '../pdf_chunking/chunk_logs/final_chunks'
);

function parseJsonlFile(content: string) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chunks: any[] = [];
  let current = '';
  for (const line of content.split('\n')) {
    current += line + '\n';
    try {
      const obj = JSON.parse(current);
      chunks.push(obj);
      current = '';
    } catch {
      // accumulate more lines
    }
  }
  return chunks;
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ file: string }> }
) {
  try {
    const { file } = await params;
    const filePath = path.join(FINAL_CHUNKS_DIR, decodeURIComponent(file));

    if (!fs.existsSync(filePath)) {
      return NextResponse.json(
        { error: 'File not found' },
        { status: 404 }
      );
    }

    const content = fs.readFileSync(filePath, 'utf-8');
    const rawChunks = parseJsonlFile(content);

    if (rawChunks.length === 0) {
      return NextResponse.json({ document: null, chunks: [] });
    }

    const chunks = rawChunks.map(raw => {
      const meta = raw.metadata || {};
      return {
        chunk_id: raw.chunk_id || '',
        text: raw.text || '',
        char_count: (raw.text || '').length,
        idx: meta.chunk_index ?? 0,
        heading: meta.heading || null,
        subheading: meta.subheading || null,
        start_page: meta.start_page ?? null,
        end_page: meta.end_page ?? null,
        s3_key: meta.source || null,
        statute_refs: meta.statute_refs || [],
        admin_rule_refs: meta.admin_rule_refs || [],
        edition_year: meta.edition_year || null,
      };
    });

    const charCounts = chunks.map(c => c.char_count);
    const firstMeta = rawChunks[0].metadata || {};
    const docId = firstMeta.source_id || firstMeta.doc_id || 'unknown';

    const document = {
      doc_id: docId,
      title: `LOCAL — ${file}`,
      doc_type: 'local',
      framework_id: null,
      authority_level: null,
      source_url: null,
      chunk_count: chunks.length,
      total_chars: charCounts.reduce((a, b) => a + b, 0),
      max_chunk_chars: Math.max(...charCounts),
      min_chunk_chars: Math.min(...charCounts),
    };

    return NextResponse.json({ document, chunks });
  } catch (err) {
    console.error('Failed to read local chunks:', err);
    return NextResponse.json(
      { error: 'Failed to read local chunks' },
      { status: 500 }
    );
  }
}
