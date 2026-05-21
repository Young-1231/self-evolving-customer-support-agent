# APR-CS ablation (synthetic NimbusFlow batch)
- N tickets: **8**, |tip pool|: **10** (7 useful + 3 distractor)
- off-anchor pass^1 (no tips): **0.500**
- base pass^1 (all tips):     **0.750**

## Per-condition metrics
| condition | resolution | keypoint_cov | intervention | avg_tips |
|---|---:|---:|---:|---:|
| all | 0.750 | 0.859 | 0.250 | 10.00 |
| top_k_relevance | 1.000 | 0.911 | 0.000 | 3.50 |
| cf_weighted | 1.000 | 0.930 | 0.000 | 2.75 |
| conf_gated | 1.000 | 0.943 | 0.000 | 2.25 |

## Counterfactual Delta_i (sorted)
- `billing_refund_proration`: Delta = +0.1250
- `login_lockout`: Delta = +0.1250
- `api_rate_limit`: Delta = +0.1250
- `data_export`: Delta = +0.1250
- `twofa_recovery`: Delta = +0.1250
- `plan_change_proration`: Delta = +0.0000
- `integration_reauth`: Delta = +0.0000
- `noise_chargeback`: Delta = -0.1250
- `noise_voice_call`: Delta = -0.1250
- `noise_seat_upgrade`: Delta = -0.1250
