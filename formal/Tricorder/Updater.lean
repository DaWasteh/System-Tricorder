/-!
# Formal Crash-Safety Proofs for System Tricorder Update Protocols

Models both transactional update paths in `system_tricorder.py`:
1. **Standalone Windows EXE update** (`UpdateWorker._prepare_standalone_exe_update`,
   `_validate_windows_candidate`, `_build_standalone_exe_update_bat`,
   `_cleanup_previous_exe_backup`, lines 5538–6252 & 7891).
2. **Multi-file standalone source update** (`_install_staged_source_files`, lines 5659–5721).

## Explicit Filesystem & OS Assumptions (Outside Lean's TCB)
The theorems below prove safety of the update *protocols* assuming:
1. Same-volume `rename`/`move` (`Path.replace` / `MoveFileExW(MOVEFILE_REPLACE_EXISTING)` /
   `move /Y` on one NTFS volume) is atomic and never destroys both source and destination.
2. `fsync` flushes newly written candidate bytes to durable storage before rename.
3. No concurrent external process deletes `%EXE%` or `%BACKUP%` during the swap window.
4. Verification predicates (`sizeOk`, `sha256Ok`, `peVersionOk`, `selfTestOk`) accurately
   reflect the integrity of the downloaded artifact at validation time.
-/

namespace Tricorder.Updater

-- ═════════════════════════════════════════════════════════════════════════════
-- PART A: STANDALONE WINDOWS EXE UPDATE STATE MACHINE
-- ═════════════════════════════════════════════════════════════════════════════

inductive Artifact where
  | old
  | unverified
  | verifiedNew
  deriving DecidableEq, Repr

structure ValidationFlags where
  sizeOk : Bool := false
  sha256Ok : Bool := false
  peVersionOk : Bool := false
  selfTestOk : Bool := false
  deriving DecidableEq, Repr

def ValidationFlags.allPassed (f : ValidationFlags) : Bool :=
  f.sizeOk && f.sha256Ok && f.peVersionOk && f.selfTestOk

structure ExeFsState where
  exe : Option Artifact
  backup : Option Artifact
  stagedNew : Option Artifact
  downloaded : Option Artifact
  deriving DecidableEq, Repr

inductive ExePhase where
  | idle
  | downloading
  | validating
  | staged
  | backupCleared
  | backedUp
  | swapped
  | waitingCommit
  | rollingBack
  | committed
  | failed
  | crashed
  deriving DecidableEq, Repr

structure ExeUpdateState where
  phase : ExePhase
  fs : ExeFsState
  flags : ValidationFlags
  deriving DecidableEq, Repr

def ExeUpdateState.init : ExeUpdateState :=
  { phase := .idle
  , fs := { exe := some .old, backup := none, stagedNew := none, downloaded := none }
  , flags := {} }

inductive ExeEvent where
  | startDownload
  | finishDownload
  | downloadFail
  | validate (sizeOk sha256Ok peVersionOk selfTestOk : Bool)
  | stageCopyOk
  | stageCopyFail
  | guardAndClearOldBackup
  | moveExeToBackupOk
  | moveExeToBackupFail
  | moveNewToExeOk
  | moveNewToExeFail
  | launchNewOk
  | launchNewFail
  | rollbackRestoreOk
  | rollbackRestoreFail
  | commitCleanupBackup
  | crash
  deriving DecidableEq, Repr

/-- Single-step transition function for the standalone Windows EXE update protocol.
    Matches the guarded sequence in `_build_standalone_exe_update_bat` +
    `UpdateWorker._prepare_standalone_exe_update` + `_cleanup_previous_exe_backup`. -/
