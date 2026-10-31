-- Spike S4: every query `edr report` ran (QUERY_HISTORY_BY_SESSION), in order.
-- The per-run schema is replaced by <ELEMENTARY_SCHEMA>. Captured by tests/spikes/test_s4_edr_contract.py.

-- [1] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [2] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [3] CREATE_TABLE_AS_SELECT
create or replace temporary table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.ordered_test_results__tmp_20261031111404531353 
        as (
            
        with test_results as (
            
    with elementary_test_results as (
        select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.elementary_test_results
        
            where datediff(
        day,
        cast(detected_at as timestamp),
        
  current_timestamp::timestamp

        ) < 7
        
    ),

    dbt_run_results as (
        select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_run_results
        
            where datediff(
        day,
        cast(execute_completed_at as timestamp),
        
  current_timestamp::timestamp

        ) < 7
        
    ),

    dbt_tests as (
        select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_tests
    ),

    dbt_models as (
        select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models
    ),

    dbt_sources as (
        select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
    ),

    dbt_artifacts as (
        select unique_id, meta, tags, owner from dbt_models
        union all
        select unique_id, meta, tags, owner from dbt_sources
    ),

    first_time_test_occurred as (
        select 
            min(detected_at) as first_time_occurred,
            test_unique_id
        from elementary_test_results
        group by test_unique_id
    )

    select
        elementary_test_results.id,
        
        case
            when elementary_test_results.test_type = 'schema_change' then elementary_test_results.test_unique_id
            
            when dbt_tests.short_name = 'dimension_anomalies' then elementary_test_results.test_unique_id
            else coalesce(elementary_test_results.test_unique_id, 'None') || '.' || coalesce(nullif(elementary_test_results.column_name, ''), 'None') || '.' || coalesce(elementary_test_results.test_sub_type, 'None')
        end as elementary_unique_id,
        elementary_test_results.invocation_id,
        elementary_test_results.data_issue_id,
        elementary_test_results.test_execution_id,
        elementary_test_results.test_unique_id,
        elementary_test_results.model_unique_id,
        elementary_test_results.detected_at,
        elementary_test_results.database_name,
        elementary_test_results.schema_name,
        elementary_test_results.table_name,
        elementary_test_results.column_name,
        elementary_test_results.test_type,
        elementary_test_results.test_sub_type,
        elementary_test_results.test_results_description,
        elementary_test_results.owners,
        elementary_test_results.tags,
        elementary_test_results.test_results_query,
        elementary_test_results.other,
        case
            when dbt_tests.short_name is not null then dbt_tests.short_name
            else elementary_test_results.test_name
        end as test_name,
        elementary_test_results.test_params,
        elementary_test_results.severity,
        elementary_test_results.status,
        
        dbt_tests.short_name,
        elementary_test_results.test_alias,
        elementary_test_results.failures,
        elementary_test_results.result_rows as result_rows,
        dbt_tests.original_path,
        dbt_tests.meta,
        dbt_tests.description as test_description,
        dbt_tests.package_name,
        dbt_tests.tags as test_tags,
        dbt_artifacts.meta as model_meta,
        dbt_artifacts.tags as model_tags,
        dbt_artifacts.owner as model_owner,
        first_occurred.first_time_occurred as test_created_at,
        dbt_run_results.execution_time as execution_time
    from elementary_test_results
    join dbt_tests on elementary_test_results.test_unique_id = dbt_tests.unique_id
    left join first_time_test_occurred first_occurred on elementary_test_results.test_unique_id = first_occurred.test_unique_id
    left join dbt_artifacts on elementary_test_results.model_unique_id = dbt_artifacts.unique_id
    left join dbt_run_results on elementary_test_results.test_execution_id = dbt_run_results.model_execution_id

        ),

        ordered_test_results as (
            select
                *,
                datediff(
        day,
        cast(detected_at as timestamp),
        
  current_timestamp::timestamp

        ) as days_diff,
                
                row_number() over (partition by elementary_unique_id order by cast(detected_at as timestamp) desc) as invocations_rank_index
            from test_results
        )

        select
            test_results.id,
            test_results.invocation_id,
            test_results.test_execution_id,
            test_results.model_unique_id,
            test_results.test_unique_id,
            test_results.elementary_unique_id,
            cast(test_results.detected_at as timestamp) as detected_at,
            test_results.database_name,
            test_results.schema_name,
            test_results.table_name,
            test_results.column_name,
            test_results.test_type,
            test_results.test_sub_type,
            test_results.test_results_description,
            test_results.test_description,
            test_results.original_path,
            test_results.package_name,
            test_results.owners,
            test_results.model_owner,
            test_results.tags,
            test_results.test_tags,
            test_results.model_tags,
            test_results.meta,
            test_results.model_meta,
            case when test_results.invocations_rank_index = 1 then test_results.test_results_query else NULL end as test_results_query,
            test_results.other,
            test_results.test_name,
            test_results.test_params,
            test_results.severity,
            test_results.status,
            test_results.execution_time,
            test_results.days_diff,
            test_results.invocations_rank_index,
            test_results.failures,
            test_results.result_rows
        from ordered_test_results as test_results
        where test_results.invocations_rank_index <= 720
        order by test_results.elementary_unique_id, test_results.invocations_rank_index desc
        )
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "b70be92e-65c9-4736-815e-a69e5e126aeb", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [4] SELECT
select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.ordered_test_results__tmp_20261031111404531353
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "b70be92e-65c9-4736-815e-a69e5e126aeb", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [5] SELECT
select
    elementary_test_results_id,
    result_row
  from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.test_result_rows
  where datediff(
        day,
        cast(detected_at as timestamp),
        
  current_timestamp::timestamp

        ) < 7
  
    and elementary_test_results_id in (
        select distinct id
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.ordered_test_results__tmp_20261031111404531353
        where invocations_rank_index = 1
    )
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "b70be92e-65c9-4736-815e-a69e5e126aeb", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [6] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_source_freshness_results
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [7] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [8] SELECT
with dbt_source_freshness_results as (
            select
                *,
                
    case
        when status = 'error' then 'fail'
        when status = 'runtime error' then 'error'
        else status
    end as normalized_status
,
                rank() over (partition by unique_id order by generated_at desc) as invocations_rank_index
            from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_source_freshness_results
            
                where datediff(
        day,
        cast(generated_at as timestamp),
        
  current_timestamp::timestamp

        ) < 7
            
        ),

        dbt_sources as (
            select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
        )

        select
            freshness.source_freshness_execution_id,
            freshness.unique_id,
            freshness.max_loaded_at,
            freshness.generated_at,
            freshness.status as original_status,
            freshness.normalized_status,
            
            
                freshness.error_after,
                freshness.warn_after,
                freshness.filter,
            
            freshness.max_loaded_at_time_ago_in_s,
            freshness.snapshotted_at,
            freshness.invocation_id,
            freshness.error,
            freshness.invocations_rank_index,
            sources.database_name,
            sources.schema_name,
            sources.source_name,
            sources.name as table_name,
            sources.loaded_at_field,
            sources.meta,
            sources.tags,
            sources.owner as owners,
            sources.freshness_error_after as source_freshness_error_after,
            sources.freshness_warn_after as source_freshness_warn_after,
            sources.freshness_filter as source_freshness_filter,
            sources.relation_name,
            
            
                sources.freshness_description
            
        from dbt_source_freshness_results freshness
        join dbt_sources sources on freshness.unique_id = sources.unique_id
        where invocations_rank_index <= 720
        order by freshness.unique_id, invocations_rank_index desc
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "3643fab5-9e33-4304-b289-bf83a38990ab", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [9] SELECT
with dbt_artifacts_seeds as (
          select
            name,
            unique_id,
            database_name,
            schema_name,
            case when alias is not null then alias
            else name end as table_name,
            owner as owners,
            tags,
            package_name,
            description,
            original_path as full_path
          from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_seeds
        )

        select * from dbt_artifacts_seeds
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "4c89e0b5-f518-41ba-b818-97cd7b8b8992", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [10] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [11] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [12] SELECT
with dbt_artifacts_snapshots as (
                select 
                  name,
                  case when alias is not null then alias
                  else name end as table_name,
                  unique_id,
                  owner as owners,
                  tags,
                  package_name,
                  description,
                  meta,
                  materialization,
                  database_name,
                  schema_name,
                  depends_on_macros,
                  depends_on_nodes,
                  original_path as full_path,
                  path,
                  patch_path,
                  generated_at,
                  unique_key,
                  incremental_strategy
                from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_snapshots
              )
            select * from dbt_artifacts_snapshots
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "7c95f00b-ba18-4431-a138-6c054851d111", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [13] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [14] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [15] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [16] SELECT
with dbt_artifacts_models as (
                select
                  name,
                  unique_id,
                  database_name,
                  schema_name,
                  case when alias is not null then alias
                  else name end as table_name,
                  owner as owners,
                  tags,
                  package_name,
                  description,
                  materialization,
                  
                  
                    patch_path,
                  
                  original_path as full_path,
                  meta
                from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models
                
                  where package_name != 'elementary'
                
              )

             select * from dbt_artifacts_models
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "dff15bb5-85de-4754-a8a9-40e5530abf91", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [17] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [18] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [19] SELECT
with dbt_artifacts_sources as (
                select
                  name,
                  source_name,
                  unique_id,
                  database_name,
                  schema_name,
                  identifier as table_name,
                  owner as owners,
                  tags,
                  package_name,
                  description,
                  original_path as full_path,
                  meta
                from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
              )

            select * from dbt_artifacts_sources
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "a33be868-6cab-4457-a894-1a09c3edf84b", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [20] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [21] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [22] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_exposures
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [23] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_exposures
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [24] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_exposures
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [25] SELECT
with dbt_artifacts_exposures as (
                select
                  name,
                  
                    label,
                  
                  unique_id,
                  url,
                  type,
                  maturity,
                  owner_email,
                  owner_name as owners,
                  tags,
                  package_name,
                  description,
                  meta,
                  original_path as full_path,
                  
                    raw_queries,
                  
                  depends_on_nodes,
                  
                    depends_on_columns
                  

                from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_exposures
              )

             select * from dbt_artifacts_exposures
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "d79fc9fe-7c83-4833-b481-cb02601ae7f6", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [26] SELECT
select
          unique_id,
          name,
          original_path,
          package_name,
          tags
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_tests
        where type = 'singular'
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "70e3cd3e-6226-41e4-ae8a-033824fa9013", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [27] SELECT
with model_runs as (
            select
                *,
                row_number() over (partition by unique_id order by generated_at desc) as invocations_rank_index
            from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.model_run_results
        )

        select
            unique_id,
            invocation_id,
            name,
            schema_name as schema,
            status,
            case
                when status != 'success' then 0
                else round(cast(execution_time as numeric(28,6)), 1)
            end as execution_time,
            full_refresh,
            materialization,
            case when invocations_rank_index = 1 then compiled_code else NULL end as compiled_code,
            generated_at
        from model_runs
        where datediff(
        day,
        cast(generated_at as timestamp),
        
  current_timestamp::timestamp

        ) < 7
        
          and unique_id not like 'model.elementary.%'
        
        order by generated_at
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "7ac7394b-79e3-49f2-9f73-17fe1b700ae1", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [28] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [29] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [30] SELECT
with dbt_tests as (
                select
                    parent_model_unique_id,
                    test_column_name
                from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_tests
            )

            select 
                parent_model_unique_id as model_unique_id,
                SUM(
                    case 
                        when test_column_name is not null then 1
                        else 0
                        end
                ) as column_tests,
                 SUM(
                    case
                        when test_column_name is null then 1
                        else 0
                        end 
                )as table_tests
            from dbt_tests
            group by parent_model_unique_id
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "e94f9a17-5b4a-4a12-a343-1d67b836ee44", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [31] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [32] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [33] SELECT
with tests as (
                select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_tests
            ),
            
            test_results as (
                select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.elementary_test_results
            ),

            models as (
                select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models
            ),
            
            sources as (
                select * from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
            ),

            nodes as (
                select
                    models.unique_id as unique_id,
                    models.name as name,
                    models.meta,
                    models.tags,
                    'model' as type
                from models
                union all
                select
                    sources.unique_id as unique_id,
                    sources.name as name,
                    sources.meta,
                    sources.tags,
                    'source' as type
                from sources
            ),

            test_results_times as (
                select
                    test_unique_id,
                    max(detected_at) as last_detected_at,
                    min(detected_at) as first_detected_at
                from test_results
                group by test_unique_id
            ),

            last_test_results as (
                select
                    test_results.test_unique_id,
                    test_results.detected_at,
                    test_results.status,
                    test_results.test_type,
                    test_results.test_sub_type
                from test_results
                join test_results_times 
                    on test_results_times.test_unique_id = test_results.test_unique_id
                    and test_results_times.last_detected_at = test_results.detected_at
            )

            select
                tests.unique_id as unique_id,
                tests.parent_model_unique_id as model_unique_id,
                tests.database_name,
                tests.schema_name,
                nodes.name as model_name,
                tests.test_column_name as column_name,
                tests.name as name,
                tests.description,
                tests.package_name,
                tests.original_path,
                tests.test_params,
                tests.meta as meta,
                nodes.meta as model_meta,
                tests.tags,
                nodes.tags as model_tags,
                tests.type as type,
                last_test_results.test_type,
                last_test_results.test_sub_type,
                test_results_times.first_detected_at as created_at,
                last_test_results.detected_at as latest_run_time,
                last_test_results.status as latest_run_status
            from tests
            left join test_results_times on tests.unique_id = test_results_times.test_unique_id
            left join last_test_results on tests.unique_id = last_test_results.test_unique_id
            left join nodes on tests.parent_model_unique_id = nodes.unique_id
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "4d3b5ccf-0161-4457-83e9-2daae87bcd41", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [34] SHOW
show objects in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
    limit 10000
    

