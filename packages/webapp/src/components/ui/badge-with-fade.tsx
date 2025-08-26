"use client";

import { cn } from "@/lib/utils";
import { Badge } from "./badge";

interface BadgeWithFadeProps {
  children: React.ReactNode;
  className?: string;
  variant?: "default" | "secondary" | "destructive" | "outline";
  onClick?: (e: React.MouseEvent) => void;
}

export function BadgeWithFade({
  children,
  className,
  variant = "outline",
  onClick,
}: BadgeWithFadeProps) {
  return (
    <Badge
      variant={variant}
      className={cn("truncate", className)}
      onClick={onClick}
    >
      {children}
    </Badge>
  );
}
