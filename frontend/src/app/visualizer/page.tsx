'use client';

import { useState, useCallback } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { QueryInput } from './components/query-input';
import { OperationStack } from './components/operation-stack';
import { AnswerTray } from './components/answer-tray';
import { CorpusGrid } from './components/corpus-grid';
import { ChunkTooltip } from './components/chunk-tooltip';
import { GridSettingsCog, type GridFilters } from './components/grid-settings-cog';
import { useVisualizerSocket } from './hooks/use-visualizer-socket';
import { useCorpusManifest } from './hooks/use-corpus-manifest';
import { useDocUrlMap } from './hooks/use-doc-url-map';
import { useHighlightTimeline } from './hooks/use-highlight-timeline';
import { useChunkMetadata } from './hooks/use-chunk-metadata';

const queryClient = new QueryClient();

export default function VisualizerPage() {
  return (
    <ProtectedRoute>
      <QueryClientProvider client={queryClient}>
        <VisualizerShell />
      </QueryClientProvider>
    </ProtectedRoute>
  );
}

function VisualizerShell() {
  const { manifest, loading: manifestLoading } = useCorpusManifest();
  const {
    traceEvents,
    answerText,
    answerComplete,
    resourceItems,
    sendQuery,
    playFixtures,
    playVectorOnly,
    isRunning,
    currentQuery,
    error,
  } = useVisualizerSocket(manifest);
  const highlight = useHighlightTimeline(traceEvents);
  const docUrls = useDocUrlMap(resourceItems);
  const [filters, setFilters] = useState<GridFilters>({
    hideOldWpam: true,
    collapseTinyDocs: true,
    tinyDocThreshold: 3,
    collapseToDocTypes: false,
  });
  const [focusChunkId, setFocusChunkId] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ chunkId: string; x: number; y: number } | null>(null);
  const { getChunkMeta } = useChunkMetadata();

  const handleTileClick = useCallback((chunkId: string, event?: React.MouseEvent) => {
    setFocusChunkId(chunkId);
    if (event) {
      setTooltip({ chunkId, x: event.clientX, y: event.clientY });
    } else {
      setTooltip(null);
    }
  }, []);

  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center justify-between px-6 py-3 border-b border-border/50 shrink-0">
        <span className="text-xs font-medium tracking-widest uppercase text-muted-foreground">
          GraphRAG Visualizer
        </span>
        <GridSettingsCog filters={filters} onChange={setFilters} />
      </header>

      <div className="flex flex-1 min-h-0">
        {/* Left panel: query + operations + answer tray */}
        <div className="w-80 border-r border-border/30 flex flex-col overflow-hidden">
          {/* Query input — overlays with active query after submit */}
          <div className="relative shrink-0 border-b border-border/25">
            <div
              className={`px-5 pt-4 pb-3 ${currentQuery ? 'invisible' : ''}`}
              aria-hidden={!!currentQuery}
            >
              <div className="flex items-stretch rounded-md border border-border/30 overflow-hidden">
                <div className="flex-1 min-w-0">
                  <QueryInput onSubmit={sendQuery} disabled={isRunning} embedded />
                </div>
              </div>
              <div className="flex items-center gap-2 mt-2">
                <button
                  type="button"
                  onClick={playFixtures}
                  disabled={isRunning}
                  className="text-[11px] font-medium text-muted-foreground/60 transition-colors hover:text-foreground/80 disabled:opacity-40"
                >
                  Full trace
                </button>
                <span className="text-muted-foreground/30">|</span>
                <button
                  type="button"
                  onClick={playVectorOnly}
                  disabled={isRunning}
                  className="text-[11px] font-medium text-muted-foreground/60 transition-colors hover:text-foreground/80 disabled:opacity-40"
                >
                  Vector only
                </button>
              </div>
              {error && (
                <p className="text-sm text-destructive mt-2">{error}</p>
              )}
            </div>

            {currentQuery && (
              <div className="absolute inset-0 z-10 flex flex-col justify-center bg-background px-5 py-4">
                <p className="text-sm text-foreground/85 leading-snug line-clamp-4">
                  {currentQuery}
                </p>
                {error && (
                  <p className="text-sm text-destructive mt-2">{error}</p>
                )}
              </div>
            )}
          </div>

          {/* Operation stack — scrollable, takes remaining space */}
          <OperationStack
            operations={highlight.operations}
            onTileClick={handleTileClick}
            answerText={answerText}
            answerComplete={answerComplete}
            docUrls={docUrls}
            resourceItems={resourceItems}
            query={currentQuery}
            done={highlight.done}
            onReplay={highlight.replayOp}
          />

          {/* Answer tray — fixed at bottom */}
          <AnswerTray revealedIds={highlight.revealedIds} done={highlight.done} onTileClick={handleTileClick} />
        </div>

        {/* Right column: canvas */}
        <div className="flex flex-col flex-1 min-w-0">
          <div className="flex-1 relative overflow-hidden">
            {manifestLoading && (
              <p className="absolute inset-0 flex items-center justify-center text-muted-foreground/30 text-sm pointer-events-none">
                Loading corpus...
              </p>
            )}
            {manifest && (
              <CorpusGrid
                manifest={manifest}
                filters={filters}
                revealedIds={highlight.revealedIds}
                activeIds={highlight.activeIds}
                focusChunkId={focusChunkId}
                gridOp={highlight.gridOp}
              />
            )}
          </div>
        </div>
      </div>

      {/* Chunk tooltip */}
      {tooltip && manifest && (
        <ChunkTooltip
          chunkId={tooltip.chunkId}
          docTitle={
            manifest.docs.find((d) =>
              tooltip.chunkId.startsWith(d.docId + '_chunk_')
            )?.title || tooltip.chunkId.replace(/_chunk_\d+$/, '')
          }
          meta={getChunkMeta(tooltip.chunkId)}
          position={{ x: tooltip.x, y: tooltip.y }}
          onClose={() => setTooltip(null)}
        />
      )}
    </div>
  );
}