def exeStep (s : ExeUpdateState) (ev : ExeEvent) : Option ExeUpdateState :=
  match ev with
  | .crash =>
    -- Power loss or abrupt termination can happen from ANY phase without touching
    -- already-durable files on disk.
    some { s with phase := .crashed }
  | .startDownload =>
    if s.phase == .idle then
      some { s with phase := .downloading }
    else none
  | .finishDownload =>
    if s.phase == .downloading then
      some { s with
        phase := .validating
        fs := { s.fs with downloaded := some .unverified } }
    else none
  | .downloadFail =>
    if s.phase == .downloading then
      some { s with
        phase := .failed
        fs := { s.fs with downloaded := none } }
    else none
  | .validate sizeOk sha256Ok peVersionOk selfTestOk =>
    if s.phase == .validating then
      let flags' : ValidationFlags :=
        { sizeOk := sizeOk, sha256Ok := sha256Ok,
          peVersionOk := peVersionOk, selfTestOk := selfTestOk }
      if flags'.allPassed then
        some { s with
          phase := .staged
          flags := flags'
          fs := { s.fs with downloaded := some .verifiedNew } }
      else
        some { s with
          phase := .failed
          flags := flags'
          fs := { s.fs with downloaded := none } }
    else none
  | .stageCopyOk =>
    if s.phase == .staged then
      some { s with
        phase := .staged
        fs := { s.fs with stagedNew := s.fs.downloaded } }
    else none
  | .stageCopyFail =>
    if s.phase == .staged then
      some { s with
        phase := .failed
        fs := { s.fs with stagedNew := none, downloaded := none } }
    else none
  | .guardAndClearOldBackup =>
    -- Phase 0 guard: `if not exist "%EXE%" goto cleanup` before `del "%BACKUP%"`.
    if s.phase == .staged && s.fs.stagedNew == some .verifiedNew then
      match s.fs.exe with
      | none =>
        some { s with
          phase := .failed
          fs := { s.fs with stagedNew := none, downloaded := none } }
      | some _ =>
        some { s with
          phase := .backupCleared
          fs := { s.fs with backup := none } }
    else none
  | .moveExeToBackupOk =>
    if s.phase == .backupCleared then
      some { s with
        phase := .backedUp
        fs := { s.fs with exe := none, backup := s.fs.exe } }
    else none
  | .moveExeToBackupFail =>
    if s.phase == .backupCleared then
      some { s with
        phase := .failed
        fs := { s.fs with stagedNew := none, downloaded := none } }
    else none
  | .moveNewToExeOk =>
    if s.phase == .backedUp then
      some { s with
        phase := .swapped
        fs := { s.fs with exe := s.fs.stagedNew, stagedNew := none } }
    else none
  | .moveNewToExeFail =>
    if s.phase == .backedUp then
      some { s with phase := .rollingBack }
    else none
  | .launchNewOk =>
    if s.phase == .swapped then
      -- Helper exits after cleaning temp download; backup stays until new EXE survives 5 s.
      some { s with
        phase := .waitingCommit
        fs := { s.fs with stagedNew := none, downloaded := none } }
    else none
  | .launchNewFail =>
    if s.phase == .swapped then
      -- Rollback starts: backup is still intact while exe is replaced back.
      some { s with phase := .rollingBack }
    else none
  | .rollbackRestoreOk =>
    if s.phase == .rollingBack then
      some { s with
        phase := .failed
        fs := { s.fs with exe := s.fs.backup, backup := none, stagedNew := none, downloaded := none } }
    else none
  | .rollbackRestoreFail =>
    if s.phase == .rollingBack then
      -- Even if restoring backup fails (e.g. AV lock), backup remains on disk!
      some { s with
        phase := .failed
        fs := { s.fs with stagedNew := none, downloaded := none } }
    else none
  | .commitCleanupBackup =>
    -- Only executed after the new EXE survived 5 seconds (`_cleanup_previous_exe_backup`).
    if s.phase == .waitingCommit then
      some { s with
        phase := .committed
        fs := { s.fs with backup := none } }
    else none

/-- Execute a finite sequence of update events starting from `s`. -/
def exeRunTrace (s : ExeUpdateState) : List ExeEvent → ExeUpdateState
  | [] => s
  | ev :: rest =>
    match exeStep s ev with
    | none => exeRunTrace s rest
    | some s' => exeRunTrace s' rest

