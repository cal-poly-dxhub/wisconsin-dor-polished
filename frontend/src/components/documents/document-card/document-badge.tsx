import { cn } from "@/lib/utils";
import { ExternalLink } from "lucide-react";

interface DocumentBadgeProps {
  source: string;
  sourceUrl?: string;
  s3Key?: string;
  onSourceClick?: (e: React.MouseEvent) => void;
  size?: "sm" | "md";
}

export function DocumentBadge({
  source,
  sourceUrl,
  s3Key,
  onSourceClick,
  size = "sm",
}: DocumentBadgeProps) {
  const sizeClasses = {
    sm: "h-8 text-xs",
    md: "h-9 text-sm",
  };

  const clickable = !!(sourceUrl || s3Key);

  return (
    <button
      type="button"
      className={cn(
        "border-input bg-background hover:bg-accent hover:text-accent-foreground inline-flex w-full min-w-0 items-center justify-center gap-2 rounded-md border px-3 font-medium transition-colors",
        clickable ? "cursor-pointer" : "cursor-default opacity-70",
        sizeClasses[size]
      )}
      onClick={clickable ? onSourceClick : undefined}
      disabled={!clickable}
      aria-label={clickable ? `Open source ${source}` : undefined}
    >
      <span className="truncate">{source}</span>
      {clickable && <ExternalLink className="h-3 w-3 flex-shrink-0" />}
    </button>
  );
}
