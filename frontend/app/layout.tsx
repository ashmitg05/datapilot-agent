import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "DataPilot",
  description: "A tool-using data analyst agent with self-correction and evaluation.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-zinc-200 dark:border-zinc-800">
          <nav className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold">DataPilot</Link>
            <Link href="/how-it-works" className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100">
              How it works
            </Link>
          </nav>
        </header>
        <main className="mx-auto max-w-4xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
