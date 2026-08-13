'use client';

// Shared two-button Yes/No control. Yes reads positive (emerald), No negative
// (red). Value is '' (unset) | 'yes' | 'no'.
export function YesNo({
  value,
  onChange,
  ariaLabel,
}: {
  value: string;
  onChange: (v: 'yes' | 'no') => void;
  ariaLabel: string;
}) {
  const options: { v: 'yes' | 'no'; label: string; active: string }[] = [
    { v: 'yes', label: 'Yes', active: 'border-emerald-400 bg-emerald-500/15 text-emerald-500' },
    { v: 'no', label: 'No', active: 'border-red-400 bg-red-500/15 text-red-500' },
  ];
  return (
    <div className="flex shrink-0 items-center gap-1.5" role="radiogroup" aria-label={ariaLabel}>
      {options.map(({ v, label, active }) => {
        const selected = value === v;
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(v)}
            className={`h-7 rounded-md border px-3 text-xs font-medium transition-[color,background-color,border-color] cursor-pointer ${
              selected
                ? active
                : 'border-border text-muted-foreground hover:border-muted-foreground/40 hover:text-foreground'
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
