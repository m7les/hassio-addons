#!/usr/bin/with-contenv bashio

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASS=$(bashio::services mqtt "password")

export INTERACTOR_URL=$(bashio::config 'interactor_url')
export POLL_INTERVAL=$(bashio::config 'poll_interval')
export BACKLIGHT_POLL_INTERVAL=$(bashio::config 'backlight_poll_interval')
export REDISCOVERY_INTERVAL=$(bashio::config 'rediscovery_interval')
export MQTT_TOPIC_PREFIX=$(bashio::config 'mqtt_topic_prefix')
export LOG_LEVEL=$(bashio::config 'log_level')

bashio::log.info "Starting LIFX Switch Bridge"
bashio::log.info "  Interactor: ${INTERACTOR_URL}"
bashio::log.info "  MQTT broker: ${MQTT_HOST}:${MQTT_PORT}"
bashio::log.info "  Topic prefix: ${MQTT_TOPIC_PREFIX}"
bashio::log.info "  Poll: ${POLL_INTERVAL}s (backlight ${BACKLIGHT_POLL_INTERVAL}s, rediscovery ${REDISCOVERY_INTERVAL}s)"

exec /opt/venv/bin/python /lifx_switch_bridge.py
