#!/usr/bin/with-contenv bashio

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_USERNAME=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")

bashio::log.info "Starting ULED-AT MQTT Bridge"
bashio::log.info "  MQTT broker: ${MQTT_HOST}:${MQTT_PORT}"

exec /opt/venv/bin/python /app/uled_bridge.py
