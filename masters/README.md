# Champions-only fixed source masters

The team for this snapshot is `Pokemon_Champions_MC_team_2026-10-03.csv`.

- At match launch, the **four** live resources named in `AGENTS.md` must be actually loaded/read/hash-frozen into memory.
- Other game-specific masters are available for pregame/offline construction, not automatic guessed effect filling in live battle.
- `Pokemon_Champions_effect_rules_catalog_seed_v0_2.jsonl` contains 892 records; only a tiny verified subset is structured and most `UNMAPPED` entries cannot run automatically. Do not treat `verification_status` as current-game activation confirmation.
- No real-screen OCR message dictionary has yet been verified. Codex should create a separate candidate dictionary with provenance and observed examples only.
- Never modify the original files silently: version derived resources and log provenance.

## 2026-10-25 Web research candidates (NOT live-ready)

- `Pokemon_Champions_mechanics_web_evidence_2026-10-25.jsonl`: 41 scoped Champions-specific web research candidates with URLs, source status, checked date and `live_autoload_allowed=false`.
- `Pokemon_Champions_type_chart_candidate_2026-10-25.json`: 18x18 standard-type **candidate**, stripped of an unsupported Stellar dimension found in the upstream community dataset; not game-observed or verified.
- `Pokemon_Champions_damage_reference_cases_candidate_2026-10-25.json`: four worked examples from a nonofficial Champions calculator, *not measured in game*.
- Gap audit, source-by-source discussion and validation priorities: `docs/CHAMPIONS_MASTER_GAP_RESEARCH_2026-10-25.md`.
- These three files do **not** alter original masters, type effectiveness, four-file preloading or LIVE tactical judgments. Human in-game corroboration and condition-specific simulator regression must precede promotion.