/-- Inductive reachability relation for the standalone EXE update state machine. -/
inductive ExeReachable : ExeUpdateState → Prop where
  | init : ExeReachable ExeUpdateState.init
  | step {s s' : ExeUpdateState} {ev : ExeEvent} :
      ExeReachable s → exeStep s ev = some s' → ExeReachable s'

/-- Primary safety invariant: at least one valid, verified executable version is present
    either in `exe` or in `backup` (never loses both versions). -/
def ExeRecoverable (s : ExeUpdateState) : Prop :=
  s.fs.exe = some .old ∨ s.fs.exe = some .verifiedNew ∨ s.fs.backup = some .old

/-- Inductive strengthening invariant for the EXE update protocol. -/
private def ExeStrongInv (s : ExeUpdateState) : Prop :=
  ExeRecoverable s ∧
  (s.fs.exe = some .verifiedNew → s.flags.allPassed = true) ∧
  (s.fs.stagedNew = some .verifiedNew → s.flags.allPassed = true) ∧
  (s.fs.downloaded = some .verifiedNew → s.flags.allPassed = true) ∧
  (s.fs.exe = none ∨ s.fs.exe = some .old ∨ s.fs.exe = some .verifiedNew) ∧
  (s.fs.backup = none ∨ s.fs.backup = some .old) ∧
  (s.fs.stagedNew = none ∨ s.fs.stagedNew = some .verifiedNew) ∧
  (s.phase = .idle → s.fs.exe = some .old ∧ s.fs.backup = none ∧ s.fs.stagedNew = none) ∧
  (s.phase = .downloading → s.fs.exe = some .old ∧ s.fs.stagedNew = none) ∧
  (s.phase = .validating → s.fs.exe = some .old ∧ s.fs.stagedNew = none) ∧
  (s.phase = .staged → s.fs.exe = some .old ∧ s.fs.downloaded = some .verifiedNew ∧ s.flags.allPassed = true) ∧
  (s.phase = .backupCleared → s.fs.exe = some .old ∧ s.fs.stagedNew = some .verifiedNew ∧ s.flags.allPassed = true) ∧
  (s.phase = .backedUp → s.fs.backup = some .old ∧ s.fs.stagedNew = some .verifiedNew ∧ s.flags.allPassed = true) ∧
  (s.phase = .swapped → s.fs.exe = some .verifiedNew ∧ s.fs.backup = some .old ∧ s.flags.allPassed = true) ∧
  (s.phase = .waitingCommit → s.fs.exe = some .verifiedNew ∧ s.fs.backup = some .old ∧ s.flags.allPassed = true) ∧
  (s.phase = .rollingBack → s.fs.backup = some .old) ∧
  (s.phase = .committed → s.fs.exe = some .verifiedNew ∧ s.fs.backup = none ∧ s.flags.allPassed = true)

private theorem exeStrongInv_init : ExeStrongInv ExeUpdateState.init := by
  simp [ExeStrongInv, ExeRecoverable, ExeUpdateState.init]

private theorem exeStrongInv_step {s s' : ExeUpdateState} {ev : ExeEvent}
    (hinv : ExeStrongInv s) (hstep : exeStep s ev = some s') :
    ExeStrongInv s' := by
  rcases hinv with ⟨hrec, hexe_new, hstg_new, hdl_new, hexe_dom, hbak_dom, hstg_dom,
    h_idle, h_dl, h_val, h_stg, h_bc, h_bu, h_sw, h_wc, h_rb, h_cm⟩
  cases ev with
  | crash =>
    simp only [exeStep, Option.some.injEq] at hstep; subst hstep
    refine ⟨hrec, hexe_new, hstg_new, hdl_new, hexe_dom, hbak_dom, hstg_dom, ?_⟩
    simp
  | startDownload =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .idle := by simpa using hp
      simp only [Option.some.injEq] at hstep; subst hstep
      have ⟨he, _, hsn⟩ := h_idle hpeq
      refine ⟨hrec, hexe_new, hstg_new, hdl_new, hexe_dom, hbak_dom, hstg_dom, ?_⟩
      simp [he, hsn]
    · simp at hstep
  | finishDownload =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .downloading := by simpa using hp
      simp only [Option.some.injEq] at hstep; subst hstep
      have ⟨he, hsn⟩ := h_dl hpeq
      refine ⟨hrec, hexe_new, hstg_new, by simp, hexe_dom, hbak_dom, hstg_dom, ?_⟩
      simp [he, hsn]
    · simp at hstep
  | downloadFail =>
    simp only [exeStep] at hstep
    split at hstep
    · simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, hstg_new, by simp, hexe_dom, hbak_dom, hstg_dom, ?_⟩
      simp
    · simp at hstep
  | validate sz sh pe st =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .validating := by simpa using hp
      have ⟨he, hsn⟩ := h_val hpeq
      split at hstep
      · rename_i hall
        simp only [Option.some.injEq] at hstep; subst hstep
        refine ⟨hrec, fun _ => hall, fun _ => hall, fun _ => hall,
          hexe_dom, hbak_dom, hstg_dom, ?_⟩
        simp [he, hall]
      · simp only [Option.some.injEq] at hstep; subst hstep
        have he_not_new : s.fs.exe ≠ some .verifiedNew := by rw [he]; decide
        refine ⟨hrec, fun h => absurd h he_not_new, by simp [hsn], by simp,
          hexe_dom, hbak_dom, hstg_dom, ?_⟩
        simp
    · simp at hstep
  | stageCopyOk =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .staged := by simpa using hp
      have ⟨he, hdl, hall⟩ := h_stg hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, fun _ => hall, hdl_new, hexe_dom, hbak_dom,
        Or.inr hdl, ?_⟩
      simp [he, hdl, hall]
    · simp at hstep
  | stageCopyFail =>
    simp only [exeStep] at hstep
    split at hstep
    · simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, by simp, by simp, hexe_dom, hbak_dom, Or.inl rfl, ?_⟩
      simp
    · simp at hstep
  | guardAndClearOldBackup =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hcond
      simp only [Bool.and_eq_true, beq_iff_eq] at hcond
      have ⟨he, _, hall⟩ := h_stg hcond.1
      cases hexe_c : s.fs.exe with
      | none =>
        rw [hexe_c] at he; simp at he
      | some a =>
        rw [hexe_c] at he
        simp only [Option.some.injEq] at he
        subst he
        simp only [hexe_c, Option.some.injEq] at hstep; subst hstep
        refine ⟨Or.inl rfl, by simp, hstg_new, hdl_new, Or.inr (Or.inl rfl), Or.inl rfl, hstg_dom, ?_⟩
        simp [hcond.2, hall]
    · simp at hstep
  | moveExeToBackupOk =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .backupCleared := by simpa using hp
      have ⟨he, hsn, hall⟩ := h_bc hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inr (Or.inr he), by simp, hstg_new, hdl_new,
        Or.inl rfl, Or.inr he, hstg_dom, ?_⟩
      simp [he, hsn, hall]
    · simp at hstep
  | moveExeToBackupFail =>
    simp only [exeStep] at hstep
    split at hstep
    · simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, by simp, by simp, hexe_dom, hbak_dom, Or.inl rfl, ?_⟩
      simp
    · simp at hstep
  | moveNewToExeOk =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .backedUp := by simpa using hp
      have ⟨hb, hsn, hall⟩ := h_bu hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inr (Or.inl hsn), fun _ => hall, by simp, hdl_new,
        Or.inr (Or.inr hsn), hbak_dom, Or.inl rfl, ?_⟩
      simp [hb, hsn, hall]
    · simp at hstep
  | moveNewToExeFail =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .backedUp := by simpa using hp
      have ⟨hb, _, _⟩ := h_bu hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, hstg_new, hdl_new, hexe_dom, hbak_dom, hstg_dom, ?_⟩
      simp [hb]
    · simp at hstep
  | launchNewOk =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .swapped := by simpa using hp
      have ⟨he, hb, hall⟩ := h_sw hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inr (Or.inl he), hexe_new, by simp, by simp,
        hexe_dom, hbak_dom, Or.inl rfl, ?_⟩
      simp [he, hb, hall]
    · simp at hstep
  | launchNewFail =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .swapped := by simpa using hp
      have ⟨_, hb, _⟩ := h_sw hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨hrec, hexe_new, hstg_new, hdl_new, hexe_dom, hbak_dom, hstg_dom, ?_⟩
      simp [hb]
    · simp at hstep
  | rollbackRestoreOk =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .rollingBack := by simpa using hp
      have hb := h_rb hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inl hb, by simp [hb], by simp, by simp,
        Or.inr (Or.inl hb), Or.inl rfl, Or.inl rfl, ?_⟩
      simp
    · simp at hstep
  | rollbackRestoreFail =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .rollingBack := by simpa using hp
      have hb := h_rb hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inr (Or.inr hb), hexe_new, by simp, by simp,
        hexe_dom, hbak_dom, Or.inl rfl, ?_⟩
      simp
    · simp at hstep
  | commitCleanupBackup =>
    simp only [exeStep] at hstep
    split at hstep
    · rename_i hp
      have hpeq : s.phase = .waitingCommit := by simpa using hp
      have ⟨he, _, hall⟩ := h_wc hpeq
      simp only [Option.some.injEq] at hstep; subst hstep
      refine ⟨Or.inr (Or.inl he), hexe_new, hstg_new, hdl_new,
        hexe_dom, Or.inl rfl, hstg_dom, ?_⟩
      simp [he, hall]
    · simp at hstep

theorem exeStrongInv_reachable {s : ExeUpdateState} (h : ExeReachable s) :
    ExeStrongInv s := by
  induction h with
  | init => exact exeStrongInv_init
  | step _ hstep ih => exact exeStrongInv_step ih hstep

/-- Theorem A1 (Main Crash-Safety Invariant): In every reachable state — including after
    a failure or crash at ANY step — at least one valid executable version survives. -/
theorem exe_never_loses_both_versions {s : ExeUpdateState} (h : ExeReachable s) :
    ExeRecoverable s :=
  (exeStrongInv_reachable h).1

/-- Theorem A2: No unvalidated release binary can ever be installed into `exe`. -/
theorem exe_new_requires_all_validations {s : ExeUpdateState} (h : ExeReachable s)
    (hnew : s.fs.exe = some .verifiedNew) :
    s.flags.allPassed = true :=
  (exeStrongInv_reachable h).2.1 hnew

/-- Theorem A3: The backup is only removed in the committed state after `exe` holds the
    verified new release. -/
theorem exe_commit_requires_verified_new {s : ExeUpdateState} (h : ExeReachable s)
    (hcm : s.phase = .committed) :
    s.fs.exe = some .verifiedNew ∧ s.fs.backup = none ∧ s.flags.allPassed = true :=
  (exeStrongInv_reachable h).2.2.2.2.2.2.2.2.2.2.2.2.2.2.2.2 hcm

/-- Theorem A4: A completed rollback always restores the previous executable (`some .old`). -/
theorem exe_rollback_restores_old {s s' : ExeUpdateState}
    (h : ExeReachable s) (hstep : exeStep s .rollbackRestoreOk = some s') :
    s'.fs.exe = some .old := by
  have hinv := exeStrongInv_reachable h
  simp only [exeStep] at hstep
  split at hstep
  · rename_i hp
    have hpeq : s.phase = .rollingBack := by simpa using hp
    have hb := hinv.2.2.2.2.2.2.2.2.2.2.2.2.2.2.2.1 hpeq
    simp only [Option.some.injEq] at hstep; subst hstep
    exact hb
  · simp at hstep

/-- Theorem A5: Executing any arbitrary event trace preserves `ExeReachable` and thus
    `ExeRecoverable`. -/
theorem exeRunTrace_reachable (s : ExeUpdateState) (evs : List ExeEvent)
    (h : ExeReachable s) :
    ExeReachable (exeRunTrace s evs) := by
  induction evs generalizing s with
  | nil => exact h
  | cons ev rest ih =>
    simp only [exeRunTrace]
    cases hs : exeStep s ev with
    | none => exact ih s h
    | some s' => exact ih s' (ExeReachable.step h hs)

theorem exeRunTrace_recoverable (evs : List ExeEvent) :
    ExeRecoverable (exeRunTrace ExeUpdateState.init evs) :=
  exe_never_loses_both_versions (exeRunTrace_reachable ExeUpdateState.init evs ExeReachable.init)

-- ═════════════════════════════════════════════════════════════════════════════
-- PART B: MULTI-FILE SOURCE UPDATE TRANSACTION (`_install_staged_source_files`)
-- ═════════════════════════════════════════════════════════════════════════════

inductive FileVer where
  | old
  | new
  deriving DecidableEq, Repr

structure FileSlot where
  existedOriginally : Bool
  target : Option FileVer
  backup : Option FileVer
  deriving DecidableEq, Repr

def FileSlot.initial (existed : Bool) : FileSlot :=
  { existedOriginally := existed
  , target := if existed then some .old else none
  , backup := none }

def FileSlot.originalTarget (slot : FileSlot) : Option FileVer :=
  if slot.existedOriginally then some .old else none

/-- Install one file slot: move existing `target` to `backup`, then place `new` in `target`. -/
def installSlot (slot : FileSlot) : FileSlot :=
  { slot with
    backup := if slot.existedOriginally then slot.target else none
    target := some .new }

/-- Roll back one file slot (matches Phase 0 fix: direct `backup.replace(target)`). -/
def rollbackSlot (slot : FileSlot) : FileSlot :=
  match slot.backup with
  | some ver => { slot with target := some ver, backup := none }
  | none =>
    if !slot.existedOriginally then { slot with target := none }
    else slot

/-- Remove backup of one committed slot without touching `target`. -/
def cleanupSlotBackup (slot : FileSlot) : FileSlot :=
  { slot with backup := none }

/-- Roll back every slot in `installedRev`, skipping indices in `failRollbackAt`
    (simulating per-file `OSError` that logs and continues rather than aborting). -/
def rollbackSlots (failRollbackAt : List Nat) : List (Nat × FileSlot) → List FileSlot
  | [] => []
  | (idx, slot) :: rest =>
    let slot' := if failRollbackAt.contains idx then slot else rollbackSlot slot
    slot' :: rollbackSlots failRollbackAt rest

/-- Clean up backups after commit, skipping indices in `failCleanupAt`. -/
def cleanupSlots (failCleanupAt : List Nat) (idx : Nat) : List FileSlot → List FileSlot
  | [] => []
  | slot :: rest =>
    let slot' := if failCleanupAt.contains idx then slot else cleanupSlotBackup slot
    slot' :: cleanupSlots failCleanupAt (idx + 1) rest

inductive SourceOutcome where
  | committed
  | rolledBack
  deriving DecidableEq, Repr

structure SourceTxResult where
  outcome : SourceOutcome
  slots : List FileSlot
  deriving DecidableEq, Repr

/-- Forward install loop over slots starting at `idx`. If `failInstallAt = some idx`,
    triggers reverse rollback of already-installed prefix and leaves suffix untouched. -/
def sourceInstallLoop
    (failInstallAt : Option Nat) (failRollbackAt failCleanupAt : List Nat)
    (idx : Nat) (installedRev : List (Nat × FileSlot)) :
    List FileSlot → SourceTxResult
  | [] =>
    let committedSlots := (installedRev.reverse).map Prod.snd
    { outcome := .committed
    , slots := cleanupSlots failCleanupAt 0 committedSlots }
  | slot :: rest =>
    if failInstallAt == some idx then
      -- Backup may have been created before candidate replace failed; model worst case
      -- where current slot has backup moved if existed, or target unchanged:
      let currentInstalled :=
        if slot.existedOriginally then
          { slot with backup := slot.target, target := slot.target }
        else slot
      let rolledRev := rollbackSlots failRollbackAt ((idx, currentInstalled) :: installedRev)
      { outcome := .rolledBack
      , slots := rolledRev.reverse ++ rest }
    else
      let slot' := installSlot slot
      sourceInstallLoop failInstallAt failRollbackAt failCleanupAt (idx + 1)
        ((idx, slot') :: installedRev) rest

def runSourceInstall
    (existedFlags : List Bool)
    (failInstallAt : Option Nat)
    (failRollbackAt failCleanupAt : List Nat) : SourceTxResult :=
  let initSlots := existedFlags.map FileSlot.initial
  sourceInstallLoop failInstallAt failRollbackAt failCleanupAt 0 [] initSlots

-- ── Proofs for Multi-File Source Update ───────────────────────────────────────

private theorem cleanupSlots_preserves_target
    (failCleanupAt : List Nat) (idx : Nat) (slots : List FileSlot) :
    (cleanupSlots failCleanupAt idx slots).map FileSlot.target = slots.map FileSlot.target := by
  induction slots generalizing idx with
  | nil => rfl
  | cons s ss ih =>
    simp only [cleanupSlots, List.map_cons]
    have ht : (if failCleanupAt.contains idx then s else cleanupSlotBackup s).target = s.target := by
      split <;> rfl
    rw [ht, ih (idx + 1)]

/-- Theorem B1 (Backup Cleanup Isolation): Backup cleanup failures after commit NEVER
    alter any target file. -/
theorem source_cleanup_preserves_targets
    (failCleanupAt : List Nat) (slots : List FileSlot) :
    (cleanupSlots failCleanupAt 0 slots).map FileSlot.target = slots.map FileSlot.target :=
  cleanupSlots_preserves_target failCleanupAt 0 slots

private theorem sourceInstallLoop_none_all_new
    (failRollbackAt failCleanupAt : List Nat) (idx : Nat)
    (installedRev : List (Nat × FileSlot)) (rem : List FileSlot)
    (hprev : ∀ p ∈ installedRev, p.2.target = some .new) :
    let res := sourceInstallLoop none failRollbackAt failCleanupAt idx installedRev rem
    res.outcome = .committed ∧ ∀ s ∈ res.slots, s.target = some .new := by
  induction rem generalizing idx installedRev with
  | nil =>
    dsimp only [sourceInstallLoop]
    refine ⟨rfl, ?_⟩
    intro s hs
    have hmem : s.target ∈ (cleanupSlots failCleanupAt 0 (installedRev.reverse.map Prod.snd)).map FileSlot.target :=
      List.mem_map.mpr ⟨s, hs, rfl⟩
    rw [cleanupSlots_preserves_target] at hmem
    rcases List.mem_map.mp hmem with ⟨s0, hs0, heq⟩
    rcases List.mem_map.mp hs0 with ⟨p, hp, rfl⟩
    rw [List.mem_reverse] at hp
    rw [← heq]
    exact hprev p hp
  | cons s rest ih =>
    dsimp only [sourceInstallLoop]
    have hnone : (none == some idx) = false := rfl
    simp only [hnone, Bool.false_eq_true, ↓reduceIte]
    apply ih (idx + 1) ((idx, installSlot s) :: installedRev)
    intro p hp
    rw [List.mem_cons] at hp
    rcases hp with rfl | hp'
    · rfl
    · exact hprev p hp'

/-- Theorem B2 (All-or-Nothing Commit): When installation succeeds without error, EVERY
    target file contains the new release, regardless of backup cleanup failures. -/
theorem source_install_success_all_new
    (existedFlags : List Bool) (failRollbackAt failCleanupAt : List Nat) :
    let res := runSourceInstall existedFlags none failRollbackAt failCleanupAt
    res.outcome = .committed ∧ ∀ s ∈ res.slots, s.target = some .new :=
  sourceInstallLoop_none_all_new failRollbackAt failCleanupAt 0 []
    (existedFlags.map FileSlot.initial) (by simp)

private theorem rollbackSlot_installSlot_initial (existed : Bool) :
    rollbackSlot (installSlot (FileSlot.initial existed)) = FileSlot.initial existed := by
  cases existed <;> rfl

private theorem rollbackSlot_existedOriginally (s : FileSlot) :
    (rollbackSlot s).existedOriginally = s.existedOriginally := by
  cases s with
  | mk e t b => cases b <;> cases e <;> rfl

private theorem rollbackSlots_clean_restores
    (installedRev : List (Nat × FileSlot))
    (hvalid : ∀ p ∈ installedRev, rollbackSlot p.2 = FileSlot.initial p.2.existedOriginally) :
    ∀ s ∈ rollbackSlots [] installedRev, s = FileSlot.initial s.existedOriginally := by
  induction installedRev with
  | nil => simp [rollbackSlots]
  | cons p rest ih =>
    rcases p with ⟨idx, slot⟩
    simp only [rollbackSlots, List.contains_nil, Bool.false_eq_true, ↓reduceIte]
    intro s hs
    rw [List.mem_cons] at hs
    rcases hs with rfl | hs'
    · have hrb := hvalid (idx, slot) List.mem_cons_self
      rw [rollbackSlot_existedOriginally]
      exact hrb
    · exact ih (fun q hq => hvalid q (List.mem_cons_of_mem _ hq)) s hs'

/-- Every slot in a resilient rollback either has its exact original state, or if its own
    rollback raised `OSError`, still retains its `.old` backup (for originally existing files). -/
def SlotSafeAfterRollback (s : FileSlot) : Prop :=
  s = FileSlot.initial s.existedOriginally ∨
  (s.existedOriginally = true ∧ s.backup = some .old) ∨
  (s.existedOriginally = false)

private theorem rollbackSlots_all_safe
    (failRollbackAt : List Nat) (installedRev : List (Nat × FileSlot))
    (hvalid : ∀ p ∈ installedRev,
      rollbackSlot p.2 = FileSlot.initial p.2.existedOriginally ∧
      (p.2.existedOriginally = true → p.2.backup = some .old)) :
    ∀ s ∈ rollbackSlots failRollbackAt installedRev, SlotSafeAfterRollback s := by
  induction installedRev with
  | nil => simp [rollbackSlots]
  | cons p rest ih =>
    rcases p with ⟨idx, slot⟩
    simp only [rollbackSlots]
    intro s hs
    rw [List.mem_cons] at hs
    rcases hs with rfl | hs'
    · have ⟨hrb, hbak⟩ := hvalid (idx, slot) List.mem_cons_self
      split
      · cases hex : slot.existedOriginally with
        | true => exact Or.inr (Or.inl ⟨hex, hbak hex⟩)
        | false => exact Or.inr (Or.inr hex)
      · exact Or.inl (by rw [rollbackSlot_existedOriginally]; exact hrb)
    · exact ih (fun q hq => hvalid q (List.mem_cons_of_mem _ hq)) s hs'

private theorem sourceInstallLoop_in_bounds
    (failAt : Nat) (failRollbackAt failCleanupAt : List Nat) (idx : Nat)
    (installedRev : List (Nat × FileSlot)) (rem : List FileSlot)
    (hbound : idx ≤ failAt ∧ failAt < idx + rem.length)
    (hprev : ∀ p ∈ installedRev,
      rollbackSlot p.2 = FileSlot.initial p.2.existedOriginally ∧
      (p.2.existedOriginally = true → p.2.backup = some .old))
    (hrem : ∀ s ∈ rem, s = FileSlot.initial s.existedOriginally) :
    let res := sourceInstallLoop (some failAt) failRollbackAt failCleanupAt idx installedRev rem
    res.outcome = .rolledBack ∧
    (failRollbackAt = [] → ∀ s ∈ res.slots, s = FileSlot.initial s.existedOriginally) ∧
    (∀ s ∈ res.slots, SlotSafeAfterRollback s) := by
  induction rem generalizing idx installedRev with
  | nil =>
    simp at hbound; omega
  | cons s rest ih =>
    dsimp only [sourceInstallLoop]
    by_cases hhit : failAt = idx
    · subst hhit
      simp only [beq_self_eq_true, ↓reduceIte]
      have hs_init := hrem s List.mem_cons_self
      have hcur_valid :
          ∀ p ∈ ((failAt,
            if s.existedOriginally = true then { s with backup := s.target, target := s.target }
            else s) :: installedRev),
          rollbackSlot p.2 = FileSlot.initial p.2.existedOriginally ∧
          (p.2.existedOriginally = true → p.2.backup = some .old) := by
        intro p hp
        rw [List.mem_cons] at hp
        rcases hp with rfl | hp'
        · rw [hs_init]
          cases s.existedOriginally <;> simp [FileSlot.initial, rollbackSlot]
        · exact hprev p hp'
      refine ⟨trivial, ?_, ?_⟩
      · intro hnil t ht
        subst hnil
        rw [List.mem_append, List.mem_reverse] at ht
        rcases ht with hrb | hrest
        · exact rollbackSlots_clean_restores _ (fun p hp => (hcur_valid p hp).1) t hrb
        · exact hrem t (List.mem_cons_of_mem s hrest)
      · intro t ht
        rw [List.mem_append, List.mem_reverse] at ht
        rcases ht with hrb | hrest
        · exact rollbackSlots_all_safe failRollbackAt _ hcur_valid t hrb
        · exact Or.inl (hrem t (List.mem_cons_of_mem s hrest))
    · have hbeq : (some failAt == some idx) = false := by
        simp [beq_false_of_ne hhit]
      simp only [hbeq, Bool.false_eq_true, ↓reduceIte]
      have hs_init := hrem s List.mem_cons_self
      have hbound' : idx + 1 ≤ failAt ∧ failAt < (idx + 1) + rest.length := by
        simp at hbound; omega
      apply ih (idx + 1) ((idx, installSlot s) :: installedRev) hbound'
      · intro p hp
        rw [List.mem_cons] at hp
        rcases hp with rfl | hp'
        · rw [hs_init]
          cases s.existedOriginally <;> simp [FileSlot.initial, installSlot, rollbackSlot]
        · exact hprev p hp'
      · intro t ht; exact hrem t (List.mem_cons_of_mem s ht)

/-- Theorem B3 (Clean Multi-File Rollback): If installation fails at any file index `k < N`
    and rollback completes cleanly, EVERY file `0..N-1` is restored to its exact initial state. -/
theorem source_install_clean_rollback_restores_all
    (existedFlags : List Bool) (failAt : Nat) (failCleanupAt : List Nat)
    (hlt : failAt < existedFlags.length) :
    let res := runSourceInstall existedFlags (some failAt) [] failCleanupAt
    res.outcome = .rolledBack ∧
    ∀ s ∈ res.slots, s = FileSlot.initial s.existedOriginally := by
  unfold runSourceInstall
  have h := sourceInstallLoop_in_bounds failAt [] failCleanupAt 0 []
    (existedFlags.map FileSlot.initial)
    ⟨Nat.zero_le _, by simpa using hlt⟩
    (by simp)
    (fun s hs => by
      rcases List.mem_map.mp hs with ⟨b, _, rfl⟩
      rfl)
  exact ⟨h.1, h.2.1 rfl⟩

/-- Theorem B4 (Resilient Rollback Safety): Even if rolling back one or more files fails
    with `OSError`, no originally existing file ever loses its `.old` version (either restored
    in `target` or preserved in `backup`). -/
theorem source_install_resilient_rollback_preserves_old
    (existedFlags : List Bool) (failAt : Nat) (failRollbackAt failCleanupAt : List Nat)
    (hlt : failAt < existedFlags.length) :
    let res := runSourceInstall existedFlags (some failAt) failRollbackAt failCleanupAt
    res.outcome = .rolledBack ∧
    ∀ s ∈ res.slots, SlotSafeAfterRollback s := by
  unfold runSourceInstall
  have h := sourceInstallLoop_in_bounds failAt failRollbackAt failCleanupAt 0 []
    (existedFlags.map FileSlot.initial)
    ⟨Nat.zero_le _, by simpa using hlt⟩
    (by simp)
    (fun s hs => by
      rcases List.mem_map.mp hs with ⟨b, _, rfl⟩
      rfl)
  exact ⟨h.1, h.2.2⟩

end Tricorder.Updater
