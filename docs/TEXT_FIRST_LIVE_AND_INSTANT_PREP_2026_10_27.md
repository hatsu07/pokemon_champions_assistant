# Text-first live judgment and zero-network PREP (2026-10-27)

## Reason for change

Real BlueStacks evidence showed 18 cached matchup rows immediately available, but an independent AI role classification could remain unanswered 92 seconds. Turn actions also sent 1–3 screenshots per decision, while forced switches were often classified as `DETAIL_OVERLAY`.

## Live PREP

A frozen exact-team match cache is read in memory after displaying the selected three and lead. PREP reports how many of 18 pair cells contain prior evidence. Missing matchups stay unknown. `preserve`, `stopper`, `closer` are optional if already obtained; no PREP network worker is started to fill them. The player's live decisions still consider all three and the opponent's win path. No outside source, database, or new rules are queried mid-match.

## Battle decisions

For a locally classified `TURN_ACTION` / `SWITCH_SELECTION` where both currently active names are known from match state, the model receives **text only**: current actives, selected three, living bench candidates, registered moves, HP/evidence, battlefield, latest OCR candidates, and only the cached pairs for the current opponent. Model output is validated against registered moves, choice lock, selected bench and known faint status. In a party menu without visual confirmation, the action basis is explicitly *selected bench visibility unverified*. No model result is evidence that the player executed a move.

A current image is used only when essential active identity is missing. Selection-preview recognition is also image-based when the opponent's six are not locally established. Timing and `images_sent` should be checked separately from locally captured frames.

A direct SWITCH recommendation is reused while the player opens the party screen in the same decision window. A subsequent battle resolution, detected faint/replacement, or genuinely new turn is a separate decision. An old move must never be reused as a forced-switch decision.

## Result and learning

The local event OCR candidate stream checks only explicit full-match ending phrases; faint messages are not result candidates. A dedicated model call receives only bounded OCR **text**, with strict `WIN/LOSS/UNKNOWN` output and an exact quote from the messages as evidence. Unclear/contradictory content remains unconfirmed. Player-confirmed WIN/LOSS is available when OCR cannot decide.

The existing Desktop `MATCH_END` callback then saves the match to SQLite, schedules the postmatch review, and at the next match's offline asset preparation publishes bounded *unverified* hints to the derived team matrix. Neither model hypotheses nor an unverified OCR string can unilaterally certify a matchup, KO or safe switch; new match master files remain frozen in battle.

## Verification and remaining E2E boundaries

Tests cover cached PREP without model traffic, text-only action/switch/result schemas, rejection of a move during forced switch, deduped normal switch, player result override and grounded OCR quote checks. Real-game OCR recognition, text-only latency, game win/lose phrasing, selected bench presence, visual classifier quality and end-to-end BlueStacks timing remain empirical E2E acceptance gates. Incomplete OCR may require the single-image exception.