/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [35] SHOW
show user functions in ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [36] SELECT
select
            unique_id,
            null as depends_on_nodes,
            null as materialization,
            'seed' as type
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_seeds
        union all
        select
            unique_id,
            depends_on_nodes,
            materialization,
            'snapshot' as type
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_snapshots
        union all
        select
            unique_id,
            depends_on_nodes,
            materialization,
            'model' as type
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models
        
            where package_name != 'elementary'
        
        union all
        select
            unique_id,
            null as depends_on_nodes,
            null as materialization,
            'source' as type
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_sources
        union all
        select
            unique_id,
            depends_on_nodes,
            null as materialization,
            'exposure' as type
        from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_exposures
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "2b09ff58-1d63-48b3-bec7-21f6f5d370e9", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [37] SELECT
with ordered_run_results as (
      select
        run_results.unique_id,
        run_results.invocation_id,
        row_number() over (partition by run_results.unique_id order by run_results.generated_at desc) as row_number
      from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_run_results run_results
      join ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models models on run_results.unique_id = models.unique_id
    ),

    latest_run_results as (
      select unique_id, invocation_id
      from ordered_run_results
      where row_number = 1
    )

    select unique_id, invocation_id from latest_run_results
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "a3a1d6ec-8939-4c04-ab9c-9b4198c6fe1a", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [38] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_invocations
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [39] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_invocations
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [40] DESCRIBE
describe table ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_invocations
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;

