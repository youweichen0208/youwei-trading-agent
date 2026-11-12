#!/command/with-contenv sh
# Alpha has one application-managed gateway per container. The upstream
# profile-gateways scanner may watch this empty tmpfs directory, but no native
# slots are registered or restored from home. The container CMD owns the run.
set -eu
/opt/hermes/.venv/bin/python -m alpha_hermes.home_init
if [ -L /run/hermes-service ]; then
    echo 'Alpha Profile refused a symlinked service directory' >&2
    exit 1
fi
mkdir -p /run/hermes-service
chown hermes:hermes /run/hermes-service
chmod 0755 /run/hermes-service
