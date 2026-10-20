#!/usr/bin/env bash
# Deploy to a free Hugging Face Docker Space.
# Usage: HF_TOKEN=hf_xxx scripts/deploy-hf.sh [space-name]   (token needs "write" access)
set -euo pipefail

SPACE_NAME="${1:-spotter-truck-routing}"
: "${HF_TOKEN:?Set HF_TOKEN to a Hugging Face write token}"
API=https://huggingface.co/api
auth=(-H "Authorization: Bearer $HF_TOKEN")

HF_USER=$(curl -fsS "${auth[@]}" "$API/whoami-v2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
SPACE="$HF_USER/$SPACE_NAME"
echo "Deploying to https://huggingface.co/spaces/$SPACE"

# Create the Space (409 = already exists, fine).
curl -sS -o /dev/null -w "create: %{http_code}\n" "${auth[@]}" -H "Content-Type: application/json" \
  -d "{\"type\":\"space\",\"name\":\"$SPACE_NAME\",\"sdk\":\"docker\",\"private\":false}" "$API/repos/create"

# Runtime settings: a generated secret key and the hf.space host.
SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
curl -fsS -o /dev/null "${auth[@]}" -H "Content-Type: application/json" \
  -d "{\"key\":\"DJANGO_SECRET_KEY\",\"value\":\"$SECRET\"}" "$API/spaces/$SPACE/secrets"
curl -fsS -o /dev/null "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"key":"DJANGO_ALLOWED_HOSTS","value":".hf.space"}' "$API/spaces/$SPACE/variables"

# Spaces read their config from README front matter; push a snapshot of HEAD with it added.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git archive HEAD | tar -x -C "$WORK"
{
  printf -- '---\ntitle: Weather-Aware Truck Routing\nemoji: 🚚\ncolorFrom: green\ncolorTo: blue\nsdk: docker\napp_port: 8000\npinned: false\n---\n\n'
  cat "$WORK/README.md"
} > "$WORK/README.tmp" && mv "$WORK/README.tmp" "$WORK/README.md"
rm -rf "$WORK/docs" # binary screenshots would need Git LFS on the Hub; the app does not use them
(
  cd "$WORK"
  git init -q -b main
  git add -A
  git -c user.name=deploy -c user.email=deploy@localhost commit -qm "Deploy $(git -C "$OLDPWD" rev-parse --short HEAD)"
  git push -f "https://$HF_USER:$HF_TOKEN@huggingface.co/spaces/$SPACE" main 2>&1 | grep -v "$HF_TOKEN" || true
)
echo "Live (after the build, about 3-5 min): https://${SPACE/\//-}.hf.space"
