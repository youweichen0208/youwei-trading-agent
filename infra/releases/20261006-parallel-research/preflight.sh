set -eu
root=/root/parallel-research-20261006
assistant=ghcr.io/youweichen0208/trading-assistant-candidates@sha256:4c2c8798568775a3795465dd0d409de63e2465055a2e781f05c59c9cc0726c88
webui=ghcr.io/youweichen0208/youwei-webui@sha256:2c5ffcdf5648ebae62c968115fdf1447b8134efa917ffd7951f63b7f0a6166fd
docker pull "$webui" > "$root/webui-pull.log" 2>&1
python3 "$root/platform/ops/verify_webui_upgrade.py" --archive "$root/backups/chat/daily/chat-backup-20261005-235746/openwebui-data.tar.gz" --webui-image "$webui" > "$root/webui-restore.log" 2>&1
/root/youwei-trading-agent/.venv/bin/python "$root/platform/ops/verify_assistant_webui.py" --assistant-image "$assistant" --webui-image "$webui" > "$root/cross-service.log" 2>&1
