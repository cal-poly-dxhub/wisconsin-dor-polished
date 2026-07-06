'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Application, Graphics, Container, Text, TextStyle } from 'pixi.js';
import type { GridManifest, RawTile, DocInfo } from '../hooks/use-corpus-manifest';

/* ─── Props ─── */

interface CorpusGridProps {
  manifest: GridManifest;
  filters: {
    hideOldWpam: boolean;
    collapseTinyDocs: boolean;
    tinyDocThreshold: number;
    collapseToDocTypes: boolean;
  };
  revealedIds: Set<string>;
  activeIds: Set<string>;
  focusChunkId: string | null;
  gridOp: import('../hooks/use-highlight-timeline').GridOp | null;
}

/* ─── Layout constants ─── */

const TILE_SIZE = 5;
const TILE_GAP = 1;
const CELL = TILE_SIZE + TILE_GAP; // 6
const DOC_GAP = 4;
const BAND_GAP = 14;
const BAND_LABEL_HEIGHT = 14;
const DOC_LABEL_HEIGHT = 10;
const MAX_ROW_WIDTH = 1800;

const SCALE_INITIAL = 1;
const SCALE_MIN = 0.5;
const SCALE_MAX = 4;
const WHEEL_ZOOM_SENSITIVITY = 0.015;
const WHEEL_DELTA_CLAMP = 200;
const TEXT_SUPERSAMPLE = SCALE_MAX;

/** Convert wheel delta to a multiplicative zoom factor (cursor-anchored zoom). */
function wheelZoomFactor(e: WheelEvent, viewportHeight: number): number {
  let delta = e.deltaY;
  if (e.deltaMode === WheelEvent.DOM_DELTA_LINE) {
    delta *= 16;
  } else if (e.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
    delta *= viewportHeight;
  }
  // Pinch-to-zoom on trackpads is delivered as ctrl+wheel with large deltas.
  if (e.ctrlKey) {
    delta *= 0.3;
  }
  delta = Math.max(-WHEEL_DELTA_CLAMP, Math.min(WHEEL_DELTA_CLAMP, delta));
  return Math.exp(-delta * WHEEL_ZOOM_SENSITIVITY);
}

// Monochrome palette
const TILE_COLOR_IDLE = 0x3a3a3a;
const TILE_COLOR_IDLE_ALPHA = 0.6;
const BAND_LABEL_COLOR = '#666666';
const DOC_LABEL_COLOR = '#555555';

const AUTHORITY_NAMES: Record<number, string> = {
  1: 'Constitution',
  2: 'Statutes',
  3: 'Case Law',
  4: 'Admin Rules',
  5: 'WPAM',
  6: 'FAQs',
  7: 'Gov Pubs',
  8: 'IAAO',
  9: 'USPAP',
};

/* ─── Abbreviation ─── */

const ABBREVIATIONS: [RegExp, string][] = [
  [/Wisconsin Property Assessment Manual/gi, 'WPAM'],
  [/Wisconsin Administrative Code/gi, 'WAC'],
  [/Wisconsin Statutes?/gi, 'Wis. Stat.'],
  [/Wisconsin Department of Revenue/gi, 'WI DOR'],
  [/Wisconsin/gi, 'WI'],
  [/Property Assessment/gi, 'Prop. Assess.'],
  [/International Association of Assessing Officers/gi, 'IAAO'],
  [/Uniform Standards of Professional Appraisal Practice/gi, 'USPAP'],
  [/Manufacturing/gi, 'Mfg.'],
  [/Assessment/gi, 'Assess.'],
  [/Chapter/gi, 'Ch.'],
  [/Section/gi, 'Sec.'],
  [/Personal Property/gi, 'Pers. Prop.'],
  [/Real Property/gi, 'Real Prop.'],
  [/Department of Revenue/gi, 'DOR'],
  [/Administrative/gi, 'Admin.'],
  [/Standard on/gi, 'Std.'],
  [/Procedures?/gi, 'Proc.'],
  [/Guidelines?/gi, 'Guide.'],
  [/Valuation/gi, 'Val.'],
  [/Property/gi, 'Prop.'],
  [/Municipal/gi, 'Muni.'],
];

function abbreviate(title: string, maxChars: number): string {
  let s = title;
  for (const [pattern, replacement] of ABBREVIATIONS) {
    if (s.length <= maxChars) break;
    s = s.replace(pattern, replacement);
  }
  if (s.length > maxChars) {
    s = s.slice(0, maxChars - 1) + '…';
  }
  return s;
}

/* ─── Layout computation ─── */

interface LayoutTile {
  x: number;
  y: number;
}

interface LayoutDoc {
  docId: string;
  title: string;
  x: number;
  y: number;
  cols: number;
  rows: number;
}

interface LayoutBand {
  label: string;
  y: number;
}

interface ComputedLayout {
  tiles: LayoutTile[];
  docs: LayoutDoc[];
  bands: LayoutBand[];
  tileIdToPos: Map<string, LayoutTile>;
  worldWidth: number;
  worldHeight: number;
}

interface DocGroup {
  docId: string;
  title: string;
  auth: number;
  tileIndices: number[]; // indices into the filtered tile array
}

