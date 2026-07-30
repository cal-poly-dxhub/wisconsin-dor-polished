'use client';

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
  return { manifest: null, loading: false, error: null };
}
