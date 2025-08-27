import { ThemeProvider } from '@/components/theme-provider';
import { ThemeToggleContainer } from '@/components/theme-toggle-container';
import type { Metadata } from 'next';
import { Geist_Mono } from 'next/font/google';
import './globals.css';

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'Component Display',
  description: 'A simple page to display components',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${geistMono.variable} antialiased`}>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          <div className="relative min-h-screen">
            <ThemeToggleContainer />
            {children}
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
