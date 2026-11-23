#!/bin/sh -eu
# Submit the triaged revision under the identity the master reports to Patchwork.
jq --arg owner "${SASHING_OWNER:?}" \
    '{jsonrpc: "2.0", method: "force", id: 1, params: {
      builderNames: ["series"],
      msgid: ((.cover_letter // .patches[0]).msgid | ltrimstr("<") | rtrimstr(">")),
      username: $owner, reason: "patchwork series \(.id)"}}' "$PW_TRIAGE_BOT_JSON" |
    curl -fsS -H 'Content-Type: application/json' -d @- http://localhost:8010/api/v2/forceschedulers/series |
    jq -e '.result // false'
