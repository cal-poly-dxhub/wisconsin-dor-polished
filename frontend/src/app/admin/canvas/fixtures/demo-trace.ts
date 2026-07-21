/**
 * Fixture trace for canvas iteration. vector_search has a full visual;
 * other tool calls will appear as placeholder panes for now.
 */

export interface FixtureTraceEvent {
  kind: 'loop_start' | 'reasoning' | 'tool_call' | 'tool_result' | 'loop_complete' | 'phase';
  turn: number | null;
  seq: number;
  timestamp: number;
  payload: Record<string, unknown>;
}

const BASE = Date.now();
let seq = 0;
const t = (offset: number): number => BASE + offset;

function wpamChunks(count: number): string[] {
  return Array.from({ length: count }, (_, i) => `wpam-wisconsin-property-assessment-manual-2026_chunk_${String(i).padStart(4, '0')}`);
}
function adminChunks(count: number): string[] {
  return Array.from({ length: count }, (_, i) => `admin_rules-tax-12_chunk_${String(i).padStart(4, '0')}`);
}
function statChunks(count: number, start = 0): string[] {
  return Array.from({ length: count }, (_, i) => `statutes-70_chunk_${String(start + i).padStart(4, '0')}`);
}
function govChunks(count: number): string[] {
  return Array.from({ length: count }, (_, i) => `gov_publications-2026-property-owners-guide_chunk_${String(i).padStart(4, '0')}`);
}

export const CORPUS_TOTAL_CHUNKS = 16234;

export const DEMO_QUERY = 'What information is used to determine my assessment?';

