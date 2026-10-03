# Reference example

All development and validation uses one dataset:

**1 kg `market for wheat grain, feed` | GLO** — ecoinvent 3.9.1 cut-off.

## Why this one

The user asked for "1 kg of GLO wheat". ecoinvent 3.9.1 has no conventional
`market for wheat grain` at GLO (only RoW and national markets). The GLO
wheat markets are `market for wheat grain, feed` and `market for wheat grain,
organic`; the conventional feed market was chosen. It is a good test case: a
market (transport + regional suppliers), a production step, then a
world-wide mix of national wheat productions with fertilisers, machinery,
electricity — and loops.

## Default method

EF v3.1 climate change, GWP100 (kg CO2-Eq). The cached model also holds the
24 other EF v3.1 categories and IPCC 2021 GWP100.

## Result (EF v3.1 climate change)

Total: **0.788 kg CO2-Eq / kg**. First levels, nodes > 2 %:

```
100.0%  0.788   1 kg wheat grain, feed | market for wheat grain, feed | GLO
 95.0%  0.748     1 kg wheat grain, feed | wheat grain, feed production | RoW
 90.2%  0.711       0.995 kg wheat grain | market for wheat grain | RoW
 49.1%  0.387         0.582 kg | wheat production | RoW
 10.8%  0.0853        0.138 kg | wheat production | IN
  8.6%  0.0678        0.0402 kg | wheat production | AU
  8.4%  0.0659        0.0911 kg | wheat production | US
  ...
  2.3%  0.018       0.019 kWh | market group for electricity, low voltage | RAS
```

Matches `bw2calc` to machine precision (see [calculation-engine.md](calculation-engine.md)).
