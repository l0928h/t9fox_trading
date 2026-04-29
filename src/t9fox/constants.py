"""Defaults aligned with common Taiwan retail assumptions; override in backtest config."""

# Broker round-trip fee as fraction per side (e.g. 0.001425 = 0.1425% each leg).
DEFAULT_COMMISSION_RATE = 0.001425

# Securities transaction tax on sells only (general stocks; ETFs often differ).
DEFAULT_SELL_TAX_RATE = 0.003