function computeLayout(
  manifest: GridManifest,
  filters: CorpusGridProps['filters']
): ComputedLayout {
  const { tiles, docs } = manifest;

  // Build doc lookup
  const docMap = new Map<string, DocInfo>();
  for (const d of docs) docMap.set(d.docId, d);

  // Filter tiles and group by auth then doc
  const bandGroups = new Map<number, DocGroup[]>();

  // Gather docs per auth band
  const docGroupMap = new Map<string, DocGroup>();

  // Determine which tiles to include
  const filteredTiles: RawTile[] = [];
  for (const tile of tiles) {
    const doc = docMap.get(tile.docId);
    if (!doc) continue;
    if (filters.hideOldWpam && doc.isOldWpam) continue;
    filteredTiles.push(tile);
  }

  // Group filtered tiles by doc
  for (let i = 0; i < filteredTiles.length; i++) {
    const tile = filteredTiles[i];
    let group = docGroupMap.get(tile.docId);
    if (!group) {
      const doc = docMap.get(tile.docId)!;
      group = {
        docId: tile.docId,
        title: doc.title,
        auth: tile.auth,
        tileIndices: [],
      };
      docGroupMap.set(tile.docId, group);
    }
    group.tileIndices.push(i);
  }

  // Organize into bands
  for (const group of docGroupMap.values()) {
    let bandList = bandGroups.get(group.auth);
    if (!bandList) {
      bandList = [];
      bandGroups.set(group.auth, bandList);
    }
    bandList.push(group);
  }

  // Collapse to doc types: merge all docs per authority, split into ~3 columns
  if (filters.collapseToDocTypes) {
    const TARGET_COLS = 3;
    for (const [auth, groups] of bandGroups.entries()) {
      const bandName = AUTHORITY_NAMES[auth] || `Level ${auth}`;
      const allIndices: number[] = [];
      for (const g of groups) {
        allIndices.push(...g.tileIndices);
      }
      // Split into TARGET_COLS roughly-equal sub-groups
      const chunkSize = Math.ceil(allIndices.length / TARGET_COLS);
      const subGroups: DocGroup[] = [];
      for (let i = 0; i < TARGET_COLS; i++) {
        const slice = allIndices.slice(i * chunkSize, (i + 1) * chunkSize);
        if (slice.length === 0) break;
        subGroups.push({
          docId: `_type_${auth}_${i}`,
          title: i === 0 ? bandName : '',
          auth,
          tileIndices: slice,
        });
      }
      bandGroups.set(auth, subGroups);
    }
  }

  // Collapse tiny docs if filter is on
  if (filters.collapseTinyDocs && !filters.collapseToDocTypes) {
    for (const [auth, groups] of bandGroups.entries()) {
      const big: DocGroup[] = [];
      const tinyIndices: number[] = [];
      let tinyDocCount = 0;

      for (const g of groups) {
        if (g.tileIndices.length <= filters.tinyDocThreshold) {
          tinyIndices.push(...g.tileIndices);
          tinyDocCount++;
        } else {
          big.push(g);
        }
      }

      if (tinyIndices.length > 0) {
        const bandName = AUTHORITY_NAMES[auth] || `Level ${auth}`;
        big.push({
          docId: `_other_${auth}`,
          title: `Other ${bandName} (${tinyDocCount} docs)`,
          auth,
          tileIndices: tinyIndices,
        });
      }

      bandGroups.set(auth, big);
    }
  }

  // Sort bands by authority level
  const sortedAuths = Array.from(bandGroups.keys()).sort((a, b) => a - b);

  // Now compute positions
  const layoutTiles: LayoutTile[] = new Array(filteredTiles.length);
  const layoutDocs: LayoutDoc[] = [];
  const layoutBands: LayoutBand[] = [];

  let cursorY = 0;
  let maxWidth = 0;

  for (const auth of sortedAuths) {
    const bandName = AUTHORITY_NAMES[auth] || `Level ${auth}`;
    layoutBands.push({ label: bandName, y: cursorY });
    cursorY += BAND_LABEL_HEIGHT;

    const groups = bandGroups.get(auth)!;

    // Pack doc rectangles left-to-right within band, wrapping at MAX_ROW_WIDTH
    let rowX = 0;
    let rowMaxHeight = 0;

    for (const group of groups) {
      const n = group.tileIndices.length;
      const cols = Math.max(1, Math.min(Math.floor(Math.sqrt(n) * 1.8), 55));
      const rows = Math.ceil(n / cols);

      const docPixelWidth = cols * CELL;
      const docPixelHeight = rows * CELL + DOC_LABEL_HEIGHT;

      // Wrap to next row if exceeds max width
      if (rowX > 0 && rowX + docPixelWidth > MAX_ROW_WIDTH) {
        cursorY += rowMaxHeight + DOC_GAP;
        rowX = 0;
        rowMaxHeight = 0;
      }

      const docX = rowX;
      const docY = cursorY;

      layoutDocs.push({
        docId: group.docId,
        title: group.title,
        x: docX,
        y: docY,
        cols,
        rows,
      });

      // Lay out individual tiles within this doc rect
      const tileStartY = docY + DOC_LABEL_HEIGHT;
      for (let i = 0; i < group.tileIndices.length; i++) {
        const tileIdx = group.tileIndices[i];
        const col = i % cols;
        const row = Math.floor(i / cols);
        layoutTiles[tileIdx] = {
          x: docX + col * CELL,
          y: tileStartY + row * CELL,
        };
      }

      rowX += docPixelWidth + DOC_GAP;
      rowMaxHeight = Math.max(rowMaxHeight, docPixelHeight);
      maxWidth = Math.max(maxWidth, rowX);
    }

    cursorY += rowMaxHeight + BAND_GAP;
  }

  // Build ID → position lookup for highlight mapping
  const tileIdToPos = new Map<string, LayoutTile>();
  for (let i = 0; i < filteredTiles.length; i++) {
    const pos = layoutTiles[i];
    if (pos) {
      tileIdToPos.set(filteredTiles[i].id, pos);
    }
  }

  return {
    tiles: layoutTiles,
    docs: layoutDocs,
    bands: layoutBands,
    tileIdToPos,
    worldWidth: maxWidth,
    worldHeight: cursorY,
  };
}

/* ─── Search Document overlay ─── */

