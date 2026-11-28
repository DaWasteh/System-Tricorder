import Tricorder

-- Phase 2: Layout & Migration
#print axioms Tricorder.Layout.canonical_flatten_tileRows
#print axioms Tricorder.Layout.tileRows_flattenTileRows
#print axioms Tricorder.Layout.canonical_moveTileToNewRow
#print axioms Tricorder.Layout.mem_visibleTiles_moveTileToNewRow
#print axioms Tricorder.Layout.mergeLayoutTiles_valid_and_partition
#print axioms Tricorder.Layout.mergeLayoutTiles_idempotent
#print axioms Tricorder.Layout.preserveDormantLayout_spec

-- Phase 3: Updater Crash-Safety
#print axioms Tricorder.Updater.exe_never_loses_both_versions
#print axioms Tricorder.Updater.exe_new_requires_all_validations
#print axioms Tricorder.Updater.exe_commit_requires_verified_new
#print axioms Tricorder.Updater.exe_rollback_restores_old
#print axioms Tricorder.Updater.exeRunTrace_recoverable
#print axioms Tricorder.Updater.source_cleanup_preserves_targets
#print axioms Tricorder.Updater.source_install_success_all_new
#print axioms Tricorder.Updater.source_install_clean_rollback_restores_all
#print axioms Tricorder.Updater.source_install_resilient_rollback_preserves_old

-- Phase 4a: Power Aggregation & RAPL Wrap-Around
#print axioms Tricorder.Power.dedupPositiveRows_nodup_names
#print axioms Tricorder.Power.cpuPackagePowerMw_single_tier
#print axioms Tricorder.Power.cpuPackagePowerMw_core_never_added_to_package
#print axioms Tricorder.Power.tierValues_ignore_ignored_row
#print axioms Tricorder.Power.cpuPackagePowerMw_all_nonpositive_none
#print axioms Tricorder.Power.linuxRaplEnergyDelta_in_range
#print axioms Tricorder.Power.linuxRaplEnergyDelta_wrap_exact

-- Phase 4b: Configuration Sanitizer
#print axioms Tricorder.Config.normaliseColorHex_idempotent
#print axioms Tricorder.Config.normaliseMinRowH_bounds
#print axioms Tricorder.Config.sanitizeConfig_preserves_valid
