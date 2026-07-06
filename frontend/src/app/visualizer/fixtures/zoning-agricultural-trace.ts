import type { TraceEvent } from '../hooks/use-visualizer-socket';

/** Fixture based on query 77c2bab3-ec88-45ee-873b-3b9d823c773b (2026-07-05). */
export const FIXTURE_QUERY =
  'Is zoning considered for agricultural classification?';
export const FIXTURE_QUERY_ID = '77c2bab3-ec88-45ee-873b-3b9d823c773b';

/** Recorded answer for fixture playback (query 77c2bab3, 2026-07-05). */
export const FIXTURE_ANSWER = `# Zoning and Agricultural Classification in Wisconsin

Zoning is **not** a factor in determining whether land qualifies for agricultural classification in Wisconsin. The determination is based solely on actual use.

## The Core Rule: Actual Use Controls

Under § 70.32, classification of real property for tax purposes is based on the actual use of the property.`;

/** Turn gap is handled manually in the fixture steps (set to 0 so it doesn't interfere). */
export const FIXTURE_TURN_GAP_MS = 0;

const VECTOR_SEARCH_1_CHUNK_IDS = [
  'statutes-70_chunk_0000',
  'statutes-70_chunk_0001',
  'statutes-706_chunk_0000',
  'statutes-706_chunk_0001',
  'statutes-706-document_chunk_0000',
  'statutes-706-document_chunk_0001',
  'admin_rules-document-12_chunk_0000',
  'admin_rules-document-12_chunk_0001',
  'admin_rules-document-15_chunk_0000',
  'admin_rules-document-15_chunk_0001',
  'admin_rules-document-18_chunk_0000',
  'admin_rules-document-18_chunk_0001',
  'admin_rules-document-19_chunk_0000',
  'admin_rules-document-19_chunk_0001',
  'admin_rules-document-20_chunk_0000',
] as const;

const VECTOR_SEARCH_2_CHUNK_IDS = [
  'admin_rules-document-15_chunk_0002',
  'admin_rules-document-15_chunk_0003',
  'admin_rules-document-18_chunk_0002',
  'admin_rules-document-18_chunk_0003',
  'admin_rules-document-19_chunk_0002',
  'admin_rules-document-19_chunk_0003',
  'admin_rules-document-20_chunk_0002',
  'admin_rules-document-20_chunk_0003',
  'admin_rules-document-6_chunk_0002',
  'admin_rules-document-6_chunk_0003',
  'admin_rules-tax-12_chunk_0002',
  'admin_rules-tax-12_chunk_0003',
  'admin_rules-tax-15_chunk_0002',
  'admin_rules-tax-15_chunk_0003',
  'admin_rules-tax-16_chunk_0002',
] as const;

const SEARCH_DOC_TURN_2_CHUNK_IDS = ['statutes-70_chunk_0050'] as const;

const SEARCH_DOC_TURN_3_CHUNK_IDS = [
  'statutes-70_chunk_0120',
  'statutes-70_chunk_0121',
  'statutes-70_chunk_0122',
  'statutes-70_chunk_0123',
] as const;

const NEIGHBOR_DOC_IDS = [
  'admin_rules-document-12',
  'admin_rules-document-20',
  'admin_rules-tax-12',
  'admin_rules-document-6',
  'admin_rules-document-19',
  'admin_rules-tax-16',
  'admin_rules-tax-20',
  'admin_rules-document-15',
  'admin_rules-tax-18',
] as const;

const CITED_DOC_IDS = [
  'statutes-70',
  'admin_rules-document-18',
  'admin_rules-document-12',
  'admin_rules-document-20',
  'admin_rules-tax-12',
  'admin_rules-document-6',
  'admin_rules-document-19',
  'admin_rules-tax-16',
] as const;

export interface FixtureStep {
  /** Milliseconds to wait before emitting this event (relative to the previous one). */
  delayMs: number;
  event: Omit<TraceEvent, 'seq' | 'timestamp'>;
}

// Phase timing: 4 seconds between each major phase
const PHASE_GAP = 4000;

