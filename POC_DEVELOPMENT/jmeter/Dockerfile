FROM eclipse-temurin:17-jre-jammy

ARG JMETER_VERSION=5.6.3
ARG JMETER_PROMETHEUS_PLUGIN_VERSION=0.7.1
ENV JMETER_HOME=/opt/apache-jmeter-${JMETER_VERSION}
ENV PATH="${JMETER_HOME}/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" -o /tmp/jmeter.tgz \
    && tar -xzf /tmp/jmeter.tgz -C /opt \
    && rm /tmp/jmeter.tgz

RUN curl -fsSL "https://github.com/johrstrom/jmeter-prometheus-plugin/releases/download/${JMETER_PROMETHEUS_PLUGIN_VERSION}/jmeter-prometheus-plugin-${JMETER_PROMETHEUS_PLUGIN_VERSION}.jar" \
    -o "${JMETER_HOME}/lib/ext/jmeter-prometheus-plugin-${JMETER_PROMETHEUS_PLUGIN_VERSION}.jar"

COPY jmeter.properties.append /tmp/jmeter.properties.append
RUN cat /tmp/jmeter.properties.append >> "${JMETER_HOME}/bin/jmeter.properties" \
    && rm /tmp/jmeter.properties.append

EXPOSE 9270
WORKDIR /test-plans
ENTRYPOINT ["jmeter"]
CMD ["--help"]
