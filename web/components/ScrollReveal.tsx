"use client";

import { useEffect } from "react";

// Reveal-on-scroll for the landing page: observes every [data-rv] inside .nm-home
// and flips data-in="1" when it enters the viewport. The pre-reveal hidden state is
// gated on html.js (see globals.css), so this only ever *reveals* — and it fails safe:
// if IntersectionObserver is missing, reduced-motion is set, or anything throws, it
// reveals everything at once rather than leaving content stuck invisible.
export default function ScrollReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>(".nm-home [data-rv]"));
    const revealAll = () => els.forEach((el) => el.setAttribute("data-in", "1"));
    try {
      if (
        matchMedia("(prefers-reduced-motion: reduce)").matches ||
        !("IntersectionObserver" in window)
      ) {
        revealAll();
        return;
      }
      const io = new IntersectionObserver(
        (entries) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              e.target.setAttribute("data-in", "1");
              io.unobserve(e.target);
            }
          });
        },
        { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
      );
      els.forEach((el) => io.observe(el));
      return () => io.disconnect();
    } catch {
      revealAll();
    }
  }, []);
  return null;
}
