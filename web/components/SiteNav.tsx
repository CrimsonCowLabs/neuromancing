"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// The global app nav — hidden on the landing page ("/"), which renders its own
// immersive cyberpunk nav as part of the design.
export default function SiteNav() {
  const path = usePathname();
  // The landing page and the construct pages render their own immersive nav.
  if (path === "/" || path.startsWith("/grid/")) return null;
  return (
    <div className="nav">
      <Link href="/" className="brand">
        NEUROMANCING <span>// ai traders, live</span>
      </Link>
      <span className="disclaimer">Simulated · entertainment only · not financial advice</span>
    </div>
  );
}
