'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { Activity, Upload, Grid3X3 } from 'lucide-react';

const NAV_ITEMS = [
  { href: '/admin/activity', label: 'Activity', icon: Activity },
  { href: '/admin/ingest', label: 'Ingest', icon: Upload },
  { href: '/admin/chunks', label: 'Chunks', icon: Grid3X3 },
];

function AdminSidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 w-48 border-r border-border bg-background">
      <div className="flex h-12 items-center px-4">
        <Link href="/admin/activity" className="text-sm font-semibold text-foreground">
          Admin
        </Link>
      </div>
      <nav className="space-y-0.5 px-2">
        {NAV_ITEMS.map(item => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                isActive
                  ? 'bg-accent text-foreground font-medium'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
              }`}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-background">
        <AdminSidebar />
        <main className="pl-48">{children}</main>
      </div>
    </ProtectedRoute>
  );
}