function drawSearchDocumentOverlay(
  overlay: Container,
  gridOp: import('../hooks/use-highlight-timeline').GridOp,
  layout: ComputedLayout,
  el: HTMLDivElement,
  world: Container,
  collapseToDocTypes: boolean,
): (() => void) | undefined {
  // Collect chunk IDs from the vectorCalls (reused for search_document chunkIds)
  // For search_document, chunkIds are stored in vectorCalls[0].chunkIds (piggybacks the structure)
  const chunkIds = gridOp.vectorCalls.length > 0 ? gridOp.vectorCalls[0].chunkIds : [];
  if (chunkIds.length === 0) return;

  // Dim layer — covers all tiles
  const dimGfx = new Graphics();
  dimGfx.rect(0, 0, layout.worldWidth, layout.worldHeight);
  dimGfx.fill({ color: 0x000000, alpha: 0.6 });
  overlay.addChild(dimGfx);

  // Find bounding box of result chunks
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const id of chunkIds) {
    const pos = layout.tileIdToPos.get(id);
    if (pos) {
      minX = Math.min(minX, pos.x);
      minY = Math.min(minY, pos.y);
      maxX = Math.max(maxX, pos.x + TILE_SIZE);
      maxY = Math.max(maxY, pos.y + TILE_SIZE);
    }
  }
  if (minX === Infinity) return;

  if (collapseToDocTypes) {
    // Doc label above the cluster — only when collapsed (otherwise labels already in the grid)
    const labelText = gridOp.vectorCalls.length > 0 ? gridOp.vectorCalls[0].label : '';
    if (labelText) {
      const labelStyle = new TextStyle({
        fontSize: 8 * TEXT_SUPERSAMPLE,
        fill: '#ffffff',
        fontFamily: 'system-ui, sans-serif',
        fontWeight: '500',
      });
      const label = new Text({ text: labelText, style: labelStyle });
      label.scale.set(1 / TEXT_SUPERSAMPLE);
      label.x = minX;
      label.y = minY - 12;
      overlay.addChild(label);
    }
  } else {
    // In normal view, redraw matching doc labels in white above the dim layer
    for (const doc of layout.docs) {
      const docRight = doc.x + doc.cols * CELL;
      const docBottom = doc.y + doc.rows * CELL + DOC_LABEL_HEIGHT;
      const overlaps = minX < docRight && maxX > doc.x && minY < docBottom && maxY > doc.y;
      if (overlaps) {
        const highlightStyle = new TextStyle({
          fontSize: 7 * TEXT_SUPERSAMPLE,
          fill: '#ffffff',
          fontFamily: 'system-ui, sans-serif',
        });
        const docPixelWidth = doc.cols * CELL;
        const maxChars = Math.max(3, Math.floor(docPixelWidth / 4) - 1);
        const label = new Text({ text: abbreviate(doc.title, maxChars), style: highlightStyle });
        label.scale.set(1 / TEXT_SUPERSAMPLE);
        label.x = doc.x;
        label.y = doc.y;
        overlay.addChild(label);
      }
    }
  }

  // Stagger-reveal result tiles bright white
  const spotlightGfx = new Graphics();
  overlay.addChild(spotlightGfx);

  const REVEAL_DELAY_MS = 400;
  const REVEAL_STAGGER_MS = 120;
  const timers: ReturnType<typeof setTimeout>[] = [];

  for (let i = 0; i < chunkIds.length; i++) {
    const timer = setTimeout(() => {
      const pos = layout.tileIdToPos.get(chunkIds[i]);
      if (!pos) return;
      spotlightGfx.rect(pos.x, pos.y, TILE_SIZE, TILE_SIZE);
      spotlightGfx.fill({ color: 0xffffff, alpha: 0.95 });
    }, REVEAL_DELAY_MS + i * REVEAL_STAGGER_MS);
    timers.push(timer);
  }

  // Auto-frame: zoom to the result bounding box
  const padding = 40;
  const frameMinX = minX - padding;
  const frameMaxX = maxX + padding;
  const frameMinY = (minY - 16) - padding;
  const frameMaxY = maxY + padding;
  const frameW = frameMaxX - frameMinX;
  const frameH = frameMaxY - frameMinY;

  const vpW = el.clientWidth;
  const vpH = el.clientHeight;
  const targetScale = Math.min(vpW / frameW, vpH / frameH, SCALE_MAX);
  const targetX = (vpW - frameW * targetScale) / 2 - frameMinX * targetScale;
  const targetY = (vpH - frameH * targetScale) / 2 - frameMinY * targetScale;

  const startX = world.x;
  const startY = world.y;
  const startScale = world.scale.x;
  const duration = 400;
  const frameStart = performance.now();
  let frameAnimId: number | null = null;

  const animateFrame = (now: number) => {
    const t = Math.min(1, (now - frameStart) / duration);
    const ease = 1 - Math.pow(1 - t, 3);

    const s = startScale + (targetScale - startScale) * ease;
    world.scale.set(s);
    world.x = startX + (targetX - startX) * ease;
    world.y = startY + (targetY - startY) * ease;

    if (t < 1) {
      frameAnimId = requestAnimationFrame(animateFrame);
    }
  };

  frameAnimId = requestAnimationFrame(animateFrame);

  return () => {
    if (frameAnimId) cancelAnimationFrame(frameAnimId);
    for (const t of timers) clearTimeout(t);
  };
}

/* ─── Get Neighbors overlay ─── */