export const ZONING_AGRICULTURAL_FIXTURE: FixtureStep[] = [
  // ─── Phase 0: loop start ───
  {
    delayMs: 150,
    event: {
      kind: 'loop_start',
      turn: null,
      payload: { maxTurns: 8 },
    },
  },

  // ─── Phase 1: Two parallel vector searches ───
  {
    delayMs: 200,
    event: {
      kind: 'tool_call',
      turn: 1,
      payload: {
        toolName: 'vector_search',
        summary: '"zoning agricultural classification Wisconsin property assessment"',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 80,
    event: {
      kind: 'tool_call',
      turn: 1,
      payload: {
        toolName: 'vector_search',
        summary: '"agricultural classification compatible use January 1 ordinance easement"',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 500,
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
          'statutes-706-document',
          'admin_rules-document-12',
          'admin_rules-document-15',
          'admin_rules-document-18',
          'admin_rules-document-19',
          'admin_rules-document-20',
        ],
        docTitles: [],
        metadata: {
          chunkCount: 15,
          docCount: 8,
          topScore: 0.8421,
          latencyMs: 547,
          chunkIds: [...VECTOR_SEARCH_1_CHUNK_IDS],
        },
      },
    },
  },
  {
    delayMs: 200,
    event: {
      kind: 'tool_result',
      turn: 1,
      payload: {
        toolName: 'vector_search',
        status: 'ok',
        summary: 'Found 15 chunks across 7 sources',
        docIds: [
          'admin_rules-document-15',
          'admin_rules-document-18',
          'admin_rules-document-19',
          'admin_rules-document-20',
          'admin_rules-document-6',
          'admin_rules-tax-12',
          'admin_rules-tax-15',
        ],
        docTitles: [],
        metadata: {
          chunkCount: 15,
          docCount: 7,
          topScore: 0.8104,
          latencyMs: 291,
          chunkIds: [...VECTOR_SEARCH_2_CHUNK_IDS],
        },
      },
    },
  },

  // ─── Phase 2: Search document (4s gap) ───
  {
    delayMs: PHASE_GAP,
    event: {
      kind: 'tool_call',
      turn: 2,
      payload: {
        toolName: 'search_document',
        summary: '"agricultural classification devoted primarily to agricultural use zoning" in statutes-70',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 300,
    event: {
      kind: 'tool_result',
      turn: 2,
      payload: {
        toolName: 'search_document',
        status: 'ok',
        summary: 'Searched statutes-70',
        docIds: ['statutes-70'],
        docTitles: ['Chapter 70 — Property Assessment'],
        metadata: {
          chunkCount: 1,
          docId: 'statutes-70',
          docTitle: 'Chapter 70 — Property Assessment',
          latencyMs: 249,
          chunkIds: [...SEARCH_DOC_TURN_2_CHUNK_IDS],
        },
      },
    },
  },

  // ─── Phase 3: Get neighbors (4s gap) ───
  {
    delayMs: PHASE_GAP,
    event: {
      kind: 'tool_call',
      turn: 3,
      payload: {
        toolName: 'get_neighbors',
        summary: 'admin_rules-document-18',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 200,
    event: {
      kind: 'tool_result',
      turn: 3,
      payload: {
        toolName: 'get_neighbors',
        status: 'ok',
        summary: 'Retrieved 9 related documents from graph',
        docIds: [...NEIGHBOR_DOC_IDS],
        docTitles: [],
        metadata: {
          neighborCount: 9,
          latencyMs: 21,
          relationshipCounts: { RELATED_TO: 5, SUPPLEMENTS: 4 },
        },
      },
    },
  },

  // ─── Phase 4: Second search document (4s gap) ───
  {
    delayMs: PHASE_GAP,
    event: {
      kind: 'tool_call',
      turn: 4,
      payload: {
        toolName: 'search_document',
        summary: '"sec 70.32 agricultural land classification devoted primarily agricultural use definition" in statutes-70',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 300,
    event: {
      kind: 'tool_result',
      turn: 4,
      payload: {
        toolName: 'search_document',
        status: 'ok',
        summary: 'Searched statutes-70',
        docIds: ['statutes-70'],
        docTitles: ['Chapter 70 — Property Assessment'],
        metadata: {
          chunkCount: 4,
          docId: 'statutes-70',
          docTitle: 'Chapter 70 — Property Assessment',
          latencyMs: 279,
          chunkIds: [...SEARCH_DOC_TURN_3_CHUNK_IDS],
        },
      },
    },
  },

  // ─── Phase 5: Prepare answer (4s gap) ───
  {
    delayMs: PHASE_GAP,
    event: {
      kind: 'tool_call',
      turn: 5,
      payload: {
        toolName: 'prepare_answer',
        summary: 'with 8 cited sources',
        status: 'pending',
      },
    },
  },
  {
    delayMs: 400,
    event: {
      kind: 'loop_complete',
      turn: 5,
      payload: {
        terminalReason: 'prepare_answer',
        turnsUsed: 5,
        elapsedMs: 19600,
        citedDocCount: 8,
        discoveryCounts: {
          'vector-search': 9,
          'graph-neighbor': 39,
        },
        discoveryTitles: Object.fromEntries(
          CITED_DOC_IDS.map((id) => [id, id])
        ),
      },
    },
  },
];
