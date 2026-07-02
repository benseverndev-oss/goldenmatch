[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.1s: promote_negative_evidence done
[controller.run n_rows=2] t=0.1s: promote_negative_evidence done
[controller.run n_rows=2] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.1s: entering iteration loop
[controller.run n_rows=2] t=0.1s: iter 0 start
[controller.run n_rows=2] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.1s: entering iteration loop
[controller.run n_rows=2] t=0.1s: iter 0 start
[controller.run n_rows=2] t=0.3s: iter 0 _run_pipeline_sample done in 0.2s
[controller.run n_rows=2] t=0.3s: iter 0 _run_pipeline_sample done in 0.2s
[controller.run n_rows=2] t=0.3s: iter 1 start
[controller.run n_rows=2] t=0.3s: iter 1 start
[controller.run n_rows=2] t=0.3s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.3s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.1s: entering iteration loop
[controller.run n_rows=2] t=0.1s: iter 0 start
[controller.run n_rows=2] t=0.8s: iter 0 _run_pipeline_sample done in 0.8s
[controller.run n_rows=2] t=0.8s: iter 1 start
[controller.run n_rows=2] t=0.9s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.9s: iter 2 start
[controller.run n_rows=2] t=0.9s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.9s: iter 3 start
[controller.run n_rows=2] t=0.9s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.1s: entering iteration loop
[controller.run n_rows=2] t=0.1s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[score_buckets] entry: prepared_df.height=2 n_buckets=68
[score_buckets] t=0.00s: slim projection 8 -> 7 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 2 start
[controller.run n_rows=3] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: bucket_score done in 0.01s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.02s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.02s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: bucket_score done in 0.01s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 2 start
[controller.run n_rows=3] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 3 start
[controller.run n_rows=3] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 2 start
[controller.run n_rows=3] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 2 start
[controller.run n_rows=3] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=3] t=0.0s: entry
[controller.run n_rows=3] t=0.0s: _initial_config done
[controller.run n_rows=3] t=0.0s: _take_sample done (sample.height=3)
[controller.run n_rows=3] t=0.0s: compute_column_priors done
[controller.run n_rows=3] t=0.0s: promote_negative_evidence done
[controller.run n_rows=3] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=3] t=0.0s: entering iteration loop
[controller.run n_rows=3] t=0.0s: iter 0 start
[controller.run n_rows=3] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 1 start
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 2 start
[controller.run n_rows=3] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=3] t=0.1s: iter 3 start
[controller.run n_rows=3] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=3] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=3 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.1s: _initial_config done
[controller.run n_rows=2] t=0.1s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.1s: compute_column_priors done
[controller.run n_rows=2] t=0.1s: promote_negative_evidence done
[controller.run n_rows=2] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.1s: entering iteration loop
[controller.run n_rows=2] t=0.1s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.1s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.2s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=4] t=0.0s: entry
[controller.run n_rows=4] t=0.0s: _initial_config done
[controller.run n_rows=4] t=0.0s: _take_sample done (sample.height=4)
[controller.run n_rows=4] t=0.0s: compute_column_priors done
[controller.run n_rows=4] t=0.0s: promote_negative_evidence done
[controller.run n_rows=4] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=4] t=0.0s: entering iteration loop
[controller.run n_rows=4] t=0.0s: iter 0 start
[controller.run n_rows=4] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.1s: iter 1 start
[controller.run n_rows=4] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=4] t=0.1s: iter 2 start
[controller.run n_rows=4] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=4 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 1 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 2 start
[controller.run n_rows=2] t=0.1s: iter 2 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 3 start
[controller.run n_rows=2] t=0.1s: iter 3 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 1 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[score_buckets] t=0.01s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.01s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.01s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.01s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.0s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.01s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[controller.run n_rows=2] t=0.0s: entry
[controller.run n_rows=2] t=0.0s: _initial_config done
[controller.run n_rows=2] t=0.0s: _take_sample done (sample.height=2)
[controller.run n_rows=2] t=0.0s: compute_column_priors done
[controller.run n_rows=2] t=0.0s: promote_negative_evidence done
[controller.run n_rows=2] t=0.0s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=2] t=0.0s: entering iteration loop
[controller.run n_rows=2] t=0.0s: iter 0 start
[controller.run n_rows=2] t=0.1s: iter 0 _run_pipeline_sample done in 0.0s
[controller.run n_rows=2] t=0.1s: iter 1 start
[controller.run n_rows=2] t=0.1s: iter 1 _run_pipeline_sample done in 0.0s
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
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: small-block fast path (height=2 < n_buckets=68); skipping hash+partition_by. See #422.
[score_buckets] t=0.00s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.00s: bucket_score done in 0.00s, 0 blocks, 0 pairs
[suggest] accepted=True flags=CorpusFlags(expect_homographs=True, has_known_schema=True, relation_vocab=('acquired', 'located in', 'part of', 'works at', 'authored'), entity_type_vocab=('person', 'organization', 'concept')) baseline_F1=0.7368 proposed_F1=0.6885 winner_xdoc=name_ci_type canon=True

# SP-C Suggester Smoke (homograph engineered)

- accepted: `True`  flags: `CorpusFlags(expect_homographs=True, has_known_schema=True, relation_vocab=('acquired', 'located in', 'part of', 'works at', 'authored'), entity_type_vocab=('person', 'organization', 'concept'))`
- baseline relational F1: 0.7368 (P=0.8153)
- proposed relational F1: 0.6885 (P=0.9323)
- winner: xdoc_key=`name_ci_type` entity_type_canon=True



===== RESULTS_MD =====
# SP-C Suggester Smoke (homograph engineered)

- accepted: `True`  flags: `CorpusFlags(expect_homographs=True, has_known_schema=True, relation_vocab=('acquired', 'located in', 'part of', 'works at', 'authored'), entity_type_vocab=('person', 'organization', 'concept'))`
- baseline relational F1: 0.7368 (P=0.8153)
- proposed relational F1: 0.6885 (P=0.9323)
- winner: xdoc_key=`name_ci_type` entity_type_canon=True
