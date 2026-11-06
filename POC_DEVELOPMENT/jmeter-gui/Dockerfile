ARG JMETER_IMAGE=pythonicshahdev/boomi-loadtest-jmeter:latest
FROM ${JMETER_IMAGE}

ARG JMETER_STUDIO_VERSION=1.0.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb fluxbox x11vnc novnc websockify supervisor \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/QAInsights/jmeter-studio/releases/download/v${JMETER_STUDIO_VERSION}/jmeter-studio-oss-${JMETER_STUDIO_VERSION}.jar" \
    -o "${JMETER_HOME}/lib/ext/jmeter-studio-oss-${JMETER_STUDIO_VERSION}.jar"

COPY supervisord.conf /etc/supervisor/conf.d/jmeter-gui.conf

EXPOSE 6080
WORKDIR /test-plans
ENTRYPOINT ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
