# Govee ArtNet LAN Bridge

This project bridges ArtNet input to Govee LAN devices. The device sender now uses a global token-bucket limiter to smooth outgoing traffic.

## Rate limiting

* The limiter refills at `rate_limit_per_second` tokens per second and holds up to `rate_limit_burst` tokens.
* Each payload send consumes one token. If no tokens are available, sends wait until enough tokens accumulate before proceeding.
* Burst capacity allows short spikes up to the configured bucket size before throttling engages.

### Visibility

* Gauge `govee_rate_limit_tokens` reports current available tokens.
* Counter `govee_rate_limit_waits_total{scope="global"}` increments whenever a send waits for the limiter.
* The sender logs when throttling delays a payload, including the estimated wait duration and remaining tokens.
