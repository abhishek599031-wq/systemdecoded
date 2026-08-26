import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SystemDecoded Studio",
  description: "Complex Technology. Decoded. — autonomous content operations",
};

const NAV = [
  { label: "Dashboard", href: "/", active: true },
  { label: "Projects", href: "#", active: false, phase: 2 },
  { label: "Review", href: "#", active: false, phase: 2 },
  { label: "Ideas", href: "#", active: false, phase: 4 },
  { label: "Channel", href: "#", active: false, phase: 1 },
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
                {NAV.map((item) => (
                  <span
                    key={item.label}
                    title={item.active ? undefined : `Arrives in Phase ${item.phase}`}
                    className={
                      item.active
                        ? "rounded-md bg-ink-800 px-3 py-1.5 text-ink-100"
                        : "cursor-not-allowed rounded-md px-3 py-1.5 text-ink-500"
                    }
                  >
                    {item.label}
                    {!item.active && (
                      <span className="ml-1.5 text-[10px] text-ink-700">P{item.phase}</span>
                    )}
                  </span>
                ))}
              </nav>
              <div className="ml-auto rounded-full border border-ink-700 px-3 py-1 text-xs text-ink-300">
                Phase 0 — Foundation
              </div>
            </div>
          </header>
          <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
