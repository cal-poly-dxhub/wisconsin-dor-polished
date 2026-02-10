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

  // Extract display text from URL if source is a URL
  const isUrl = source.startsWith('http://') || source.startsWith('https://');
  const displayText = isUrl ? new URL(source).hostname.replace('www.', '') : source;
  const linkUrl = sourceUrl || (isUrl ? source : undefined);

  const badgeContent = (
    <>
      {linkUrl && <Link className="mr-1 h-3 w-3 flex-shrink-0" />}
      <span className="truncate opacity-90">{displayText}</span>
    </>
  );

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    // Prevent the card from expanding when clicking the badge
    e.stopPropagation();
    // Call optional tracking callback
    if (onSourceClick) {
      onSourceClick(e);
    }
    // Let the browser handle navigation naturally (don't prevent default)
  };

  if (linkUrl) {
    return (
      <a
        href={linkUrl}
        target="_blank"
        rel="noopener noreferrer"
        onClick={handleClick}
        className="inline-block no-underline group"
      >
        <BadgeWithFade
          variant="outline"
          className={`hover:bg-accent hover:border-primary/50 cursor-pointer transition-all duration-200 ease-in-out group-hover:shadow-sm ${sizeClasses[size]}`}
        >
          {badgeContent}
        </BadgeWithFade>
      </a>
    );
  }

  return (
    <BadgeWithFade
      variant="outline"
      className={`transition-colors duration-200 ease-in-out ${sizeClasses[size]}`}
    >
      {badgeContent}
    </BadgeWithFade>
  );
}
