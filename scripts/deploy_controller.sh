#!/usr/bin/env bash
# Copy STP_NCAA_Basketball controller files into Captivate DataControllers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/captivate_controller/STP_NCAA_Basketball"
DEST="${STP_NCAA_DATACONTROLLERS:-/Users/Shared/NewBlue/LiveEngine/DataControllers/STP_NCAA_Basketball}"

if [[ ! -d "$SRC" ]]; then
  echo "Missing controller source: $SRC" >&2
  exit 1
fi
if [[ ! -d "$(dirname "$DEST")" ]]; then
  echo "Missing DataControllers parent: $(dirname "$DEST")" >&2
  exit 1
fi

mkdir -p "$DEST"
rsync -a --delete \
  --exclude 'STP_NCAA_Basketball-Controller.nbpak' \
  --exclude 'content.manifest' \
  --exclude '.DS_Store' \
  "$SRC/" "$DEST/"

echo "Deployed controller $(tr -d '[:space:]' < "$DEST/VERSION") → $DEST"
