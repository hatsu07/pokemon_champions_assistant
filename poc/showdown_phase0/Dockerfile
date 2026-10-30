# Isolated Phase 0 experiment. Never shipped with the Windows app.
FROM node:22.18.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG SHOWDOWN_SHA=ccfaa20f32af363569cd21a9d501b2bc1ded26b1
RUN test "$SHOWDOWN_SHA" = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1"
RUN mkdir -p /opt/pokemon-showdown \
    && git -C /opt/pokemon-showdown init \
    && git -C /opt/pokemon-showdown remote add origin https://github.com/smogon/pokemon-showdown.git \
    && git -C /opt/pokemon-showdown fetch --depth=1 origin "$SHOWDOWN_SHA" \
    && git -C /opt/pokemon-showdown checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/pokemon-showdown rev-parse HEAD)" = "$SHOWDOWN_SHA"
WORKDIR /opt/pokemon-showdown
RUN npm ci --no-audit --no-fund && npm run build
RUN test -e LICENSE && test -e dist/sim/index.js
COPY poc/showdown_phase0/ /opt/phase0/
COPY masters/Pokemon_Champions_MC_move_master.csv /opt/phase0/masters/
COPY masters/Pokemon_Champions_MC_usable_pokemon.csv /opt/phase0/masters/
COPY masters/Pokemon_Champions_MC_pokemon_learnsets_linked.csv /opt/phase0/masters/
COPY masters/Pokemon_Champions_MC_usable_held_items.csv /opt/phase0/masters/
ENV SHOWDOWN_ROOT=/opt/pokemon-showdown
WORKDIR /opt/phase0
CMD ["node", "run.cjs"]
