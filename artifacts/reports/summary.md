# Phase 2 - experiment summary

| experiment | model | precision | recall | F1 | FPR | PR-AUC | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| exp1_baseline_sentinelops | robust_zscore | 0.750 | 0.667 | 0.706 | 0.133 | 0.819 | event recall 0.67, delay 0.0s |
| exp2_isolation_forest_sentinelops | isolation_forest | 0.692 | 1.000 | 0.818 | 0.267 | 0.700 | event recall 1.00, delay 0.0s |
| exp3_comparison_sentinelops | robust_zscore | 0.750 | 0.667 | 0.706 | 0.133 | 0.819 | event recall 0.67, delay 0.0s |
| exp3_comparison_sentinelops | isolation_forest | 0.692 | 1.000 | 0.818 | 0.267 | 0.700 | event recall 1.00, delay 0.0s |
| exp3_comparison_sentinelops | random_forest_supervised | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | event recall 1.00, delay 0.0s |
| exp4_heldout_fault_sentinelops | robust_zscore | 0.850 | 0.944 | 0.895 | 0.083 | 0.777 | event recall 1.00, delay 0.0s |
| exp4_heldout_fault_sentinelops | isolation_forest | 0.750 | 1.000 | 0.857 | 0.167 | 0.735 | event recall 1.00, delay 0.0s |
| exp4_heldout_fault_sentinelops | random_forest_supervised | 1.000 | 0.500 | 0.667 | 0.000 | 0.703 | event recall 0.50, delay 0.0s |
| exp5_nab_realknowncause | robust_zscore | 0.181 | 0.198 | 0.182 | 0.112 | 0.201 | macro-avg over series |
| exp5_nab_realknowncause | isolation_forest | 0.170 | 0.123 | 0.142 | 0.083 | 0.182 | macro-avg over series |
| exp6_nab_realawscloudwatch | robust_zscore | 0.138 | 0.426 | 0.143 | 0.345 | 0.115 | macro-avg over series |
| exp6_nab_realawscloudwatch | isolation_forest | 0.109 | 0.331 | 0.116 | 0.288 | 0.129 | macro-avg over series |
