import { BadgeWithFade } from "@/components/ui/badge-with-fade";
import { Link } from "lucide-react";

interface DocumentBadgeProps {
  source: string;
  sourceUrl?: string;
  onSourceClick?: (e: React.MouseEvent) => void;
  size?: "sm" | "md";
}

export function DocumentBadge({
  source,
  sourceUrl,
  onSourceClick,
  size = "sm",
}: DocumentBadgeProps) {
  const sizeClasses = {
    sm: "max-w-32 xl:max-w-40 text-xs mb-3",
    md: "max-w-40 xl:max-w-48",
  };

  return (
    <BadgeWithFade
      variant="outline"
      className={`hover:bg-accent cursor-pointer transition-colors duration-200 ease-in-out ${sourceUrl ? "hover:underline" : ""} ${sizeClasses[size]} `}
      onClick={sourceUrl ? onSourceClick : undefined}
    >
      {sourceUrl && <Link className="mr-1 h-3 w-3 flex-shrink-0" />}
      <span className="truncate opacity-90">{source}</span>
    </BadgeWithFade>
  );
}
