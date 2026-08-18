"""Options v1 foundation — pure pricing/vol/risk math for the synthetic options table
and the options-aware backtester. No I/O, no service deps; safe to import anywhere.

See ~/.claude/plans (Options v1) for the design + the modeling caveats (BS is European,
flat-vol; the vol surface + variance-risk premium are the fidelity levers)."""
