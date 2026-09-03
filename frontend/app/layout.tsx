import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SystemDecoded Studio",
  description: "Complex Technology. Decoded. — autonomous content operations",
};

const NAV = [
  { label: "Dashboard", href: "/", active: true },
  { label: "Review", href: "/review", active: true },
  { label: "Ideas", href: "#", active: false, phase: 4 },
  { label: "Analytics", href: "#", active: false, phase: 3 },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-ink-800 bg-ink-900">
            <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-4">
              <div>
                <div className="text-sm font-semibold tracking-tight">SystemDecoded</div>
                <div className="text-xs text-ink-500">Complex Technology. Decoded.</div>
              </div>
              <nav className="flex items-center gap-1 text-sm">
                {NAV.map((item) =>
                  item.active ? (
                    <a
                      key={item.label}
                      href={item.href}
                      className="rounded-md px-3 py-1.5 text-ink-300 transition hover:bg-ink-800 hover:text-ink-100"
                    >
                      {item.label}
                    </a>
                  ) : (
                    <span
                      key={item.label}
                      title={`Arrives in Phase ${item.phase}`}
                      className="cursor-not-allowed rounded-md px-3 py-1.5 text-ink-500"
                    >
                      {item.label}
                      <span className="ml-1.5 text-[10px] text-ink-700">P{item.phase}</span>
                    </span>
                  ),
                )}
              </nav>
              <div className="ml-auto rounded-full border border-ink-700 px-3 py-1 text-xs text-ink-300">
                Phase 2 — Production
              </div>
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
