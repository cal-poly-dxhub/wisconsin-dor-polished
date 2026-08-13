'use client';

// Small, palette-aligned field-state pills. Required is a solid dark pill with
// white text so it reads as a firm requirement without the "error" connotation
// of red. Optional is a soft, low-contrast pill that recedes. Kept tiny and
// nudged down a hair so they sit centered against label text.

const base =
  'inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide leading-none translate-y-px';

export function RequiredMark() {
  return (
    <span className={`${base} bg-foreground text-background`}>Required</span>
  );
}

export function OptionalMark() {
  return (
    <span className={`${base} bg-muted text-muted-foreground/70`}>Optional</span>
  );
}
