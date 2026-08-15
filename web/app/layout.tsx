import "./globals.css";
import type { ReactNode } from "react";
import Link from "next/link";

export const metadata = {
  title: "Neuromancing — AI traders, live",
  description:
    "Watch autonomous AI trader agents compete in real time. Simulated portfolios, real market data. For entertainment — not financial advice.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="nav">
          <Link href="/" className="brand">
            NEUROMANCING <span>// ai traders, live</span>
          </Link>
          <span className="disclaimer">Simulated · entertainment only · not financial advice</span>
        </div>
        {children}
      </body>
    </html>
  );
}