function drawGetNeighborsOverlay(
  overlay: Container,
  gridOp: import('../hooks/use-highlight-timeline').GridOp,
  layout: ComputedLayout,
  manifest: GridManifest,
  filters: { hideOldWpam: boolean },
  el: HTMLDivElement,
  world: Container,
): (() => void) | undefined {
  const { neighborData } = gridOp;
  if (!neighborData) return;

  const { seedDocId, neighborDocIds } = neighborData;

  // Build docId → first tile position map
  const docFirstTile = new Map<string, LayoutTile>();
  const filteredTiles = manifest.tiles.filter((tile) => {
    if (filters.hideOldWpam) {
      const doc = manifest.docs.find((d) => d.docId === tile.docId);
      if (doc?.isOldWpam) return false;
    }
    return true;
  });
  for (const tile of filteredTiles) {
    if (!docFirstTile.has(tile.docId)) {
      const pos = layout.tileIdToPos.get(tile.id);
      if (pos) docFirstTile.set(tile.docId, pos);
    }
  }

  // Collect neighbor positions
  const neighborPositions: { docId: string; pos: LayoutTile }[] = [];
  for (const docId of neighborDocIds) {
    const pos = docFirstTile.get(docId);
    if (pos) neighborPositions.push({ docId, pos });
  }
  if (neighborPositions.length === 0) return;

  // Seed position — fall back to center of neighbors if seed doc not in manifest
  const seedPos = docFirstTile.get(seedDocId) ?? neighborPositions[0].pos;

  // Bounding box
  let minX = seedPos.x, minY = seedPos.y, maxX = seedPos.x + TILE_SIZE, maxY = seedPos.y + TILE_SIZE;
  for (const { pos } of neighborPositions) {
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x + TILE_SIZE);
    maxY = Math.max(maxY, pos.y + TILE_SIZE);
  }

  // Dim layer
  const dimGfx = new Graphics();
  dimGfx.rect(0, 0, layout.worldWidth, layout.worldHeight);
  dimGfx.fill({ color: 0x000000, alpha: 0.5 });
  overlay.addChild(dimGfx);

  // Redraw relevant doc labels in white above dim
  const relevantDocIds = new Set([seedDocId, ...neighborDocIds]);
  for (const doc of layout.docs) {
    if (!relevantDocIds.has(doc.docId)) continue;
    const highlightStyle = new TextStyle({
      fontSize: 7 * TEXT_SUPERSAMPLE,
      fill: '#ffffff',
      fontFamily: 'system-ui, sans-serif',
    });
    const docPixelWidth = doc.cols * CELL;
    const maxChars = Math.max(3, Math.floor(docPixelWidth / 4) - 1);
    const label = new Text({ text: abbreviate(doc.title, maxChars), style: highlightStyle });
    label.scale.set(1 / TEXT_SUPERSAMPLE);
    label.x = doc.x;
    label.y = doc.y;
    overlay.addChild(label);
  }

  // Edges layer
  const edgesGfx = new Graphics();
  overlay.addChild(edgesGfx);

  // Seed node — bright circle
  const seedCenterX = seedPos.x + TILE_SIZE / 2;
  const seedCenterY = seedPos.y + TILE_SIZE / 2;
  const seedRadius = 10;

  const seedGfx = new Graphics();
  seedGfx.circle(seedCenterX, seedCenterY, seedRadius);
  seedGfx.fill({ color: 0xffffff, alpha: 0.9 });
  overlay.addChild(seedGfx);

  // Seed label
  const seedLabelStyle = new TextStyle({
    fontSize: 7 * TEXT_SUPERSAMPLE,
    fill: '#1a1a1a',
    fontFamily: 'system-ui, sans-serif',
    fontWeight: '700',
  });
  const seedLabel = new Text({ text: 'GN', style: seedLabelStyle });
  seedLabel.scale.set(1 / TEXT_SUPERSAMPLE);
  seedLabel.anchor.set(0.5);
  seedLabel.x = seedCenterX;
  seedLabel.y = seedCenterY;
  overlay.addChild(seedLabel);

  // Stagger neighbor node highlights + edges
  const EDGE_DELAY_MS = 400;
  const EDGE_STAGGER_MS = 80;
  const timers: ReturnType<typeof setTimeout>[] = [];

  for (let i = 0; i < neighborPositions.length; i++) {
    const timer = setTimeout(() => {
      const { pos } = neighborPositions[i];
      const cx = pos.x + TILE_SIZE / 2;
      const cy = pos.y + TILE_SIZE / 2;

      // Edge from seed to neighbor
      edgesGfx.moveTo(seedCenterX, seedCenterY);
      edgesGfx.lineTo(cx, cy);
      edgesGfx.stroke({ width: 1.5, color: 0xffffff, alpha: 0.5 });

      // Neighbor dot
      const dotGfx = new Graphics();
      dotGfx.circle(cx, cy, 4);
      dotGfx.fill({ color: 0xffffff, alpha: 0.8 });
      overlay.addChild(dotGfx);
    }, EDGE_DELAY_MS + i * EDGE_STAGGER_MS);
    timers.push(timer);
  }

  // Auto-frame
  const padding = 50;
  const frameMinX = minX - padding;
  const frameMaxX = maxX + padding;
  const frameMinY = minY - padding;
  const frameMaxY = maxY + padding;
  const frameW = frameMaxX - frameMinX;
  const frameH = frameMaxY - frameMinY;

  const vpW = el.clientWidth;
  const vpH = el.clientHeight;
  const targetScale = Math.min(vpW / frameW, vpH / frameH, SCALE_MAX);
  const targetX = (vpW - frameW * targetScale) / 2 - frameMinX * targetScale;
  const targetY = (vpH - frameH * targetScale) / 2 - frameMinY * targetScale;

  const startX = world.x;
  const startY = world.y;
  const startScale = world.scale.x;
  const duration = 400;
  const frameStart = performance.now();
  let frameAnimId: number | null = null;

  const animateFrame = (now: number) => {
    const t = Math.min(1, (now - frameStart) / duration);
    const ease = 1 - Math.pow(1 - t, 3);

    const s = startScale + (targetScale - startScale) * ease;
    world.scale.set(s);
    world.x = startX + (targetX - startX) * ease;
    world.y = startY + (targetY - startY) * ease;

    if (t < 1) {
      frameAnimId = requestAnimationFrame(animateFrame);
    }
  };

  frameAnimId = requestAnimationFrame(animateFrame);

  return () => {
    if (frameAnimId) cancelAnimationFrame(frameAnimId);
    for (const t of timers) clearTimeout(t);
  };
}

