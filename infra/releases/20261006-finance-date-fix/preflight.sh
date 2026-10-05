set -eu
root=/root/finance-date-fix-20261006
image=ghcr.io/youweichen0208/trading-assistant-candidates@sha256:73e28618c2fe5a67c05098e855a668e808a8e336ca498339d23af0a865315ad7
for i in $(seq 1 180); do
 if docker image inspect "$image" >/dev/null 2>&1; then break; fi
 sleep 5
done
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/root/finance-date-fix-20261006/image.json')
p.write_text(json.dumps({'image':'ghcr.io/youweichen0208/trading-assistant-candidates@sha256:73e28618c2fe5a67c05098e855a668e808a8e336ca498339d23af0a865315ad7','revision':'9b38567be3f401730bc5a150e83bbb5b92f6c58f','build':'https://github.com/youweichen0208/trading-assistant/actions/runs/37387250503'},indent=2)+'\n')
PY
python3 "$root/platform/ops/backup/assistant_image.py" verify --image "$image" --archive "$root/backups/chat/daily/chat-backup-20261005-231021/hermes-data.tar.gz" > "$root/candidate-restore.log" 2>&1
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,size=128m -e HOME=/tmp/finance -e HERMES_HOME=/tmp/finance/profile -v "$root/verify_live.py:/verify_live.py:ro" --entrypoint python "$image" /verify_live.py > "$root/live-preflight.json" 2> "$root/live-preflight.err"
/root/youwei-trading-agent/.venv/bin/python "$root/platform/ops/verify_assistant_webui.py" --assistant-image "$image" --webui-image ghcr.io/youweichen0208/youwei-webui@sha256:203a484b5dfa6237a98536b2fbeb650f910fa7635bdb34a771cf785237b8e345 > "$root/cross-service.log" 2>&1
