[controller.run n_rows=278] t=0.0s: entry
[controller.run n_rows=278] t=0.0s: _initial_config done
[controller.run n_rows=278] t=0.0s: _take_sample done (sample.height=278)
[controller.run n_rows=278] t=0.1s: compute_column_priors done
[controller.run n_rows=278] t=0.1s: promote_negative_evidence done
[controller.run n_rows=278] t=0.1s: estimate_sparse_match_signal done (exact_cols=0)
[controller.run n_rows=278] t=0.1s: entering iteration loop
[controller.run n_rows=278] t=0.1s: iter 0 start
[controller.run n_rows=278] t=1.0s: iter 0 _run_pipeline_sample done in 1.0s
[controller.run n_rows=278] t=1.0s: iter 1 start
[controller.run n_rows=278] t=1.3s: iter 1 _run_pipeline_sample done in 0.2s
[score_buckets] entry: prepared_df.height=278 n_buckets=68
[score_buckets] t=0.00s: slim projection 6 -> 6 cols
[score_buckets._resolve_fast_path] declined: _resolve_score_pair_callable('ensemble') is None
[score_buckets] t=0.00s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.00s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.00s: partition_by(bucket) in 0.00s -> 32 buckets
[score_buckets] t=0.00s: 32 non-empty buckets ready for scoring
[score_buckets] t=0.00s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.13s: bucket_score done in 0.12s, 41 blocks, 956 pairs
[score_buckets] t=0.13s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.13s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.13s: partition_by(bucket) in 0.00s -> 30 buckets
[score_buckets] t=0.13s: 30 non-empty buckets ready for scoring
[score_buckets] t=0.13s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.24s: bucket_score done in 0.11s, 38 blocks, 1851 pairs
[score_buckets] t=0.24s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.24s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.24s: partition_by(bucket) in 0.00s -> 29 buckets
[score_buckets] t=0.24s: 29 non-empty buckets ready for scoring
[score_buckets] t=0.24s: starting bucket_score with max_workers=17 path=find_fuzzy_matches
[score_buckets] t=0.35s: bucket_score done in 0.11s, 37 blocks, 1142 pairs
[score_buckets] t=0.35s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.35s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.35s: partition_by(bucket) in 0.00s -> 1 buckets
[score_buckets] t=0.35s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.35s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.37s: bucket_score done in 0.02s, 1 blocks, 4835 pairs
[score_buckets] t=0.37s: keyed (with_columns key_expr) in 0.00s
[score_buckets] t=0.37s: bucketed (hash %% N) in 0.00s
[score_buckets] t=0.37s: partition_by(bucket) in 0.00s -> 1 buckets
[score_buckets] t=0.37s: 1 non-empty buckets ready for scoring
[score_buckets] t=0.37s: starting bucket_score with max_workers=1 path=find_fuzzy_matches
[score_buckets] t=0.39s: bucket_score done in 0.01s, 1 blocks, 4835 pairs
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
[score_buckets] t=0.00s: slim projection 8 -> 6 cols
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
[score_buckets] t=0.00s: bucket_score done in 0.00s, 1 blocks, 0 pairs
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
[substrate] ambiguity=0.0: relational: F1=0.7823 R=0.7426 P=0.8264 | connectivity: edge_recall=0.8849 | coherence: comp=1 largest=1.000 | ER-F1(A)=0.5855 gap=-0.1968 provenance=1.000

# Substrate-Quality Scoreboard (engineered)

| ambiguity | ER-F1(A) | relational_F1(B) | relational_P | relational_R | edge_recall | A-B gap | components | largest-frac | provenance |
|---|---|---|---|---|---|---|---|---|---|
| 0.0 | 0.5855 | 0.7823 | 0.8264 | 0.7426 | 0.8849 | -0.1968 | 1 | 1.0000 | 1.0000 |

A = resolver in isolation (clean gold surfaces); B = end-to-end build. **A-B gap = extraction-induced fragmentation.** On the engineered corpus the doc-id oracle IS the presence signal, so only the RELATIONAL (B) + connectivity(edge_recall) axes are reported here; the presence/connectivity-coverage split is a wiki-path (alias-bearing) metric.



===== RESULTS_MD =====
# Substrate-Quality Scoreboard (engineered)

| ambiguity | ER-F1(A) | relational_F1(B) | relational_P | relational_R | edge_recall | A-B gap | components | largest-frac | provenance |
|---|---|---|---|---|---|---|---|---|---|
| 0.0 | 0.5855 | 0.7823 | 0.8264 | 0.7426 | 0.8849 | -0.1968 | 1 | 1.0000 | 1.0000 |

A = resolver in isolation (clean gold surfaces); B = end-to-end build. **A-B gap = extraction-induced fragmentation.** On the engineered corpus the doc-id oracle IS the presence signal, so only the RELATIONAL (B) + connectivity(edge_recall) axes are reported here; the presence/connectivity-coverage split is a wiki-path (alias-bearing) metric.
