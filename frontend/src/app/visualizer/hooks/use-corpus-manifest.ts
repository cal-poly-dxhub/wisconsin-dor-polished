'use client';

import { useState, useEffect } from 'react';

export interface RawTile {
  id: string;
  docId: string;
  auth: number;
  idx: number;
}

export interface DocInfo {
  docId: string;
  title: string;
  auth: number;
  chunkCount: number;
  isOldWpam: boolean;
}

export interface GridManifest {
  totalChunks: number;
  totalDocs: number;
  tileSize: number;
  tileGap: number;
  tiles: RawTile[];
  docs: DocInfo[];
}

interface UseCorpusManifestReturn {
  manifest: GridManifest | null;
  loading: boolean;
  error: string | null;
}

export function useCorpusManifest(): UseCorpusManifestReturn {
  const [manifest, setManifest] = useState<GridManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch('/api/visualizer?file=grid-manifest.json')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: GridManifest) => {
        if (!cancelled) {
          setManifest(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load manifest');
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, []);

  return { manifest, loading, error };
}
