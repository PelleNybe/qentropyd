# Operator Manual

## Installation

```bash
pip install .
sudo mkdir -p /etc/qentropyd
sudo cp systemd/qentropyd.example.yaml /etc/qentropyd/config.yaml
sudo cp systemd/qentropyd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qentropyd.service
```

## CLI Commands

- `qentropyd -c /etc/qentropyd/config.yaml validate-config` — Validate YAML configuration and exit.
- `qentropyd -c /etc/qentropyd/config.yaml test-sources [--json]` — Probe all configured quantum and hardware sources.
- `qentropyd -c /etc/qentropyd/config.yaml self-check --bytes 4096 [--json]` — Run NIST SP 800-90B + 800-22 self-check.
- `qentropyd -c /etc/qentropyd/config.yaml run` — Run the daemon in the foreground.

## Signals

- `SIGTERM` / `SIGINT`: Graceful shutdown after closing source clients and syslog.
- `SIGHUP`: Hot-reload YAML configuration on the next cycle without restarting the service (`systemctl reload qentropyd`).

## Monitoring (Prometheus & Journald)

Scrape `http://127.0.0.1:9108/metrics` for:
- `qentropyd_batches_accepted_total`
- `qentropyd_batches_rejected_total{reason=...}`
- `qentropyd_nist_p_value{test_name=...}`
- `qentropyd_kernel_entropy_avail_bits`
- `qentropyd_circuit_breaker_open`

Inspect logs via:
```bash
journalctl -u qentropyd -f
```

## Grafana Dashboard & Alert Rules

- **Grafana:** Import `deploy/grafana/qentropyd-dashboard.json` in Grafana (**Dashboards -> New -> Import**) and select your Prometheus datasource.
- **Prometheus Alerts:** Load `deploy/prometheus/qentropyd-alerts.yml` in your Prometheus `rule_files:` configuration to alert on `QEntropydCircuitBreakerOpen`, `QEntropydBatchRejectionSpike`, and `QEntropydSourceErrors`.
