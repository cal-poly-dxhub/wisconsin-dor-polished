'use client';

import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  GitBranch,
  ArrowRight,
  ArrowLeft,
  RotateCcw,
  Scale,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Flag,
} from 'lucide-react';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../ui/dialog';
import type {
  FlowchartContent,
  FlowchartNode,
  FlowchartEdge,
} from '@messages/websocket-interface';

interface FlowchartWalkthroughProps {
  flowchart: FlowchartContent;
}

/** Outcome accent colors keyed by a terminal node's `outcome`. */
function outcomeTone(outcome?: string): { icon: typeof Flag; className: string } {
  switch (outcome) {
    case 'exempt':
    case 'qualified':
    case 'manufacturing':
      return { icon: CheckCircle2, className: 'text-emerald-600 dark:text-emerald-400' };
    case 'taxable':
    case 'not_qualified':
    case 'not_manufacturing':
      return { icon: XCircle, className: 'text-rose-600 dark:text-rose-400' };
    default:
      return { icon: Flag, className: 'text-amber-600 dark:text-amber-400' };
  }
}

/**
 * "Walk the flowchart" — an interactive, one-step-at-a-time walk through a WPAM
 * decision tree seeded for this query. A highlighted button opens a modal that
 * presents the current decision (question + criteria + governing authorities),
 * lets the user follow the Yes / No branch, and lands on the terminal outcome.
 * Back and Restart controls let the user explore freely. The DOR disclaimer is
 * always visible — the walk is general guidance, not a determination.
 */
