# PantryOS update supervisor (ADR 0018 §6). One code path for every install
# type: polls the release manifest, defers per policy, then runs the update
# transaction (pre-update dump -> pull -> recreate -> health poll -> rollback
# on failure). The desktop launcher is just a remote control for this.
FROM docker:29-cli

RUN apk add --no-cache bash curl jq

COPY update.sh /usr/local/bin/update.sh
RUN chmod +x /usr/local/bin/update.sh

ENTRYPOINT ["/usr/local/bin/update.sh"]
