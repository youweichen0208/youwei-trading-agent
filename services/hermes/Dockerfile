# Build context is services/hermes, never the repository or a credential directory.
# This is the upstream release manifest pin; the resulting custom image needs
# its own digest and real deployment evidence before Worker registration.
FROM nousresearch/hermes-agent@sha256:276d44685c65c0cb2a25f190b63c65c8906f9dc76486fb1c05ae71b5826bda08
USER root
# A refused home identity must prevent CMD execution. Upstream s6's default
# merely logs failing cont-init scripts and otherwise continues startup.
ENV S6_BEHAVIOUR_IF_STAGE2_FAILS=2
COPY upstream.json apply_patch.py /opt/alpha-build/
RUN python3 /opt/alpha-build/apply_patch.py /opt/hermes
# The host ProfileSupervisor owns start/recovery. Preserve upstream s6 init,
# privilege drop and signal handling, but never register auto-restarting native
# gateway slots from persisted gateway_state.json (including after a crash).
RUN cmp /opt/hermes/docker/cont-init.d/02-reconcile-profiles /etc/cont-init.d/02-reconcile-profiles
COPY container-init.sh /etc/cont-init.d/02-reconcile-profiles
RUN chmod 755 /etc/cont-init.d/02-reconcile-profiles
COPY alpha_hermes /opt/hermes/alpha_hermes
COPY plugin /opt/hermes/plugins/alpha-lab
RUN chmod -R a-w /opt/hermes/alpha_hermes /opt/hermes/plugins/alpha-lab && \
    python3 -m compileall -q /opt/hermes/alpha_hermes
LABEL org.opencontainers.image.title="Alpha Lab Hermes runtime" \
      lab.alpha.hermes.commit="3b421578588243f920886e6fe79a6faa87ea026e"