export function FlowchartWalkthrough({ flowchart }: FlowchartWalkthroughProps) {
  const [open, setOpen] = useState(false);
  // Path of visited node ids; last element is the current node.
  const [path, setPath] = useState<string[]>([flowchart.startNode]);

  const nodesById = useMemo(() => {
    const m = new Map<string, FlowchartNode>();
    for (const n of flowchart.nodes) m.set(n.id, n);
    return m;
  }, [flowchart.nodes]);

  const edgesFrom = useMemo(() => {
    const m = new Map<string, FlowchartEdge[]>();
    for (const e of flowchart.edges) {
      const list = m.get(e.from) ?? [];
      list.push(e);
      m.set(e.from, list);
    }
    return m;
  }, [flowchart.edges]);

  const decisionCount = useMemo(
    () => flowchart.nodes.filter(n => n.type === 'decision').length,
    [flowchart.nodes]
  );

  const currentId = path[path.length - 1];
  const current = nodesById.get(currentId);

  const reset = () => setPath([flowchart.startNode]);
  const back = () => setPath(p => (p.length > 1 ? p.slice(0, -1) : p));

  // Advance along the edge whose branch matches (or the sole pass-through edge).
  const advance = (branch?: 'yes' | 'no') => {
    const outgoing = edgesFrom.get(currentId) ?? [];
    const edge = branch
      ? outgoing.find(e => e.branch === branch)
      : outgoing.find(e => !e.branch) ?? outgoing[0];
    if (edge) setPath(p => [...p, edge.to]);
  };

  // Auto-skip a start node so the first visible card is the first decision.
  const visible = current?.type === 'start' ? followPassThrough(current) : current;

  function followPassThrough(node: FlowchartNode): FlowchartNode | undefined {
    const out = edgesFrom.get(node.id) ?? [];
    const next = out.find(e => !e.branch) ?? out[0];
    return next ? nodesById.get(next.to) : node;
  }

  // If we're sitting on a start node, jump the path forward to its successor so
  // Back/step counting behaves. Done lazily on open.
  const ensurePastStart = () => {
    if (nodesById.get(flowchart.startNode)?.type === 'start') {
      const out = edgesFrom.get(flowchart.startNode) ?? [];
      const next = out.find(e => !e.branch) ?? out[0];
      if (next) setPath([flowchart.startNode, next.to]);
    }
  };

  const node = visible;
  const isDecision = node?.type === 'decision';
  const isTerminal = node?.type === 'terminal' || node?.type === 'end';

  // Step label: which decision number we're on (1-indexed) when known.
  const stepLabel =
    node?.step != null
      ? `Step ${node.step} of ${decisionCount}`
      : isTerminal
        ? 'Outcome'
        : '';

  return (
    <Dialog
      open={open}
      onOpenChange={o => {
        setOpen(o);
        if (o) {
          reset();
          ensurePastStart();
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="group gap-2 border-sky-300 bg-sky-50 text-sky-800 hover:bg-sky-100 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-300 dark:hover:bg-sky-900/50"
        >
          <GitBranch className="h-4 w-4" />
          Walk the flowchart
          <ArrowRight className="h-3.5 w-3.5 opacity-0 -translate-x-1 transition-all group-hover:opacity-100 group-hover:translate-x-0" />
        </Button>
      </DialogTrigger>

      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <GitBranch className="h-4 w-4 text-sky-600 dark:text-sky-400" />
            {flowchart.title}
          </DialogTitle>
          {flowchart.statute && (
            <p className="text-xs text-muted-foreground">{flowchart.statute}</p>
          )}
        </DialogHeader>

        {/* Progress / step label */}
        {stepLabel && (
          <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">
            <span>{stepLabel}</span>
            {node?.step != null && (
              <div className="flex gap-1">
                {Array.from({ length: decisionCount }).map((_, i) => (
                  <span
                    key={i}
                    className={
                      i < (node.step ?? 0)
                        ? 'h-1.5 w-4 rounded-full bg-sky-500'
                        : 'h-1.5 w-4 rounded-full bg-muted'
                    }
                  />
                ))}
              </div>
            )}
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.div
            key={currentId}
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -12 }}
            transition={{ duration: 0.18 }}
            className="min-h-[9rem]"
          >
            {node && (isDecision || node.type === 'note') && (
              <div className="space-y-3">
                <p className="text-sm font-medium leading-relaxed">
                  {node.question ?? node.label}
                </p>
                {node.definition && (
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    {node.definition}
                  </p>
                )}
                {node.criteria && node.criteria.length > 0 && (
                  <ul className="ml-1 space-y-1">
                    {node.criteria.map((c, i) => (
                      <li
                        key={i}
                        className="flex gap-2 text-xs text-muted-foreground leading-relaxed"
                      >
                        <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-sky-400" />
                        {c}
                      </li>
                    ))}
                  </ul>
                )}
                {node.guidance && (
                  <p className="text-xs italic text-muted-foreground">{node.guidance}</p>
                )}
                {node.note && (
                  <p className="rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                    {node.note}
                  </p>
                )}
                <AuthorityList authorities={node.authorities} />
              </div>
            )}

            {node && isTerminal && <TerminalCard node={node} />}
          </motion.div>
        </AnimatePresence>

        {/* Branch / navigation controls */}
        <div className="mt-1 space-y-3">
          {isDecision && (
            <div className="flex gap-2">
              <Button
                onClick={() => advance('yes')}
                className="flex-1 gap-1.5 bg-emerald-600 hover:bg-emerald-700"
              >
                <CheckCircle2 className="h-4 w-4" />
                Yes
              </Button>
              <Button
                onClick={() => advance('no')}
                className="flex-1 gap-1.5 bg-rose-600 hover:bg-rose-700"
              >
                <XCircle className="h-4 w-4" />
                No
              </Button>
            </div>
          )}
          {node?.type === 'note' && (
            <Button onClick={() => advance()} className="w-full gap-1.5">
              Continue
              <ArrowRight className="h-4 w-4" />
            </Button>
          )}

          <div className="flex items-center justify-between">
            <Button
              variant="ghost"
              size="sm"
              onClick={back}
              disabled={path.length <= 1}
              className="gap-1.5 text-xs text-muted-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                reset();
                ensurePastStart();
              }}
              className="gap-1.5 text-xs text-muted-foreground"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Start over
            </Button>
          </div>
        </div>

        {/* Disclaimer + source — always visible. */}
        <div className="mt-1 space-y-2 border-t pt-3">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {flowchart.disclaimer}
          </p>
          {flowchart.sourceUrl && (
            <a
              href={flowchart.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] font-medium text-sky-600 hover:underline dark:text-sky-400"
            >
              <ExternalLink className="h-3 w-3" />
              View in the WPAM
              {flowchart.wpamPage ? ` (p. ${flowchart.wpamPage})` : ''}
            </a>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function AuthorityList({
  authorities,
}: {
  authorities?: FlowchartContent['nodes'][number]['authorities'];
}) {
  if (!authorities || authorities.length === 0) return null;
  return (
    <div className="space-y-1.5 rounded-md bg-muted/50 p-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Scale className="h-3 w-3" />
        Authorities
      </div>
      {authorities.map((a, i) => (
        <div key={i} className="text-xs leading-relaxed">
          <span className="font-medium">{a.cite}</span>
          {a.note && <span className="text-muted-foreground"> — {a.note}</span>}
        </div>
      ))}
    </div>
  );
}

function TerminalCard({ node }: { node: FlowchartNode }) {
  const tone = outcomeTone(node.outcome);
  const Icon = tone.icon;
  return (
    <div className="space-y-3">
      <div className={`flex items-center gap-2 ${tone.className}`}>
        <Icon className="h-5 w-5" />
        <span className="text-sm font-semibold">{node.label}</span>
      </div>
      {node.action && (
        <p className="text-sm leading-relaxed text-foreground/90">{node.action}</p>
      )}
      <AuthorityList authorities={node.authorities} />
    </div>
  );
}
