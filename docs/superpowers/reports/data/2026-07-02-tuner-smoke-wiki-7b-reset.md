[controller.run n_rows=61] t=0.0s: entry
[controller.run n_rows=61] t=0.0s: _initial_config done
[controller.run n_rows=61] t=0.0s: _take_sample done (sample.height=61)
[controller.run n_rows=61] t=0.0s: compute_column_priors done
[controller.run n_rows=61] t=0.0s: promote_negative_evidence done
[controller.run n_rows=61] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=61] t=0.1s: entering iteration loop
[controller.run n_rows=61] t=0.1s: iter 0 start
[controller.run n_rows=61] t=1.0s: iter 0 _run_pipeline_sample done in 0.9s
[controller.run n_rows=61] t=1.0s: iter 1 start
[controller.run n_rows=61] t=1.1s: iter 1 _run_pipeline_sample done in 0.1s
[controller.run n_rows=61] t=1.1s: iter 2 start
[controller.run n_rows=61] t=1.1s: iter 2 _run_pipeline_sample done in 0.1s
[controller.run n_rows=61] t=1.2s: iter 3 start
[controller.run n_rows=61] t=1.2s: iter 3 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=61 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=61 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 7 blocks, 13 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=61 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.01s, 10 blocks, 40 pairs
[controller.run n_rows=78] t=0.0s: entry
[controller.run n_rows=78] t=0.0s: _initial_config done
[controller.run n_rows=78] t=0.0s: _take_sample done (sample.height=78)
[controller.run n_rows=78] t=0.0s: compute_column_priors done
[controller.run n_rows=78] t=0.0s: promote_negative_evidence done
[controller.run n_rows=78] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=78] t=0.0s: entering iteration loop
[controller.run n_rows=78] t=0.0s: iter 0 start
[controller.run n_rows=78] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=78] t=0.1s: iter 1 start
[controller.run n_rows=78] t=0.2s: iter 1 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=78 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.00s: partition_by(bucket) in 0.00s -> 44 buckets
[score_buckets] t=0.00s: 44 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.03s: bucket_score done in 0.02s, 2 blocks, 4 pairs
[score_buckets] t=0.03s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.03s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.03s: partition_by(bucket) in 0.00s -> 33 buckets
[score_buckets] t=0.03s: 33 non-empty buckets ready for scoring
[score_buckets] t=0.03s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.09s: bucket_score done in 0.06s, 22 blocks, 33 pairs
[controller.run n_rows=6] t=0.0s: entry
[controller.run n_rows=6] t=0.0s: _initial_config done
[controller.run n_rows=6] t=0.0s: _take_sample done (sample.height=6)
[controller.run n_rows=6] t=0.0s: compute_column_priors done
[controller.run n_rows=6] t=0.0s: promote_negative_evidence done
[controller.run n_rows=6] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=6] t=0.0s: entering iteration loop
[controller.run n_rows=6] t=0.0s: iter 0 start
[controller.run n_rows=6] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=6] t=0.0s: iter 1 start
[controller.run n_rows=6] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=6 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.0s: iter 1 start
[controller.run n_rows=4] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=4 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=6] t=0.0s: entry
[controller.run n_rows=6] t=0.0s: _initial_config done
[controller.run n_rows=6] t=0.0s: _take_sample done (sample.height=6)
[controller.run n_rows=6] t=0.0s: compute_column_priors done
[controller.run n_rows=6] t=0.0s: promote_negative_evidence done
[controller.run n_rows=6] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=6] t=0.0s: entering iteration loop
[controller.run n_rows=6] t=0.0s: iter 0 start
[controller.run n_rows=6] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=6] t=0.0s: iter 1 start
[controller.run n_rows=6] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=6 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.0s: iter 1 start
[controller.run n_rows=4] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=4 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=5] t=0.0s: entry
[controller.run n_rows=5] t=0.0s: _initial_config done
[controller.run n_rows=5] t=0.0s: _take_sample done (sample.height=5)
[controller.run n_rows=5] t=0.0s: compute_column_priors done
[controller.run n_rows=5] t=0.0s: promote_negative_evidence done
[controller.run n_rows=5] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=5] t=0.0s: entering iteration loop
[controller.run n_rows=5] t=0.0s: iter 0 start
[controller.run n_rows=5] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=5] t=0.0s: iter 1 start
[controller.run n_rows=5] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=5 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=5 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=5 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=52] t=0.0s: entry
[controller.run n_rows=52] t=0.0s: _initial_config done
[controller.run n_rows=52] t=0.0s: _take_sample done (sample.height=52)
[controller.run n_rows=52] t=0.0s: compute_column_priors done
[controller.run n_rows=52] t=0.0s: promote_negative_evidence done
[controller.run n_rows=52] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=52] t=0.0s: entering iteration loop
[controller.run n_rows=52] t=0.0s: iter 0 start
[controller.run n_rows=52] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=52] t=0.1s: iter 1 start
[controller.run n_rows=52] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=52 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=52 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=52 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 5 blocks, 9 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.0s: iter 1 start
[controller.run n_rows=4] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=4 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=47] t=0.0s: entry
[controller.run n_rows=47] t=0.0s: _initial_config done
[controller.run n_rows=47] t=0.0s: _take_sample done (sample.height=47)
[controller.run n_rows=47] t=0.0s: compute_column_priors done
[controller.run n_rows=47] t=0.0s: promote_negative_evidence done
[controller.run n_rows=47] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=47] t=0.0s: entering iteration loop
[controller.run n_rows=47] t=0.0s: iter 0 start
[controller.run n_rows=47] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=47] t=0.1s: iter 1 start
[controller.run n_rows=47] t=0.1s: iter 1 _run_pipeline_sample done in 0.1s
[controller.run n_rows=47] t=0.1s: iter 2 start
[controller.run n_rows=47] t=0.2s: iter 2 _run_pipeline_sample done in 0.1s
[controller.run n_rows=47] t=0.2s: iter 3 start
[controller.run n_rows=47] t=0.2s: iter 3 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=47 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=47 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 2 blocks, 18 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=47 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.02s, 16 blocks, 4 pairs
[controller.run n_rows=50] t=0.0s: entry
[controller.run n_rows=50] t=0.0s: _initial_config done
[controller.run n_rows=50] t=0.0s: _take_sample done (sample.height=50)
[controller.run n_rows=50] t=0.0s: compute_column_priors done
[controller.run n_rows=50] t=0.0s: promote_negative_evidence done
[controller.run n_rows=50] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=50] t=0.0s: entering iteration loop
[controller.run n_rows=50] t=0.0s: iter 0 start
[controller.run n_rows=50] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=50] t=0.1s: iter 1 start
[controller.run n_rows=50] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=50 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=50 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 4 blocks, 6 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=50 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.01s, 12 blocks, 20 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.0s: iter 1 start
[controller.run n_rows=3] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=3 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=9] t=0.0s: entry
[controller.run n_rows=9] t=0.0s: _initial_config done
[controller.run n_rows=9] t=0.0s: _take_sample done (sample.height=9)
[controller.run n_rows=9] t=0.0s: compute_column_priors done
[controller.run n_rows=9] t=0.0s: promote_negative_evidence done
[controller.run n_rows=9] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=9] t=0.0s: entering iteration loop
[controller.run n_rows=9] t=0.0s: iter 0 start
[controller.run n_rows=9] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=9] t=0.0s: iter 1 start
[controller.run n_rows=9] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=9 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=9 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=9 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=27] t=0.0s: entry
[controller.run n_rows=27] t=0.0s: _initial_config done
[controller.run n_rows=27] t=0.0s: _take_sample done (sample.height=27)
[controller.run n_rows=27] t=0.0s: compute_column_priors done
[controller.run n_rows=27] t=0.0s: promote_negative_evidence done
[controller.run n_rows=27] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=27] t=0.0s: entering iteration loop
[controller.run n_rows=27] t=0.0s: iter 0 start
[controller.run n_rows=27] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.0s: iter 1 start
[controller.run n_rows=27] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.1s: iter 2 start
[controller.run n_rows=27] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.1s: iter 3 start
[controller.run n_rows=27] t=0.2s: iter 3 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=27 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=27 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 2 blocks, 1 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=27 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 6 blocks, 9 pairs
[controller.run n_rows=11] t=0.0s: entry
[controller.run n_rows=11] t=0.0s: _initial_config done
[controller.run n_rows=11] t=0.0s: _take_sample done (sample.height=11)
[controller.run n_rows=11] t=0.0s: compute_column_priors done
[controller.run n_rows=11] t=0.0s: promote_negative_evidence done
[controller.run n_rows=11] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=11] t=0.0s: entering iteration loop
[controller.run n_rows=11] t=0.0s: iter 0 start
[controller.run n_rows=11] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=11] t=0.0s: iter 1 start
[controller.run n_rows=11] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=11 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=11 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=11 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 2 blocks, 7 pairs
[controller.run n_rows=22] t=0.0s: entry
[controller.run n_rows=22] t=0.0s: _initial_config done
[controller.run n_rows=22] t=0.0s: _take_sample done (sample.height=22)
[controller.run n_rows=22] t=0.0s: compute_column_priors done
[controller.run n_rows=22] t=0.0s: promote_negative_evidence done
[controller.run n_rows=22] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=22] t=0.0s: entering iteration loop
[controller.run n_rows=22] t=0.0s: iter 0 start
[controller.run n_rows=22] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.0s: iter 1 start
[controller.run n_rows=22] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.1s: iter 2 start
[controller.run n_rows=22] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.1s: iter 3 start
[controller.run n_rows=22] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=22 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=22 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=22 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=61] t=0.0s: entry
[controller.run n_rows=61] t=0.0s: _initial_config done
[controller.run n_rows=61] t=0.0s: _take_sample done (sample.height=61)
[controller.run n_rows=61] t=0.0s: compute_column_priors done
[controller.run n_rows=61] t=0.0s: promote_negative_evidence done
[controller.run n_rows=61] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=61] t=0.0s: entering iteration loop
[controller.run n_rows=61] t=0.0s: iter 0 start
[controller.run n_rows=61] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=61] t=0.1s: iter 1 start
[controller.run n_rows=61] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=61] t=0.1s: iter 2 start
[controller.run n_rows=61] t=0.2s: iter 2 _run_pipeline_sample done in 0.1s
[controller.run n_rows=61] t=0.2s: iter 3 start
[controller.run n_rows=61] t=0.2s: iter 3 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=61 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=61 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 6 blocks, 11 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=61 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.01s, 10 blocks, 45 pairs
[controller.run n_rows=77] t=0.0s: entry
[controller.run n_rows=77] t=0.0s: _initial_config done
[controller.run n_rows=77] t=0.0s: _take_sample done (sample.height=77)
[controller.run n_rows=77] t=0.0s: compute_column_priors done
[controller.run n_rows=77] t=0.0s: promote_negative_evidence done
[controller.run n_rows=77] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=77] t=0.0s: entering iteration loop
[controller.run n_rows=77] t=0.0s: iter 0 start
[controller.run n_rows=77] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=77] t=0.1s: iter 1 start
[controller.run n_rows=77] t=0.2s: iter 1 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=77 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.00s: partition_by(bucket) in 0.00s -> 46 buckets
[score_buckets] t=0.00s: 46 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.03s: bucket_score done in 0.02s, 1 blocks, 3 pairs
[score_buckets] t=0.03s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.03s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.03s: partition_by(bucket) in 0.00s -> 31 buckets
[score_buckets] t=0.03s: 31 non-empty buckets ready for scoring
[score_buckets] t=0.03s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.09s: bucket_score done in 0.05s, 21 blocks, 33 pairs
[controller.run n_rows=6] t=0.0s: entry
[controller.run n_rows=6] t=0.0s: _initial_config done
[controller.run n_rows=6] t=0.0s: _take_sample done (sample.height=6)
[controller.run n_rows=6] t=0.0s: compute_column_priors done
[controller.run n_rows=6] t=0.0s: promote_negative_evidence done
[controller.run n_rows=6] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=6] t=0.0s: entering iteration loop
[controller.run n_rows=6] t=0.0s: iter 0 start
[controller.run n_rows=6] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=6] t=0.0s: iter 1 start
[controller.run n_rows=6] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=6 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.0s: iter 1 start
[controller.run n_rows=4] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=4 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=6] t=0.0s: entry
[controller.run n_rows=6] t=0.0s: _initial_config done
[controller.run n_rows=6] t=0.0s: _take_sample done (sample.height=6)
[controller.run n_rows=6] t=0.0s: compute_column_priors done
[controller.run n_rows=6] t=0.0s: promote_negative_evidence done
[controller.run n_rows=6] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=6] t=0.0s: entering iteration loop
[controller.run n_rows=6] t=0.0s: iter 0 start
[controller.run n_rows=6] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=6] t=0.0s: iter 1 start
[controller.run n_rows=6] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=6 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=6 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.0s: iter 1 start
[controller.run n_rows=3] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=3 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=5] t=0.0s: entry
[controller.run n_rows=5] t=0.0s: _initial_config done
[controller.run n_rows=5] t=0.0s: _take_sample done (sample.height=5)
[controller.run n_rows=5] t=0.0s: compute_column_priors done
[controller.run n_rows=5] t=0.0s: promote_negative_evidence done
[controller.run n_rows=5] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=5] t=0.0s: entering iteration loop
[controller.run n_rows=5] t=0.0s: iter 0 start
[controller.run n_rows=5] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=5] t=0.0s: iter 1 start
[controller.run n_rows=5] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=5 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=5 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=5 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=72] t=0.0s: entry
[controller.run n_rows=72] t=0.0s: _initial_config done
[controller.run n_rows=72] t=0.0s: _take_sample done (sample.height=72)
[controller.run n_rows=72] t=0.0s: compute_column_priors done
[controller.run n_rows=72] t=0.0s: promote_negative_evidence done
[controller.run n_rows=72] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=72] t=0.0s: entering iteration loop
[controller.run n_rows=72] t=0.0s: iter 0 start
[controller.run n_rows=72] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=72] t=0.1s: iter 1 start
[controller.run n_rows=72] t=0.1s: iter 1 _run_pipeline_sample done in 0.1s
[controller.run n_rows=72] t=0.1s: iter 2 start
[controller.run n_rows=72] t=0.2s: iter 2 _run_pipeline_sample done in 0.1s
[controller.run n_rows=72] t=0.2s: iter 3 start
[controller.run n_rows=72] t=0.3s: iter 3 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=72 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.00s: partition_by(bucket) in 0.00s -> 44 buckets
[score_buckets] t=0.00s: 44 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.02s, 3 blocks, 2 pairs
[score_buckets] t=0.02s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.02s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.03s: partition_by(bucket) in 0.00s -> 35 buckets
[score_buckets] t=0.03s: 35 non-empty buckets ready for scoring
[score_buckets] t=0.03s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.07s: bucket_score done in 0.04s, 17 blocks, 24 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.0s: iter 1 start
[controller.run n_rows=4] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=4 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=48] t=0.0s: entry
[controller.run n_rows=48] t=0.0s: _initial_config done
[controller.run n_rows=48] t=0.0s: _take_sample done (sample.height=48)
[controller.run n_rows=48] t=0.0s: compute_column_priors done
[controller.run n_rows=48] t=0.0s: promote_negative_evidence done
[controller.run n_rows=48] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=48] t=0.0s: entering iteration loop
[controller.run n_rows=48] t=0.0s: iter 0 start
[controller.run n_rows=48] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=48] t=0.1s: iter 1 start
[controller.run n_rows=48] t=0.1s: iter 1 _run_pipeline_sample done in 0.1s
[controller.run n_rows=48] t=0.1s: iter 2 start
[controller.run n_rows=48] t=0.2s: iter 2 _run_pipeline_sample done in 0.1s
[controller.run n_rows=48] t=0.2s: iter 3 start
[controller.run n_rows=48] t=0.2s: iter 3 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=48 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=48 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 2 blocks, 18 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=48 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.02s, 16 blocks, 4 pairs
[controller.run n_rows=49] t=0.0s: entry
[controller.run n_rows=49] t=0.0s: _initial_config done
[controller.run n_rows=49] t=0.0s: _take_sample done (sample.height=49)
[controller.run n_rows=49] t=0.0s: compute_column_priors done
[controller.run n_rows=49] t=0.0s: promote_negative_evidence done
[controller.run n_rows=49] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=49] t=0.0s: entering iteration loop
[controller.run n_rows=49] t=0.0s: iter 0 start
[controller.run n_rows=49] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=49] t=0.1s: iter 1 start
[controller.run n_rows=49] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=49 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=49 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 4 blocks, 6 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=49 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.01s, 12 blocks, 19 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.0s: iter 1 start
[controller.run n_rows=3] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=3 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=9] t=0.0s: entry
[controller.run n_rows=9] t=0.0s: _initial_config done
[controller.run n_rows=9] t=0.0s: _take_sample done (sample.height=9)
[controller.run n_rows=9] t=0.0s: compute_column_priors done
[controller.run n_rows=9] t=0.0s: promote_negative_evidence done
[controller.run n_rows=9] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=9] t=0.0s: entering iteration loop
[controller.run n_rows=9] t=0.0s: iter 0 start
[controller.run n_rows=9] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=9] t=0.0s: iter 1 start
[controller.run n_rows=9] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=9 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=9 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=9 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.0s: iter 1 start
[controller.run n_rows=2] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=27] t=0.0s: entry
[controller.run n_rows=27] t=0.0s: _initial_config done
[controller.run n_rows=27] t=0.0s: _take_sample done (sample.height=27)
[controller.run n_rows=27] t=0.0s: compute_column_priors done
[controller.run n_rows=27] t=0.0s: promote_negative_evidence done
[controller.run n_rows=27] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=27] t=0.0s: entering iteration loop
[controller.run n_rows=27] t=0.0s: iter 0 start
[controller.run n_rows=27] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.0s: iter 1 start
[controller.run n_rows=27] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.1s: iter 2 start
[controller.run n_rows=27] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=27] t=0.1s: iter 3 start
[controller.run n_rows=27] t=0.2s: iter 3 _run_pipeline_sample done in 0.1s
[score_buckets] entry: prepared_df.height=27 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=27 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.01s, 3 blocks, 2 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=27 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.01s, 6 blocks, 8 pairs
[controller.run n_rows=8] t=0.0s: entry
[controller.run n_rows=8] t=0.0s: _initial_config done
[controller.run n_rows=8] t=0.0s: _take_sample done (sample.height=8)
[controller.run n_rows=8] t=0.0s: compute_column_priors done
[controller.run n_rows=8] t=0.0s: promote_negative_evidence done
[controller.run n_rows=8] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=8] t=0.0s: entering iteration loop
[controller.run n_rows=8] t=0.0s: iter 0 start
[controller.run n_rows=8] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=8] t=0.0s: iter 1 start
[controller.run n_rows=8] t=0.0s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=8 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=8 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=8 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 6 pairs
[controller.run n_rows=22] t=0.0s: entry
[controller.run n_rows=22] t=0.0s: _initial_config done
[controller.run n_rows=22] t=0.0s: _take_sample done (sample.height=22)
[controller.run n_rows=22] t=0.0s: compute_column_priors done
[controller.run n_rows=22] t=0.0s: promote_negative_evidence done
[controller.run n_rows=22] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=22] t=0.0s: entering iteration loop
[controller.run n_rows=22] t=0.0s: iter 0 start
[controller.run n_rows=22] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.0s: iter 1 start
[controller.run n_rows=22] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.1s: iter 2 start
[controller.run n_rows=22] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=22] t=0.1s: iter 3 start
[controller.run n_rows=22] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=22 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=22 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 2 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=22 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[substrate-tuner] stopped=passed rounds=1 init_xdoc=name_ci init_chunk=True
[tuner-round 0] xdoc=name_ci chunk=True schema_canon=False | presence=0.9077 relational_F1=0.6844 R=0.5202 P=1.0000 conn_edge_recall=1.0000 comp=15 | gate_passed=True failing_axis=None escalated_to=None
[substrate-tuner] WINNER xdoc=name_ci chunk=True schema_canon=False | FULL presence=0.9077 relational_F1=0.6667 R=0.5000 P=1.0000 conn_edge_recall=1.0000 comp=16

# Substrate Staged-Tuner Smoke (wiki)

- stopped_reason: `passed`  rounds: 1
- WINNER config: xdoc_key=`name_ci` chunk_extract=True schema_canon=False
- FULL scorecard: presence=0.9077 relational_F1=0.6667 R=0.5000 P=1.0000 conn_edge_recall=1.0000 comp=16

| round | xdoc_key | chunk | presence | relational_F1 | gate_passed | failing_axis | escalated_to |
|---|---|---|---|---|---|---|---|
| 0 | name_ci | True | 0.9077 | 0.6844 | True | None | None |


===== RESULTS_MD =====
# Substrate Staged-Tuner Smoke (wiki)

- stopped_reason: `passed`  rounds: 1
- WINNER config: xdoc_key=`name_ci` chunk_extract=True schema_canon=False
- FULL scorecard: presence=0.9077 relational_F1=0.6667 R=0.5000 P=1.0000 conn_edge_recall=1.0000 comp=16

| round | xdoc_key | chunk | presence | relational_F1 | gate_passed | failing_axis | escalated_to |
|---|---|---|---|---|---|---|---|
| 0 | name_ci | True | 0.9077 | 0.6844 | True | None | None |
