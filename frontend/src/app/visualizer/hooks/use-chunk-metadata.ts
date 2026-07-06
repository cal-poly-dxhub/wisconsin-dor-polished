'use client';

import { useState, useEffect, useCallback } from 'react';

export interface ChunkMeta {
  sp?: number; // startPage
  ep?: number; // endPage
  h?: string;  // heading
  sh?: string; // subheading
}

type MetadataMap = Record<string, ChunkMeta>;

interface UseChunkMetadataReturn {
  getChunkMeta: (chunkId: string) => ChunkMeta | null;
  loaded: boolean;
}

let cachedMetadata: MetadataMap | null = null;
let fetchPromise: Promise<MetadataMap> | null = null;

function fetchMetadata(): Promise<MetadataMap> {
  if (cachedMetadata) return Promise.resolve(cachedMetadata);
  if (fetchPromise) return fetchPromise;

  fetchPromise = fetch('/api/visualizer?file=grid-metadata.json')
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((data: MetadataMap) => {
      cachedMetadata = data;
      return data;
    })
    .catch((err) => {
      console.warn('[Visualizer] Failed to load chunk metadata:', err);
      cachedMetadata = {};
      return {};
    });

  return fetchPromise;
}

export function useChunkMetadata(): UseChunkMetadataReturn {
  const [, forceRender] = useState(0);

  useEffect(() => {
    if (cachedMetadata) return;
    let active = true;
    fetchMetadata().then(() => {
      if (active) forceRender((n) => n + 1);
    });
    return () => { active = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const getChunkMeta = useCallback((chunkId: string): ChunkMeta | null => {
    if (!cachedMetadata) return null;
    return cachedMetadata[chunkId] || null;
  }, []);

  return { getChunkMeta, loaded: !!cachedMetadata };
}
