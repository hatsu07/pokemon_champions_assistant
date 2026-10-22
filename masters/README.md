# Champions-only fixed source masters

The team for this snapshot is `Pokemon_Champions_MC_team_2026-10-03.csv`.

- At match launch, the **four** live resources named in `AGENTS.md` must be actually loaded/read/hash-frozen into memory.
- Other game-specific masters are available for pregame/offline construction, not automatic guessed effect filling in live battle.
- `Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl` contains 892 records; only a tiny verified subset is structured and most `UNMAPPED` entries cannot run automatically. Do not treat `verification_status` as current-game activation confirmation.
- No real-screen OCR message dictionary has yet been verified. Codex should create a separate candidate dictionary with provenance and observed examples only.
- Never modify the original files silently: version derived resources and log provenance.