/* ─── Component ─── */

export function CorpusGrid({ manifest, filters, revealedIds, activeIds, focusChunkId, gridOp }: CorpusGridProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const worldRef = useRef<Container | null>(null);
  const highlightGfxRef = useRef<Graphics | null>(null);
  const scaleRef = useRef(SCALE_INITIAL);
  const minScaleRef = useRef(SCALE_MIN);
  const [minScale, setMinScale] = useState(SCALE_MIN);
  const [sliderValue, setSliderValue] = useState(SCALE_INITIAL);
  const [appReady, setAppReady] = useState(false);

  // Pan state
  const dragging = useRef(false);
  const lastPointer = useRef({ x: 0, y: 0 });

  // Compute layout from manifest + filters
  const layout = useMemo(
    () => computeLayout(manifest, filters),
    [manifest, filters]
  );

  // Create PixiJS Application once on mount, persist across filter changes
  useEffect(() => {
    if (!containerRef.current) return;

    const el = containerRef.current;
    const width = el.clientWidth;
    const height = el.clientHeight;
    let destroyed = false;

    const app = new Application();
    const initPromise = app.init({
      width,
      height,
      backgroundColor: 0x1a1a1a,
      antialias: false,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    }).then(() => {
      if (destroyed) { app.destroy(true); return; }

      el.appendChild(app.canvas as HTMLCanvasElement);
      appRef.current = app;

      const world = new Container();
      app.stage.addChild(world);
      worldRef.current = world;
      setAppReady(true);
    });

    // Zoom (wheel)
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const world = worldRef.current;
      if (!world) return;

      const factor = wheelZoomFactor(e, el.clientHeight);
      const newScale = Math.max(minScaleRef.current, Math.min(SCALE_MAX, world.scale.x * factor));
      const scaleChange = newScale / world.scale.x;

      const rect = el.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      world.x = mouseX - (mouseX - world.x) * scaleChange;
      world.y = mouseY - (mouseY - world.y) * scaleChange;
      world.scale.set(newScale);
      scaleRef.current = newScale;
      setSliderValue(newScale);
    };

    // Pan (drag)
    const handlePointerDown = (e: PointerEvent) => {
      dragging.current = true;
      lastPointer.current = { x: e.clientX, y: e.clientY };
      el.style.cursor = 'grabbing';
    };

    const handlePointerMove = (e: PointerEvent) => {
      if (!dragging.current) return;
      const world = worldRef.current;
      if (!world) return;
      const dx = e.clientX - lastPointer.current.x;
      const dy = e.clientY - lastPointer.current.y;
      world.x += dx;
      world.y += dy;
      lastPointer.current = { x: e.clientX, y: e.clientY };
    };

    const handlePointerUp = () => {
      dragging.current = false;
      el.style.cursor = 'grab';
    };

    el.style.cursor = 'grab';
    el.addEventListener('wheel', handleWheel, { passive: false });
    el.addEventListener('pointerdown', handlePointerDown);
    el.addEventListener('pointermove', handlePointerMove);
    el.addEventListener('pointerup', handlePointerUp);
    el.addEventListener('pointerleave', handlePointerUp);

    // Wait for init before first draw
    initPromise.then(() => {});

    return () => {
      destroyed = true;
      el.removeEventListener('wheel', handleWheel);
      el.removeEventListener('pointerdown', handlePointerDown);
      el.removeEventListener('pointermove', handlePointerMove);
      el.removeEventListener('pointerup', handlePointerUp);
      el.removeEventListener('pointerleave', handlePointerUp);
      if (appRef.current) {
        const canvas = appRef.current.canvas as HTMLCanvasElement;
        appRef.current.destroy(true, { children: true });
        appRef.current = null;
        worldRef.current = null;
        if (canvas && canvas.parentNode) {
          canvas.parentNode.removeChild(canvas);
        }
      }
    };
  }, []); // mount-only

  // Redraw world contents whenever layout changes or app becomes ready
  useEffect(() => {
    if (!appReady) return;
    const world = worldRef.current;
    const el = containerRef.current;
    if (!world || !el) return;

    world.removeChildren();

    const width = el.clientWidth;
    const height = el.clientHeight;

    // Fit world to viewport — this becomes the floor for zoom-out
    const scaleX = (width * 0.85) / layout.worldWidth;
    const scaleY = (height * 0.85) / layout.worldHeight;
    const fitScale = Math.min(scaleX, scaleY, SCALE_INITIAL);
    minScaleRef.current = fitScale;
    setMinScale(fitScale);
    world.scale.set(fitScale);
    world.x = (width - layout.worldWidth * fitScale) / 2;
    world.y = (height - layout.worldHeight * fitScale) / 2;
    scaleRef.current = fitScale;
    setSliderValue(fitScale);

    // Band labels — supersampled for sharp zoom
    const bandLabelStyle = new TextStyle({
      fontSize: 10 * TEXT_SUPERSAMPLE,
      fill: BAND_LABEL_COLOR,
      fontFamily: 'system-ui, sans-serif',
      fontWeight: '500',
    });
    for (const band of layout.bands) {
      const label = new Text({ text: band.label, style: bandLabelStyle });
      label.scale.set(1 / TEXT_SUPERSAMPLE);
      label.x = 0;
      label.y = band.y;
      world.addChild(label);
    }

    // Doc title labels — abbreviated, supersampled
    const docLabelStyle = new TextStyle({
      fontSize: 7 * TEXT_SUPERSAMPLE,
      fill: DOC_LABEL_COLOR,
      fontFamily: 'system-ui, sans-serif',
    });
    const CHAR_WIDTH_APPROX = 4;
    for (const doc of layout.docs) {
      const docPixelWidth = doc.cols * CELL;
      const maxChars = Math.max(3, Math.floor(docPixelWidth / CHAR_WIDTH_APPROX) - 1);
      const label = new Text({ text: abbreviate(doc.title, maxChars), style: docLabelStyle });
      label.scale.set(1 / TEXT_SUPERSAMPLE);
      label.x = doc.x;
      label.y = doc.y;
      world.addChild(label);
    }

    // Tiles — single batched Graphics
    const tilesGfx = new Graphics();
    for (const tile of layout.tiles) {
      if (tile) {
        tilesGfx.rect(tile.x, tile.y, TILE_SIZE, TILE_SIZE);
      }
    }
    tilesGfx.fill({ color: TILE_COLOR_IDLE, alpha: TILE_COLOR_IDLE_ALPHA });
    world.addChild(tilesGfx);

    // Highlight layer — drawn on top, updated by trace events
    const highlightGfx = new Graphics();
    world.addChild(highlightGfx);
    highlightGfxRef.current = highlightGfx;
  }, [layout, appReady]);

  // Highlight tiles: revealed (cumulative, dimmer) + active (current batch, bright)
  useEffect(() => {
    const gfx = highlightGfxRef.current;
    if (!gfx || !appReady) return;

    gfx.clear();

    // Draw revealed tiles (cumulative) at medium brightness
    if (revealedIds.size > 0) {
      for (const id of revealedIds) {
        const pos = layout.tileIdToPos.get(id);
        if (pos) {
          gfx.rect(pos.x, pos.y, TILE_SIZE, TILE_SIZE);
        }
      }
      gfx.fill({ color: 0xffffff, alpha: 0.7 });
    }

    // Draw active tiles (current batch) brighter on top
    if (activeIds.size > 0) {
      for (const id of activeIds) {
        const pos = layout.tileIdToPos.get(id);
        if (pos) {
          gfx.rect(pos.x, pos.y, TILE_SIZE, TILE_SIZE);
        }
      }
      gfx.fill({ color: 0xffffff, alpha: 1.0 });
    }
  }, [revealedIds, activeIds, layout, appReady]);

  // Grid overlay: draws tool-specific visuals based on gridOp
  const gridOverlayRef = useRef<Container | null>(null);

  useEffect(() => {
    if (!appReady) return;
    const world = worldRef.current;
    const el = containerRef.current;
    if (!world || !el) return;

    // Clean up previous overlay
    if (gridOverlayRef.current) {
      gridOverlayRef.current.destroy({ children: true });
      gridOverlayRef.current = null;
    }

    if (!gridOp) return;

    const overlay = new Container();
    world.addChild(overlay);
    gridOverlayRef.current = overlay;

    // Route to tool-specific visualizations
    if (gridOp.toolName === 'search_document' || gridOp.toolName === 'get_section') {
      const cleanup = drawSearchDocumentOverlay(overlay, gridOp, layout, el, world, filters.collapseToDocTypes);
      return () => {
        cleanup?.();
        if (gridOverlayRef.current) {
          gridOverlayRef.current.destroy({ children: true });
          gridOverlayRef.current = null;
        }
      };
    }

    if (gridOp.toolName === 'get_neighbors' && gridOp.neighborData && gridOp.neighborData.neighborDocIds.length > 0) {
      const cleanup = drawGetNeighborsOverlay(overlay, gridOp, layout, manifest, filters, el, world);
      return () => {
        cleanup?.();
        if (gridOverlayRef.current) {
          gridOverlayRef.current.destroy({ children: true });
          gridOverlayRef.current = null;
        }
      };
    }

    if (gridOp.toolName !== 'vector_search') {
      // Default for unhandled tools (faq_search, get_authority_chain, prepare_answer, etc.):
      // zoom out to frame all retrieved chunks so far
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const id of revealedIds) {
        const pos = layout.tileIdToPos.get(id);
        if (pos) {
          minX = Math.min(minX, pos.x);
          minY = Math.min(minY, pos.y);
          maxX = Math.max(maxX, pos.x + TILE_SIZE);
          maxY = Math.max(maxY, pos.y + TILE_SIZE);
        }
      }
      if (minX === Infinity) return;

      const padding = 80;
      const frameMinX = minX - padding;
      const frameMaxX = maxX + padding;
      const frameMinY = minY - padding;
      const frameMaxY = maxY + padding;
      const frameW = frameMaxX - frameMinX;
      const frameH = frameMaxY - frameMinY;

      const vpW = el.clientWidth;
      const vpH = el.clientHeight;
      const targetScale = Math.min(vpW / frameW, vpH / frameH, SCALE_MAX);
      const targetX = (vpW - frameW * targetScale) / 2 - frameMinX * targetScale;
      const targetY = (vpH - frameH * targetScale) / 2 - frameMinY * targetScale;

      const startX = world.x;
      const startY = world.y;
      const startScale = world.scale.x;
      const duration = 500;
      const frameStart = performance.now();
      let frameAnimId: number | null = null;

      const animateFrame = (now: number) => {
        const t = Math.min(1, (now - frameStart) / duration);
        const ease = 1 - Math.pow(1 - t, 3);

        const s = startScale + (targetScale - startScale) * ease;
        world.scale.set(s);
        world.x = startX + (targetX - startX) * ease;
        world.y = startY + (targetY - startY) * ease;
        scaleRef.current = s;

        if (t < 1) {
          frameAnimId = requestAnimationFrame(animateFrame);
        } else {
          setSliderValue(targetScale);
        }
      };

      frameAnimId = requestAnimationFrame(animateFrame);

      return () => {
        if (frameAnimId) cancelAnimationFrame(frameAnimId);
        if (gridOverlayRef.current) {
          gridOverlayRef.current.destroy({ children: true });
          gridOverlayRef.current = null;
        }
      };
    }

    // vector_search — only reached if vectorCalls exist
    if (gridOp.vectorCalls.length === 0) return;

    // Collect all chunk IDs across all vector calls for bounding box
    const allChunkIds: string[] = [];
    for (const vc of gridOp.vectorCalls) {
      allChunkIds.push(...vc.chunkIds);
    }

    // Find bounding box
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of allChunkIds) {
      const pos = layout.tileIdToPos.get(id);
      if (pos) {
        minX = Math.min(minX, pos.x);
        minY = Math.min(minY, pos.y);
        maxX = Math.max(maxX, pos.x + TILE_SIZE);
        maxY = Math.max(maxY, pos.y + TILE_SIZE);
      }
    }
    if (minX === Infinity) return;

    const bbCenterX = (minX + maxX) / 2;
    const bbCenterY = (minY + maxY) / 2;

    // Position Q nodes: spread horizontally if multiple, centered if single
    const qRadius = 14;
    const qSpacing = 40;
    const numQ = gridOp.vectorCalls.length;
    const qStartX = bbCenterX - ((numQ - 1) * qSpacing) / 2;

    // Edges layer (behind Q nodes)
    const edgesGfx = new Graphics();
    overlay.addChild(edgesGfx);

    // Draw Q nodes on top
    const qPositions: { x: number; y: number; label: string }[] = [];
    for (let qi = 0; qi < numQ; qi++) {
      const qX = qStartX + qi * qSpacing;
      const qY = bbCenterY;
      const label = gridOp.vectorCalls[qi].label;
      qPositions.push({ x: qX, y: qY, label });

      const qGfx = new Graphics();
      qGfx.circle(qX, qY, qRadius);
      qGfx.fill({ color: 0xffffff, alpha: 0.9 });
      overlay.addChild(qGfx);

      const qLabelStyle = new TextStyle({
        fontSize: 9 * TEXT_SUPERSAMPLE,
        fill: '#1a1a1a',
        fontFamily: 'system-ui, sans-serif',
        fontWeight: '700',
      });
      const qLabelText = new Text({ text: label, style: qLabelStyle });
      qLabelText.scale.set(1 / TEXT_SUPERSAMPLE);
      qLabelText.anchor.set(0.5);
      qLabelText.x = qX;
      qLabelText.y = qY;
      overlay.addChild(qLabelText);
    }

    // Precompute all edges grouped by Q node
    type EdgeData = { qX: number; qY: number; tileCenter: { x: number; y: number }; lineWidth: number; alpha: number };
    const allEdges: EdgeData[] = [];
    for (let qi = 0; qi < numQ; qi++) {
      const vc = gridOp.vectorCalls[qi];
      const { x: qX, y: qY } = qPositions[qi];
      const total = vc.chunkIds.length;
      for (let rank = 0; rank < total; rank++) {
        const pos = layout.tileIdToPos.get(vc.chunkIds[rank]);
        if (!pos) continue;
        const strength = 1 - (rank / total) * 0.7;
        allEdges.push({
          qX, qY,
          tileCenter: { x: pos.x + TILE_SIZE / 2, y: pos.y + TILE_SIZE / 2 },
          lineWidth: Math.max(0.5, strength * 2.5),
          alpha: Math.max(0.2, strength * 0.8),
        });
      }
    }

    // Stagger edges: 500ms delay, then 100ms between each
    const EDGE_DELAY_MS = 500;
    const EDGE_STAGGER_MS = 100;
    const edgeTimers: ReturnType<typeof setTimeout>[] = [];

    for (let i = 0; i < allEdges.length; i++) {
      const timer = setTimeout(() => {
        const edge = allEdges[i];
        edgesGfx.moveTo(edge.qX, edge.qY);
        edgesGfx.lineTo(edge.tileCenter.x, edge.tileCenter.y);
        edgesGfx.stroke({ width: edge.lineWidth, color: 0xffffff, alpha: edge.alpha });

        const dx = edge.tileCenter.x - edge.qX;
        const dy = edge.tileCenter.y - edge.qY;
        const len = Math.sqrt(dx * dx + dy * dy);
        if (len < 1) return;
        const nx = dx / len;
        const ny = dy / len;
        const arrowLen = 4;
        const arrowX = edge.tileCenter.x - nx * 3;
        const arrowY = edge.tileCenter.y - ny * 3;
        const perpX = -ny;
        const perpY = nx;

        edgesGfx.moveTo(arrowX + perpX * arrowLen * 0.5, arrowY + perpY * arrowLen * 0.5);
        edgesGfx.lineTo(edge.tileCenter.x, edge.tileCenter.y);
        edgesGfx.lineTo(arrowX - perpX * arrowLen * 0.5, arrowY - perpY * arrowLen * 0.5);
        edgesGfx.stroke({ width: edge.lineWidth, color: 0xffffff, alpha: edge.alpha });
      }, EDGE_DELAY_MS + i * EDGE_STAGGER_MS);
      edgeTimers.push(timer);
    }

    // Auto-frame: zoom/pan to fit bounding box + Q nodes with padding
    const padding = 60;
    const frameMinX = Math.min(minX, qStartX - qRadius) - padding;
    const frameMaxX = Math.max(maxX, qStartX + (numQ - 1) * qSpacing + qRadius) + padding;
    const frameMinY = Math.min(minY, bbCenterY - qRadius) - padding;
    const frameMaxY = Math.max(maxY, bbCenterY + qRadius) + padding;
    const frameW = frameMaxX - frameMinX;
    const frameH = frameMaxY - frameMinY;

    const vpW = el.clientWidth;
    const vpH = el.clientHeight;
    const targetScale = Math.min(vpW / frameW, vpH / frameH, SCALE_MAX);
    const targetX = (vpW - frameW * targetScale) / 2 - frameMinX * targetScale;
    const targetY = (vpH - frameH * targetScale) / 2 - frameMinY * targetScale;

    const startX = world.x;
    const startY = world.y;
    const startScale = world.scale.x;
    const frameDuration = 400;
    const frameStart = performance.now();
    let frameAnimId: number | null = null;

    const animateFrame = (now: number) => {
      const t = Math.min(1, (now - frameStart) / frameDuration);
      const ease = 1 - Math.pow(1 - t, 3);

      const s = startScale + (targetScale - startScale) * ease;
      world.scale.set(s);
      world.x = startX + (targetX - startX) * ease;
      world.y = startY + (targetY - startY) * ease;
      scaleRef.current = s;

      if (t < 1) {
        frameAnimId = requestAnimationFrame(animateFrame);
      } else {
        setSliderValue(targetScale);
      }
    };

    frameAnimId = requestAnimationFrame(animateFrame);

    return () => {
      if (frameAnimId) cancelAnimationFrame(frameAnimId);
      for (const t of edgeTimers) clearTimeout(t);
    };
  }, [gridOp, layout, appReady, manifest, filters]);

  // Animate to focused chunk + flash highlight
  const animRef = useRef<number | null>(null);
  const focusGfxRef = useRef<Graphics | null>(null);
  const fadeRef = useRef<number | null>(null);
  useEffect(() => {
    if (!focusChunkId || !appReady) return;
    const world = worldRef.current;
    const el = containerRef.current;
    if (!world || !el) return;

    const pos = layout.tileIdToPos.get(focusChunkId);
    if (!pos) return;

    // Target: center the tile at 3x zoom
    const targetScale = 3;
    const vpW = el.clientWidth;
    const vpH = el.clientHeight;
    const targetX = vpW / 2 - (pos.x + TILE_SIZE / 2) * targetScale;
    const targetY = vpH / 2 - (pos.y + TILE_SIZE / 2) * targetScale;

    const startX = world.x;
    const startY = world.y;
    const startScale = world.scale.x;
    const duration = 350;
    const startTime = performance.now();

    const animate = (now: number) => {
      const t = Math.min(1, (now - startTime) / duration);
      const ease = 1 - Math.pow(1 - t, 3); // ease-out cubic

      world.x = startX + (targetX - startX) * ease;
      world.y = startY + (targetY - startY) * ease;
      const s = startScale + (targetScale - startScale) * ease;
      world.scale.set(s);
      scaleRef.current = s;

      if (t < 1) {
        animRef.current = requestAnimationFrame(animate);
      } else {
        setSliderValue(targetScale);
        animRef.current = null;
        // Flash the focused tile light blue then fade out
        flashFocusTile(pos);
      }
    };

    const flashFocusTile = (tilePos: LayoutTile) => {
      // Remove previous focus highlight
      if (focusGfxRef.current) {
        focusGfxRef.current.destroy();
        focusGfxRef.current = null;
      }
      if (fadeRef.current) {
        cancelAnimationFrame(fadeRef.current);
        fadeRef.current = null;
      }

      const gfx = new Graphics();
      gfx.rect(tilePos.x - 1, tilePos.y - 1, TILE_SIZE + 2, TILE_SIZE + 2);
      gfx.fill({ color: 0x88ccff, alpha: 1.0 });
      world.addChild(gfx);
      focusGfxRef.current = gfx;

      // Fade out over 2 seconds
      const fadeStart = performance.now();
      const fadeDuration = 2000;
      const fadeOut = (now: number) => {
        const ft = Math.min(1, (now - fadeStart) / fadeDuration);
        gfx.alpha = 1 - ft;
        if (ft < 1) {
          fadeRef.current = requestAnimationFrame(fadeOut);
        } else {
          gfx.destroy();
          focusGfxRef.current = null;
          fadeRef.current = null;
        }
      };
      fadeRef.current = requestAnimationFrame(fadeOut);
    };

    if (animRef.current) cancelAnimationFrame(animRef.current);
    animRef.current = requestAnimationFrame(animate);

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
      if (fadeRef.current) cancelAnimationFrame(fadeRef.current);
    };
  }, [focusChunkId, layout, appReady]);

  // Slider change handler — zoom toward canvas center
  const handleSliderChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newScale = parseFloat(e.target.value);
      const world = worldRef.current;
      const el = containerRef.current;
      if (!world || !el) return;

      const width = el.clientWidth;
      const height = el.clientHeight;
      const centerX = width / 2;
      const centerY = height / 2;

      const scaleChange = newScale / world.scale.x;
      world.x = centerX - (centerX - world.x) * scaleChange;
      world.y = centerY - (centerY - world.y) * scaleChange;
      world.scale.set(newScale);
      scaleRef.current = newScale;
      setSliderValue(newScale);
    },
    []
  );

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="absolute inset-0" />
      {/* Vertical zoom slider on right edge */}
      <div
        className="absolute right-4 top-1/2 -translate-y-1/2 flex items-center justify-center z-10"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <input
          type="range"
          min={minScale}
          max={SCALE_MAX}
          step={0.01}
          value={sliderValue}
          onChange={handleSliderChange}
          className="visualizer-zoom-slider"
          style={{
            writingMode: 'vertical-lr',
            direction: 'rtl',
            width: '12px',
            height: '200px',
          }}
        />
        <style dangerouslySetInnerHTML={{ __html: `
          .visualizer-zoom-slider {
            -webkit-appearance: none;
            appearance: none;
            background: transparent;
            cursor: pointer;
          }
          .visualizer-zoom-slider::-webkit-slider-runnable-track {
            width: 2px;
            height: 100%;
            background: rgba(255,255,255,0.1);
            border-radius: 1px;
          }
          .visualizer-zoom-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: rgba(255,255,255,0.4);
            border: none;
            margin-left: -5px;
          }
          .visualizer-zoom-slider::-moz-range-track {
            width: 2px;
            height: 100%;
            background: rgba(255,255,255,0.1);
            border-radius: 1px;
          }
          .visualizer-zoom-slider::-moz-range-thumb {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: rgba(255,255,255,0.4);
            border: none;
          }
        `}} />
      </div>
    </div>
  );
}