export const DEMO_TRACE: FixtureTraceEvent[] = [
  // --- Pre-loop: disambiguation check ---
  {
    kind: 'phase', turn: null, seq: ++seq, timestamp: t(0),
    payload: { phase: 'generality_classified', label: 'Query is specific enough to proceed', result: 'proceed' },
  },

  { kind: 'loop_start', turn: null, seq: ++seq, timestamp: t(30), payload: { maxTurns: 8 } },

  // --- Turn 0: faq_search ---
  {
    kind: 'tool_call', turn: 0, seq: ++seq, timestamp: t(35),
    payload: {
      toolName: 'faq_search',
      summary: `"${DEMO_QUERY}"`,
      status: 'pending',
    },
  },
  {
    kind: 'tool_result', turn: 0, seq: ++seq, timestamp: t(180),
    payload: {
      toolName: 'faq_search',
      status: 'ok',
      summary: 'FAQ semantic match score 0.82',
      metadata: {
        faqCount: 5,
        topScore: 0.82,
        faqScoreThreshold: 0.70,
        faqScores: [0.82, 0.78, 0.74, 0.65, 0.61],
        topFaqSnippet:
          'All real property in Wisconsin must be assessed at its full value, defined as the price it would bring at private sale. The assessor uses the Wisconsin Property Assessment Manual...',
        latencyMs: 145,
      },
    },
  },
  {
    kind: 'phase', turn: 0, seq: ++seq, timestamp: t(200),
    payload: {
      phase: 'faq_transition',
      label: 'FAQ match found, supplementing with graph search',
    },
  },

  // --- Turn 0: seeded initial vector_search (dual-arm) ---
  {
    kind: 'tool_call', turn: 0, seq: ++seq, timestamp: t(220),
    payload: {
      toolName: 'vector_search',
      summary: `"${DEMO_QUERY}"`,
      status: 'pending',
    },
  },
  {
    kind: 'tool_result', turn: 0, seq: ++seq, timestamp: t(700),
    payload: {
      toolName: 'vector_search',
      status: 'ok',
      summary: 'Found 15 chunks across 4 sources',
      docIds: [
        'wpam-wisconsin-property-assessment-manual-2026',
        'admin_rules-tax-12',
        'statutes-70',
        'gov_publications-2026-property-owners-guide',
      ],
      metadata: {
        seeded: true,
        chunkCount: 15,
        broadChunkCount: 7,
        totalChunkCount: 22,
        topK: 15,
        diversityCapPerDoc: 5,
        docCount: 4,
        topScore: 0.8712,
        latencyMs: 420,
        preDedupCount: 60,
        authorityBreakdown: { '2': 3, '4': 3, '5': 5, '7': 4 },
        scoreBuckets: { '0.8-0.9': 3, '0.7-0.8': 7, '<0.7': 5 },
        refinedQuery:
          'Wisconsin property assessment full value market value determination factors assessor',
        broadQuery: DEMO_QUERY,
        broadSkipped: false,
        broadPreDedupCount: 55,
        broadKeptCount: 15,
        broadTopScore: 0.79,
        broadAuthorityBreakdown: { '5': 6, '7': 9 },
        broadScoreBuckets: { '0.8-0.9': 4, '0.7-0.8': 8, '<0.7': 3 },
        broadFullDocChunks: {
          'wpam-wisconsin-property-assessment-manual-2026': 5,
          'gov_publications-2026-property-owners-guide': 4,
          'news-2025-assessment-deadline-reminder': 4,
          'gov_publications-2026-agricultural-assessment-guide': 2,
        },
        broadDiscovery: [
          { docId: 'news-2025-assessment-deadline-reminder', score: 0.74 },
          { docId: 'gov_publications-2026-property-owners-guide', score: 0.71 },
        ],
        broadDocChunks: {
          'news-2025-assessment-deadline-reminder': 4,
          'gov_publications-2026-agricultural-assessment-guide': 3,
        },
        docChunks: {
          'wpam-wisconsin-property-assessment-manual-2026': 5,
          'admin_rules-tax-12': 3,
          'statutes-70': 3,
          'gov_publications-2026-property-owners-guide': 4,
        },
        chunkIds: [
          ...wpamChunks(5),
          ...adminChunks(3),
          ...statChunks(3),
          ...govChunks(4),
        ],
        targetWpamYear: 2026,
        caseLawCount: 1,
        statuteBackfill: [
          { chunkId: 'statutes-70_chunk_0032', docId: 'statutes-70', sourceRank: 1 },
          { chunkId: 'statutes-70_chunk_0996', docId: 'statutes-70', sourceRank: 2 },
        ],
        caselawBackfill: [
          {
            caseId: 'case_law-state-ex-rel-markarian',
            title: 'State ex rel. Markarian v. City of Cudahy',
            citation: '2005 WI 14',
            summary: 'Uniformity and full-value assessment standard.',
          },
        ],
      },
    },
  },

  {
    kind: 'reasoning', turn: 1, seq: ++seq, timestamp: t(2700),
    payload: {
      text: 'The broad search found the current assessment manual and governing statutes. I should inspect their graph relationships for more authoritative sources.',
    },
  },

  // --- Turn 2: get_neighbors (placeholder) ---
  {
    kind: 'tool_call', turn: 2, seq: ++seq, timestamp: t(2800),
    payload: { toolName: 'get_neighbors', summary: 'wpam-wisconsin-property-assessment-manual-2026', status: 'pending' },
  },
  {
    kind: 'tool_result', turn: 2, seq: ++seq, timestamp: t(2900),
    payload: {
      toolName: 'get_neighbors', status: 'ok',
      summary: 'Retrieved 6 related documents from graph',
      metadata: {
        neighborCount: 6,
        latencyMs: 21,
        neighborTitles: [
          'statutes-70',
          'admin_rules-tax-12',
          'admin_rules-tax-18',
          'gov_publications-2026-property-owners-guide',
          'wpam-wisconsin-property-assessment-manual-2025',
          'iaao-standard-mass-appraisal',
        ],
        neighborEdges: [
          { title: 'Chapter 70 — Property Assessment', relationship: 'IMPLEMENTS' },
          { title: 'Tax 12 — Assessor Certification', relationship: 'RELATED_TO' },
          { title: 'Tax 18 — Agricultural Land', relationship: 'RELATED_TO' },
          { title: '2026 Property Owner\'s Guide', relationship: 'RELATED_TO' },
          { title: 'WPAM 2025', relationship: 'DERIVED_FROM' },
          { title: 'IAAO Standard on Mass Appraisal', relationship: 'RELATED_TO' },
        ],
      },
    },
  },

  // --- Turn 3: list_sections ---
  {
    kind: 'tool_call', turn: 3, seq: ++seq, timestamp: t(4200),
    payload: { toolName: 'list_sections', summary: 'wpam-wisconsin-property-assessment-manual-2026', status: 'pending' },
  },
  {
    kind: 'tool_result', turn: 3, seq: ++seq, timestamp: t(4400),
    payload: {
      toolName: 'list_sections', status: 'ok',
      summary: 'Found 18 sections in WPAM 2026',
      metadata: {
        sectionCount: 18,
        docTitle: 'WPAM 2026 — Wisconsin Property Assessment Manual',
        latencyMs: 45,
        sectionHeadings: [
          'Ch. 1 — Introduction & Legal Framework',
          'Ch. 2 — Assessment Administration',
          'Ch. 3 — Property Classification',
          'Ch. 4 — Agricultural Land',
          'Ch. 5 — Personal Property',
          'Ch. 6 — Manufacturing',
          'Ch. 7 — Real Property Listing',
          'Ch. 8 — Market Analysis',
          'Ch. 9 — Valuation',
          'Ch. 10 — Income Approach',
          'Ch. 11 — Cost Approach',
          'Ch. 12 — Sales Comparison',
        ],
      },
    },
  },

  // --- Turn 4: get_section ---
  {
    kind: 'tool_call', turn: 4, seq: ++seq, timestamp: t(5500),
    payload: { toolName: 'get_section', summary: '"Valuation of residential property" from WPAM 2026', status: 'pending' },
  },
  {
    kind: 'tool_result', turn: 3, seq: ++seq, timestamp: t(5700),
    payload: {
      toolName: 'get_section', status: 'ok',
      summary: 'Got "Ch. 9 Valuation" (6 chunks)',
      metadata: {
        chunkCount: 6,
        docTitle: 'WPAM 2026 — Ch. 9 Valuation',
        heading: 'Valuation of residential property',
        query: 'residential property assessment full value market value',
        filtered: true,
        sectionChunkCount: 14,
        returnedChunkCount: 6,
        latencyMs: 312,
        chunkScores: [
          { chunkId: 'c1', cosine: 0.842, zScore: 2.1, heading: 'Three-tier evidence', included: true },
          { chunkId: 'c2', cosine: 0.803, zScore: 1.5, heading: "Arm's-length sale", included: true },
          { chunkId: 'c3', cosine: 0.776, zScore: 1.0, heading: 'Comparable sales', included: true },
          { chunkId: 'c4', cosine: 0.742, zScore: 0.5, heading: 'Cost approach', included: true },
          { chunkId: 'c5', cosine: 0.711, zScore: 0.0, heading: 'Income approach', included: true },
          { chunkId: 'c6', cosine: 0.688, zScore: -0.4, heading: 'Other factors', included: true },
          { chunkId: 'c7', cosine: 0.612, zScore: -1.6, heading: 'Definitions', included: false },
          { chunkId: 'c8', cosine: 0.585, zScore: -2.1, heading: 'Cross-references', included: false },
          { chunkId: 'c9', cosine: 0.551, zScore: -2.7, heading: 'Scope', included: false },
          { chunkId: 'c10', cosine: 0.530, zScore: -3.0, heading: 'Introduction', included: false },
          { chunkId: 'c11', cosine: 0.502, zScore: -3.5, heading: 'Table of contents', included: false },
          { chunkId: 'c12', cosine: 0.488, zScore: -3.7, heading: 'Appendix A', included: false },
          { chunkId: 'c13', cosine: 0.461, zScore: -4.2, heading: 'Appendix B', included: false },
          { chunkId: 'c14', cosine: 0.440, zScore: -4.5, heading: 'Index', included: false },
        ],
      },
    },
  },

  // --- Turn 5: prepare_answer (placeholder) ---
  {
    kind: 'tool_call', turn: 5, seq: ++seq, timestamp: t(8200),
    payload: { toolName: 'prepare_answer', summary: 'with 5 cited sources', status: 'pending' },
  },
  {
    kind: 'loop_complete', turn: 5, seq: ++seq, timestamp: t(8600),
    payload: {
      terminalReason: 'prepare_answer',
      turnsUsed: 5,
      elapsedMs: 8600,
      citedDocCount: 5,
      discoveryTitles: {
        'wpam-wisconsin-property-assessment-manual-2026': 'WPAM 2026 — Wisconsin Property Assessment Manual',
        'statutes-70': 'Chapter 70 — Property Assessment',
        'admin_rules-tax-12': 'Tax 12 — Assessor Certification',
        'gov_publications-2026-property-owners-guide': '2026 Property Owner\'s Guide',
        'admin_rules-tax-18': 'Tax 18 — Agricultural Land',
      },
    },
  },
];
