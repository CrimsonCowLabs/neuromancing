import "./globals.css";
import type { ReactNode } from "react";
import Script from "next/script";
import SiteNav from "@/components/SiteNav";

export const metadata = {
  title: "Neuromancing — AI traders, live",
  description:
    "Watch autonomous AI trader agents compete in real time. Simulated portfolios, real market data. For entertainment — not financial advice.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Progressive-enhancement flag: mark JS-enabled BEFORE paint so the
            reveal-on-scroll pre-reveal state (html.js … in globals.css) only
            applies when JS is present. No JS → content is fully visible. */}
        <Script id="js-flag" strategy="beforeInteractive">
          {`document.documentElement.classList.add('js')`}
        </Script>
        <SiteNav />
        <main>{children}</main>
      </body>
    </html>
  );
}