-- [41] SELECT
with ordered_run_results as (
      select
        run_results.invocation_id,
        row_number() over (partition by run_results.unique_id order by run_results.generated_at desc) as row_number
      from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_run_results run_results
      join ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_models models on run_results.unique_id = models.unique_id
    ),

    latest_models_invocations as (
      select distinct invocation_id
      from ordered_run_results
      where row_number = 1
    )

    select
      invocations.invocation_id,
      invocations.command,
      invocations.selected,
      invocations.full_refresh,
      invocations.job_url,
      invocations.job_name,
      invocations.job_id,
      invocations.job_run_id,
      invocations.job_run_url,
      invocations.orchestrator
    from ICECORE_SANDBOX.<ELEMENTARY_SCHEMA>.dbt_invocations invocations
    join latest_models_invocations on invocations.invocation_id = latest_models_invocations.invocation_id
/* --ELEMENTARY-METADATA-- {"command": "run-operation", "elementary_component": "OSS", "invocation_id": "bf6d6a09-e405-4a03-ad11-a6ee44417a78", "package_name": "elementary", "resource_name": "log_macro_results", "resource_type": "macro"} --END-ELEMENTARY-METADATA-- */
/* {"app": "dbt", "dbt_version": "1.12.5", "profile_name": "elementary", "target_name": "sandbox", "connection_name": "macro_log_macro_results"} */;
