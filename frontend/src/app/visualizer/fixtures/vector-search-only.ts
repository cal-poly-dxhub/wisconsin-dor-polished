import type { FixtureStep } from './zoning-agricultural-trace';

export const VECTOR_ONLY_QUERY = 'How is agricultural land classified?';

const VECTOR_CHUNK_IDS = [
  'statutes-70_chunk_0000',
  'statutes-70_chunk_0001',
  'statutes-70_chunk_0002',
  'statutes-706_chunk_0000',
  'statutes-706_chunk_0001',
  'admin_rules-document-12_chunk_0000',
  'admin_rules-document-12_chunk_0001',
  'admin_rules-document-15_chunk_0000',
  'admin_rules-document-18_chunk_0000',
  'admin_rules-document-18_chunk_0001',
  'admin_rules-document-19_chunk_0000',
  'admin_rules-document-20_chunk_0000',
  'admin_rules-document-20_chunk_0001',
  'admin_rules-tax-12_chunk_0000',
  'admin_rules-tax-12_chunk_0001',
] as const;

export const VECTOR_SEARCH_ONLY_FIXTURE: FixtureStep[] = [
  {
    delayMs: 150,
    event: {
      kind: 'loop_start',
      turn: null,
      payload: { maxTurns: 8 },
    },
  },
  {
    delayMs: 200,
    event: {
      kind: 'tool_call',
      turn: 1,
      payload: {
        toolName: 'vector_search',
        summary: '"agricultural land classification property assessment"',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 600,
    event: {
      kind: 'tool_result',
      turn: 1,
      payload: {
        toolName: 'vector_search',
        status: 'ok',
        summary: 'Found 15 chunks across 8 sources',
        docIds: [
          'statutes-70',
          'statutes-706',
          'admin_rules-document-12',
          'admin_rules-document-15',
          'admin_rules-document-18',
          'admin_rules-document-19',
          'admin_rules-document-20',
          'admin_rules-tax-12',
        ],
        docTitles: [],
        metadata: {
          chunkCount: 15,
          docCount: 8,
          topScore: 0.8421,
          latencyMs: 600,
          chunkIds: [...VECTOR_CHUNK_IDS],
        },
      },
    },
  },
  // No more events — hangs here to let you inspect the Q node + edges
];
